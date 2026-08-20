# -*- coding: utf-8 -*-
"""Workspace router — list runtime MCP clients, skills, and workspace files."""

import asyncio
import mimetypes
import os
import tempfile
import textwrap
import uuid
import zipfile
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .._service._agent_asset_store import AgentAssetStore
from ..deps import (
    get_agent_asset_store,
    get_current_user_id,
    get_storage,
    get_workspace_manager,
)
from ..workspace_manager import WorkspaceManagerBase
from ..storage import AgentRecord, StorageBase
from ...mcp import MCPClient
from ...skill import Skill
from ...tool import DirEntry, LocalBackend
from ...tool._builtin._read import Read
from ...workspace import DockerBackend, E2BBackend, WorkspaceBase

workspace_router = APIRouter(prefix="/workspace", tags=["workspace"])
_DOWNLOAD_CHUNK_SIZE = 1024 * 1024
_REMOTE_ZIP_SCRIPT = textwrap.dedent(
    """
    import os
    import sys
    import zipfile
    from pathlib import Path

    source = Path(sys.argv[1])
    output = Path(sys.argv[2])
    root_name = sys.argv[3]
    allow_missing = sys.argv[4] == "1"

    output.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(
        output,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        strict_timestamps=False,
    ) as zf:
        if not source.exists():
            if not allow_missing:
                raise FileNotFoundError(str(source))
            zf.writestr(root_name.rstrip("/") + "/", b"")
            sys.exit(0)

        if source.is_file():
            zf.write(source, arcname=root_name)
            sys.exit(0)

        zf.writestr(root_name.rstrip("/") + "/", b"")
        for root, dirs, files in os.walk(source):
            root_path = Path(root)
            rel_root = root_path.relative_to(source)
            for dir_name in dirs:
                arcname = (Path(root_name) / rel_root / dir_name).as_posix().rstrip("/") + "/"
                zf.writestr(arcname, b"")
            for file_name in files:
                abs_path = root_path / file_name
                arcname = (Path(root_name) / rel_root / file_name).as_posix()
                zf.write(abs_path, arcname=arcname)
    """,
).strip()


class ToolInfo(BaseModel):
    """The tool info."""

    name: str
    description: str | None = None


class MCPClientStatus(MCPClient):
    """MCPClient enriched with live tool list and health status."""

    connection_status: str = "disconnected"
    connection_error: str | None = None
    connection_error_detail: str | None = None
    is_healthy: bool = False
    tools: list[ToolInfo] = Field(default_factory=list)


class WorkspaceFileEntry(BaseModel):
    """One file or directory inside the current workspace root."""

    name: str
    path: str
    is_dir: bool
    mime_type: str | None = None
    size_bytes: int | None = None
    mtime: float | None = None


async def get_workspace_download_user_id(
    x_user_id: str | None = Header(default=None, alias="X-User-ID"),
    user_id: str | None = Query(default=None),
) -> str:
    """Resolve caller identity for browser-native workspace downloads."""
    resolved = x_user_id or user_id
    if not resolved:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-ID header or user_id query parameter is required.",
        )
    return resolved


async def _resolve_agent_workspace(
    user_id: str,
    agent_id: str,
    session_id: str,
    storage: StorageBase,
    workspace_manager: WorkspaceManagerBase,
    asset_store: AgentAssetStore,
) -> tuple[AgentRecord, WorkspaceBase]:
    """Resolve the current agent record and synchronized workspace."""
    agent_record = await storage.get_agent(user_id, agent_id)
    if agent_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id!r} not found.",
        )
    session_record = await storage.get_session_meta(user_id, session_id)
    if session_record is None or session_record.agent_id != agent_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id!r} not found.",
        )
    resolved_mcp_assets = [
        asset.model_copy(update={"dir": asset_store.resolve_dir(asset.dir)})
        for asset in agent_record.data.mcp_assets
    ]
    resolved_skills = [
        skill.model_copy(update={"dir": asset_store.resolve_dir(skill.dir)})
        for skill in agent_record.data.skills
    ]
    workspace = await workspace_manager.get_workspace(
        user_id,
        agent_id,
        session_id,
        session_record.config.workspace_id,
        agent_mcps=agent_record.data.mcps,
        agent_mcp_assets=resolved_mcp_assets,
        agent_skill_assets=resolved_skills,
    )
    return agent_record, workspace


def _workspace_root_path(workspace: WorkspaceBase) -> str:
    """Return the current workspace root path."""
    return workspace.get_backend().normpath(workspace.workdir)


def _normalize_relative_workspace_path(
    workspace: WorkspaceBase,
    raw_path: str,
) -> str:
    """Normalize a workspace-relative path and reject directory traversal."""
    backend = workspace.get_backend()
    candidate = (raw_path or "").strip().replace("\\", "/")
    if candidate in ("", "."):
        return ""
    if backend.isabs(candidate):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace path must be relative.",
        )
    normalized = backend.normpath(candidate)
    if normalized in ("", "."):
        return ""
    if normalized == ".." or normalized.startswith("../"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace path escapes the workspace root.",
        )
    return normalized


def _resolve_workspace_target_path(
    workspace: WorkspaceBase,
    relative_path: str,
) -> tuple[str, str]:
    """Resolve one workspace-relative path to an absolute backend path."""
    backend = workspace.get_backend()
    workspace_root = _workspace_root_path(workspace)
    normalized = _normalize_relative_workspace_path(workspace, relative_path)
    resolved = workspace_root
    if normalized:
        resolved = backend.normpath(backend.join_path(workspace_root, normalized))
    if resolved != workspace_root and not resolved.startswith(workspace_root.rstrip("/") + "/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace path escapes the workspace root.",
        )
    return workspace_root, resolved


def _workspace_download_filename(
    relative_path: str,
    *,
    is_dir: bool,
    session_id: str,
) -> str:
    """Build the browser-visible filename for one workspace download."""
    if not relative_path:
        return f"workspace-{session_id}.zip"
    name = Path(relative_path).name or f"workspace-{session_id}"
    return f"{name}.zip" if is_dir else name


def _workspace_zip_root_name(relative_path: str, session_id: str) -> str:
    """Build the archive root directory name."""
    if not relative_path:
        return f"workspace-{session_id}"
    return Path(relative_path).name or f"workspace-{session_id}"


def _text_like_media_type(path: str) -> str | None:
    """Return textual media type hints similar to the Read tool."""
    media_type, _ = mimetypes.guess_type(path)
    suffix = Path(path).suffix.lower()
    if media_type is not None and (
        media_type.startswith("image/")
        or media_type.startswith("text/")
        or media_type == "application/xml"
        or media_type == "application/javascript"
        or media_type == "application/x-javascript"
        or media_type == "application/yaml"
        or media_type == "application/x-yaml"
        or media_type == "application/toml"
        or media_type == "application/json"
        or media_type == "application/ld+json"
        or media_type.endswith("+json")
        or media_type.endswith("+xml")
    ):
        return media_type
    if suffix in {".md", ".markdown"}:
        return "text/markdown"
    if suffix in {".json", ".jsonl"}:
        return "application/json"
    return None


async def _read_preview_sample(workspace: WorkspaceBase, path: str) -> bytes:
    """Read a small byte sample for text/binary sniffing."""
    async for chunk in workspace.get_backend().read_stream(path, chunk_size=4096):
        return chunk
    return b""


async def _preview_media_type(
    workspace: WorkspaceBase,
    path: str,
    display_name: str,
) -> str | None:
    """Return previewable media type for one file path, if supported."""
    media_type = _text_like_media_type(display_name)
    if media_type is not None and media_type.startswith("image/"):
        return media_type

    sample = await _read_preview_sample(workspace, path)
    if Read._is_binary_file(sample, media_type):
        return None
    return media_type or "text/plain"


async def _entry_to_response(
    workspace: WorkspaceBase,
    parent_relative_path: str,
    parent_path: str,
    entry: DirEntry,
) -> WorkspaceFileEntry:
    """Convert one backend directory entry to API response shape."""
    entry_path = (
        f"{parent_relative_path}/{entry.name}"
        if parent_relative_path
        else entry.name
    )
    mime_type = None
    if not entry.is_dir:
        mime_type = await _preview_media_type(
            workspace,
            workspace.get_backend().join_path(parent_path, entry.name),
            entry.name,
        )
    return WorkspaceFileEntry(
        name=entry.name,
        path=entry_path,
        is_dir=entry.is_dir,
        mime_type=mime_type,
        size_bytes=entry.size_bytes,
        mtime=entry.mtime,
    )


async def _stream_backend_file(
    workspace: WorkspaceBase,
    path: str,
    cleanup: Callable[[], Awaitable[None]] | None = None,
) -> AsyncIterator[bytes]:
    """Yield file bytes from one backend and clean up afterwards."""
    try:
        async for chunk in workspace.get_backend().read_stream(
            path,
            chunk_size=_DOWNLOAD_CHUNK_SIZE,
        ):
            yield chunk
    finally:
        if cleanup is not None:
            try:
                await cleanup()
            except Exception:
                pass


async def _cleanup_local_path(path: str) -> None:
    """Delete one local temporary file."""
    await asyncio.to_thread(os.remove, path)


async def _cleanup_remote_temp_zip(workspace: WorkspaceBase, path: str) -> None:
    """Delete one remote temporary zip file."""
    await workspace.get_backend().exec_shell(["rm", "-f", path])


async def _build_local_zip(
    source_path: str,
    archive_root: str,
    *,
    allow_missing: bool,
) -> str:
    """Create one local temporary zip file and return its path."""

    def _write_zip() -> str:
        fd, temp_path = tempfile.mkstemp(
            prefix="agentscope-workspace-download-",
            suffix=".zip",
        )
        os.close(fd)
        source = Path(source_path)
        try:
            with zipfile.ZipFile(
                temp_path,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                strict_timestamps=False,
            ) as zf:
                if not source.exists():
                    if not allow_missing:
                        raise FileNotFoundError(source_path)
                    zf.writestr(archive_root.rstrip("/") + "/", b"")
                    return temp_path
                if source.is_file():
                    zf.write(source, arcname=archive_root)
                    return temp_path
                zf.writestr(archive_root.rstrip("/") + "/", b"")
                for root, dirs, files in os.walk(source):
                    root_path = Path(root)
                    rel_root = root_path.relative_to(source)
                    for dir_name in dirs:
                        arcname = (
                            (Path(archive_root) / rel_root / dir_name)
                            .as_posix()
                            .rstrip("/")
                            + "/"
                        )
                        zf.writestr(arcname, b"")
                    for file_name in files:
                        abs_path = root_path / file_name
                        arcname = (Path(archive_root) / rel_root / file_name).as_posix()
                        zf.write(abs_path, arcname=arcname)
        except Exception:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            raise
        return temp_path

    return await asyncio.to_thread(_write_zip)


async def _build_remote_zip(
    workspace: WorkspaceBase,
    source_path: str,
    archive_root: str,
    *,
    allow_missing: bool,
) -> str:
    """Create one zip file inside the remote workspace runtime."""
    temp_path = f"/tmp/agentscope-workspace-download-{uuid.uuid4().hex}.zip"
    result = await workspace.get_backend().exec_shell(
        [
            "python3",
            "-c",
            _REMOTE_ZIP_SCRIPT,
            source_path,
            temp_path,
            archive_root,
            "1" if allow_missing else "0",
        ],
    )
    if not result.ok():
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=stderr or "Failed to create workspace archive.",
        )
    return temp_path


async def _build_project_zip(
    workspace: WorkspaceBase,
    source_path: str,
    archive_root: str,
    *,
    allow_missing: bool,
) -> tuple[str, Callable[[], Awaitable[None]]]:
    """Create a temporary workspace zip and return its path plus cleanup hook."""
    backend = workspace.get_backend()
    if isinstance(backend, LocalBackend):
        zip_path = await _build_local_zip(
            source_path,
            archive_root,
            allow_missing=allow_missing,
        )
        return zip_path, lambda: _cleanup_local_path(zip_path)
    if isinstance(backend, (DockerBackend, E2BBackend)):
        zip_path = await _build_remote_zip(
            workspace,
            source_path,
            archive_root,
            allow_missing=allow_missing,
        )
        return zip_path, lambda: _cleanup_remote_temp_zip(workspace, zip_path)
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"Unsupported backend type: {type(backend).__name__}.",
    )


# ---------------------------------------------------------------------------
# MCP endpoints
# ---------------------------------------------------------------------------


@workspace_router.get("/mcp")
async def list_mcps(
    agent_id: str = Query(...),
    session_id: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    asset_store: AgentAssetStore = Depends(get_agent_asset_store),
) -> list[MCPClientStatus]:
    """Return all MCP clients with live tool list and health status."""
    _, workspace = await _resolve_agent_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
        asset_store,
    )
    clients = await workspace.list_mcps()

    results = [await _build_mcp_status(client) for client in clients]

    return results


async def _build_mcp_status(client: MCPClient) -> MCPClientStatus:
    """Build one MCP status payload from runtime state."""
    base = client.model_dump()
    status = client.connection_status
    error = client.connection_error
    error_detail = client.connection_error_detail
    tools: list[ToolInfo] = []
    is_healthy = False

    if status == "connected":
        try:
            mcp_tools = await client.list_tools()
            tools = [ToolInfo(name=t.name, description=t.description) for t in mcp_tools]
            is_healthy = True
        except Exception as exc:
            status = client.connection_status
            error = client.connection_error or str(exc)
            error_detail = client.connection_error_detail or error

    return MCPClientStatus(
        **base,
        connection_status=status,
        connection_error=error,
        connection_error_detail=error_detail,
        is_healthy=is_healthy,
        tools=tools,
    )


@workspace_router.post("/mcp/{name}/reconnect")
async def reconnect_mcp(
    name: str,
    agent_id: str = Query(...),
    session_id: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    asset_store: AgentAssetStore = Depends(get_agent_asset_store),
) -> MCPClientStatus:
    """Reconnect one MCP in the current workspace and return its status."""
    _, workspace = await _resolve_agent_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
        asset_store,
    )
    try:
        client = await workspace.reconnect_mcp(name)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return await _build_mcp_status(client)


# ---------------------------------------------------------------------------
# Skill endpoints
# ---------------------------------------------------------------------------


@workspace_router.get("/skill")
async def list_skills(
    agent_id: str = Query(...),
    session_id: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    asset_store: AgentAssetStore = Depends(get_agent_asset_store),
) -> list[Skill]:
    """Return all skills available in the session's workspace."""
    _, workspace = await _resolve_agent_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
        asset_store,
    )
    return await workspace.list_skills()


# ---------------------------------------------------------------------------
# Workspace file endpoints
# ---------------------------------------------------------------------------


@workspace_router.get("/files")
async def list_workspace_files(
    agent_id: str = Query(...),
    session_id: str = Query(...),
    path: str = Query(default=""),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    asset_store: AgentAssetStore = Depends(get_agent_asset_store),
) -> list[WorkspaceFileEntry]:
    """Return one directory level from the current workspace root."""
    _, workspace = await _resolve_agent_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
        asset_store,
    )
    relative_path = _normalize_relative_workspace_path(workspace, path)
    workspace_root, target_path = _resolve_workspace_target_path(workspace, relative_path)
    backend = workspace.get_backend()

    if not await backend.is_dir(workspace_root):
        if relative_path:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workspace directory not found.",
            )
        return []
    if relative_path and not await backend.is_dir(target_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace directory not found.",
        )

    try:
        entries = await backend.scandir(target_path)
    except FileNotFoundError:
        if relative_path:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workspace directory not found.",
            ) from None
        return []

    entries.sort(key=lambda item: (not item.is_dir, item.name.lower()))
    return [
        await _entry_to_response(workspace, relative_path, target_path, entry)
        for entry in entries
    ]


@workspace_router.get("/files/download")
async def download_workspace_file(
    agent_id: str = Query(...),
    session_id: str = Query(...),
    path: str = Query(default=""),
    user_id: str = Depends(get_workspace_download_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    asset_store: AgentAssetStore = Depends(get_agent_asset_store),
) -> StreamingResponse:
    """Download one workspace file or one zipped workspace directory."""
    _, workspace = await _resolve_agent_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
        asset_store,
    )
    relative_path = _normalize_relative_workspace_path(workspace, path)
    workspace_root, target_path = _resolve_workspace_target_path(workspace, relative_path)
    backend = workspace.get_backend()
    workspace_root_exists = await backend.is_dir(workspace_root)

    if relative_path:
        target_stat = await backend.stat(target_path)
        if target_stat is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workspace file or directory not found.",
            )
    else:
        target_stat = await backend.stat(workspace_root)

    is_dir = target_stat.is_dir if target_stat is not None else True
    filename = _workspace_download_filename(
        relative_path,
        is_dir=is_dir,
        session_id=session_id,
    )
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
    }

    if not is_dir:
        media_type, _ = mimetypes.guess_type(filename)
        return StreamingResponse(
            _stream_backend_file(workspace, target_path),
            media_type=media_type or "application/octet-stream",
            headers=headers,
        )

    zip_path, cleanup = await _build_project_zip(
        workspace,
        workspace_root if not relative_path else target_path,
        _workspace_zip_root_name(relative_path, session_id),
        allow_missing=not relative_path and not workspace_root_exists,
    )
    return StreamingResponse(
        _stream_backend_file(workspace, zip_path, cleanup=cleanup),
        media_type="application/zip",
        headers=headers,
    )


@workspace_router.get("/files/preview")
async def preview_workspace_file(
    agent_id: str = Query(...),
    session_id: str = Query(...),
    path: str = Query(...),
    user_id: str = Depends(get_workspace_download_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    asset_store: AgentAssetStore = Depends(get_agent_asset_store),
) -> StreamingResponse:
    """Preview one text or image file from the workspace root."""
    _, workspace = await _resolve_agent_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
        asset_store,
    )
    relative_path = _normalize_relative_workspace_path(workspace, path)
    if not relative_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Preview path must point to a file.",
        )

    _, target_path = _resolve_workspace_target_path(workspace, relative_path)
    target_stat = await workspace.get_backend().stat(target_path)
    if target_stat is None or target_stat.is_dir:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace file not found.",
        )

    media_type = await _preview_media_type(
        workspace,
        target_path,
        Path(relative_path).name,
    )
    if media_type is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only text files and images support preview.",
        )

    headers = {
        "Content-Disposition": (
            f"inline; filename*=UTF-8''{quote(Path(relative_path).name or 'preview')}"
        ),
    }
    return StreamingResponse(
        _stream_backend_file(workspace, target_path),
        media_type=media_type,
        headers=headers,
    )
