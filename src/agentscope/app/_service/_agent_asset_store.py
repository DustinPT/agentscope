# -*- coding: utf-8 -*-
"""Managed skill and MCP asset storage for agent-level workspace configuration."""

import asyncio
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import frontmatter
from fastapi import HTTPException, UploadFile, status

from ...mcp import MCPClient
from ..storage import AgentMCPAsset, AgentSkillAsset


@dataclass
class StagedSkillAsset:
    """Temporary extracted skill awaiting commit."""

    name: str
    description: str
    archive_name: str
    temp_dir: str
    content_hash: str


@dataclass
class StagedMCPAsset:
    """Temporary extracted MCP awaiting commit."""

    name: str
    archive_name: str
    temp_dir: str
    content_hash: str
    client: MCPClient


class AgentAssetStore:
    """Manage staged and committed agent skill and MCP assets on local disk."""

    def __init__(self, root_dir: str) -> None:
        self._root_dir = os.path.abspath(root_dir)
        self._staging_dir = os.path.join(self._root_dir, ".staging")

    @property
    def root_dir(self) -> str:
        """Return the absolute root directory."""
        return self._root_dir

    def _agent_dir(self, user_id: str, agent_id: str) -> str:
        return os.path.join(self._root_dir, user_id, agent_id)

    def _agent_mcp_dir(self, user_id: str, agent_id: str) -> str:
        return os.path.join(self._agent_dir(user_id, agent_id), "mcps")

    @staticmethod
    def _assert_safe_zip(members: list[zipfile.ZipInfo]) -> None:
        for member in members:
            path = member.filename
            if not path or path.endswith("/"):
                continue
            normalized = Path(path)
            if normalized.is_absolute() or ".." in normalized.parts:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid ZIP entry path: {path}",
                )

    @staticmethod
    def _find_skill_root(extract_dir: str) -> str:
        candidates: list[str] = []
        for root, _dirs, files in os.walk(extract_dir):
            if "SKILL.md" in files:
                candidates.append(root)
        if not candidates:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Skill ZIP must contain a SKILL.md file.",
            )
        if len(candidates) > 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Skill ZIP must contain exactly one skill root.",
            )
        return candidates[0]

    @staticmethod
    def _find_mcp_root(extract_dir: str) -> str:
        candidates: list[str] = []
        for root, _dirs, files in os.walk(extract_dir):
            if "mcp.json" in files:
                candidates.append(root)
        if not candidates:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MCP ZIP must contain a mcp.json file.",
            )
        if len(candidates) > 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MCP ZIP must contain exactly one MCP root.",
            )
        return candidates[0]

    @staticmethod
    def _read_skill_metadata(skill_dir: str) -> tuple[str, str, str]:
        skill_md_path = os.path.join(skill_dir, "SKILL.md")
        with open(skill_md_path, "r", encoding="utf-8") as f:
            raw = f.read()
        doc = frontmatter.loads(raw)
        name = doc.get("name")
        description = doc.get("description")
        if not name or not description:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="SKILL.md must include both 'name' and 'description'.",
            )
        return str(name), str(description), hashlib.sha256(
            raw.encode("utf-8"),
        ).hexdigest()

    @staticmethod
    def _read_mcp_metadata(mcp_dir: str) -> tuple[str, MCPClient]:
        mcp_json_path = os.path.join(mcp_dir, "mcp.json")
        try:
            with open(mcp_json_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid mcp.json: {exc}",
            ) from exc

        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="mcp.json must be a JSON object.",
            )

        name = payload.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "MCP name must contain only letters, digits, underscores, "
                    "or hyphens."
                ),
            )

        mcp_config = payload.get("mcp_config")
        if not isinstance(mcp_config, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="mcp.json must include an object field 'mcp_config'.",
            )

        type_ = mcp_config.get("type")
        if type_ not in {"stdio_mcp", "http_mcp"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="mcp_config.type must be 'stdio_mcp' or 'http_mcp'.",
            )

        model_payload = {
            "name": name,
            "is_stateful": payload.get("is_stateful"),
            "mcp_config": mcp_config,
            "execution_timeout": payload.get("execution_timeout"),
        }
        try:
            client = MCPClient.model_validate(model_payload)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid mcp.json: {exc}",
            ) from exc

        if type_ == "stdio_mcp" and not client.is_stateful:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="stdio_mcp requires is_stateful=true.",
            )

        return name, client

    async def stage_skill_zip(
        self,
        file: UploadFile,
    ) -> StagedSkillAsset:
        """Validate and extract an uploaded ZIP file into a staging area."""
        if not file.filename or not file.filename.lower().endswith(".zip"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only .zip skill packages are supported.",
            )

        payload = await file.read()
        if not payload:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded skill package is empty.",
            )

        def _stage() -> StagedSkillAsset:
            os.makedirs(self._staging_dir, exist_ok=True)
            staging_root = tempfile.mkdtemp(
                prefix="skill_",
                dir=self._staging_dir,
            )
            try:
                with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                    self._assert_safe_zip(zf.infolist())
                    zf.extractall(staging_root)
                skill_root = self._find_skill_root(staging_root)
                name, description, content_hash = self._read_skill_metadata(
                    skill_root,
                )
                return StagedSkillAsset(
                    name=name,
                    description=description,
                    archive_name=file.filename or f"{name}.zip",
                    temp_dir=skill_root,
                    content_hash=content_hash,
                )
            except zipfile.BadZipFile as exc:
                shutil.rmtree(staging_root, ignore_errors=True)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid ZIP archive: {exc}",
                ) from exc
            except Exception:
                shutil.rmtree(staging_root, ignore_errors=True)
                raise

        return await asyncio.to_thread(_stage)

    async def stage_mcp_zip(
        self,
        file: UploadFile,
    ) -> StagedMCPAsset:
        """Validate and extract an uploaded MCP ZIP file into staging."""
        if not file.filename or not file.filename.lower().endswith(".zip"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only .zip MCP packages are supported.",
            )

        payload = await file.read()
        if not payload:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded MCP package is empty.",
            )

        def _stage() -> StagedMCPAsset:
            os.makedirs(self._staging_dir, exist_ok=True)
            staging_root = tempfile.mkdtemp(
                prefix="mcp_",
                dir=self._staging_dir,
            )
            try:
                with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                    self._assert_safe_zip(zf.infolist())
                    zf.extractall(staging_root)
                mcp_root = self._find_mcp_root(staging_root)
                content_hash = hashlib.sha256(payload).hexdigest()
                name, client = self._read_mcp_metadata(mcp_root)
                return StagedMCPAsset(
                    name=name,
                    archive_name=file.filename or f"{name}.zip",
                    temp_dir=mcp_root,
                    content_hash=content_hash,
                    client=client,
                )
            except zipfile.BadZipFile as exc:
                shutil.rmtree(staging_root, ignore_errors=True)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid ZIP archive: {exc}",
                ) from exc
            except Exception:
                shutil.rmtree(staging_root, ignore_errors=True)
                raise

        return await asyncio.to_thread(_stage)

    async def commit_staged_skills(
        self,
        user_id: str,
        agent_id: str,
        staged: list[StagedSkillAsset],
    ) -> list[AgentSkillAsset]:
        """Move staged skills into the managed agent asset directory."""

        def _commit() -> list[AgentSkillAsset]:
            agent_dir = self._agent_dir(user_id, agent_id)
            os.makedirs(agent_dir, exist_ok=True)
            committed: list[AgentSkillAsset] = []
            for item in staged:
                target_dir = os.path.join(agent_dir, item.name)
                if os.path.exists(target_dir):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Skill '{item.name}' already exists.",
                    )
                shutil.move(item.temp_dir, target_dir)
                committed.append(
                    AgentSkillAsset(
                        name=item.name,
                        description=item.description,
                        archive_name=item.archive_name,
                        dir=target_dir,
                        content_hash=item.content_hash,
                    ),
                )
            return committed

        return await asyncio.to_thread(_commit)

    async def cleanup_staged_skills(
        self,
        staged: list[StagedSkillAsset],
    ) -> None:
        """Remove staging directories for uncommitted skill uploads."""

        def _cleanup() -> None:
            for item in staged:
                shutil.rmtree(
                    os.path.dirname(item.temp_dir),
                    ignore_errors=True,
                )

        await asyncio.to_thread(_cleanup)

    async def commit_staged_mcps(
        self,
        user_id: str,
        agent_id: str,
        staged: list[StagedMCPAsset],
        *,
        replace_names: set[str] | None = None,
    ) -> list[AgentMCPAsset]:
        """Move staged MCPs into the managed agent asset directory."""

        def _commit() -> list[AgentMCPAsset]:
            agent_dir = self._agent_mcp_dir(user_id, agent_id)
            os.makedirs(agent_dir, exist_ok=True)
            committed: list[AgentMCPAsset] = []
            replace = replace_names or set()
            for item in staged:
                target_dir = os.path.join(agent_dir, item.name)
                if os.path.exists(target_dir):
                    if item.name in replace:
                        shutil.rmtree(target_dir, ignore_errors=True)
                    else:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"MCP '{item.name}' already exists.",
                        )
                shutil.move(item.temp_dir, target_dir)
                committed.append(
                    AgentMCPAsset(
                        name=item.name,
                        archive_name=item.archive_name,
                        dir=target_dir,
                        content_hash=item.content_hash,
                        client=item.client,
                    ),
                )
            return committed

        return await asyncio.to_thread(_commit)

    async def cleanup_staged_mcps(
        self,
        staged: list[StagedMCPAsset],
    ) -> None:
        """Remove staging directories for uncommitted MCP uploads."""

        def _cleanup() -> None:
            for item in staged:
                shutil.rmtree(
                    os.path.dirname(item.temp_dir),
                    ignore_errors=True,
                )

        await asyncio.to_thread(_cleanup)

    async def import_skill_dir(
        self,
        user_id: str,
        agent_id: str,
        skill_path: str,
    ) -> AgentSkillAsset:
        """Copy an existing local skill directory into managed storage."""

        def _import() -> AgentSkillAsset:
            source_dir = os.path.abspath(skill_path)
            if not os.path.isdir(source_dir):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Skill path does not exist: {skill_path}",
                )
            name, description, content_hash = self._read_skill_metadata(source_dir)
            target_dir = os.path.join(self._agent_dir(user_id, agent_id), name)
            if os.path.exists(target_dir):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Skill '{name}' already exists.",
                )
            os.makedirs(os.path.dirname(target_dir), exist_ok=True)
            shutil.copytree(source_dir, target_dir)
            return AgentSkillAsset(
                name=name,
                description=description,
                archive_name=f"{name}.zip",
                dir=target_dir,
                content_hash=content_hash,
            )

        return await asyncio.to_thread(_import)

    async def delete_skills(
        self,
        user_id: str,
        agent_id: str,
        skill_names: list[str],
    ) -> None:
        """Delete committed skill directories by name."""

        def _delete() -> None:
            agent_dir = self._agent_dir(user_id, agent_id)
            for skill_name in skill_names:
                shutil.rmtree(
                    os.path.join(agent_dir, skill_name),
                    ignore_errors=True,
                )

        await asyncio.to_thread(_delete)

    async def delete_mcps(
        self,
        user_id: str,
        agent_id: str,
        mcp_names: list[str],
    ) -> None:
        """Delete committed MCP directories by name."""

        def _delete() -> None:
            agent_dir = self._agent_mcp_dir(user_id, agent_id)
            for mcp_name in mcp_names:
                shutil.rmtree(
                    os.path.join(agent_dir, mcp_name),
                    ignore_errors=True,
                )

        await asyncio.to_thread(_delete)

    async def zip_skill(
        self,
        user_id: str,
        agent_id: str,
        skill_name: str,
    ) -> tuple[str, bytes]:
        """Create a ZIP archive from a committed skill directory."""

        def _zip() -> tuple[str, bytes]:
            skill_dir = os.path.join(self._agent_dir(user_id, agent_id), skill_name)
            if not os.path.isdir(skill_dir):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Skill '{skill_name}' not found.",
                )
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for root, _dirs, files in os.walk(skill_dir):
                    for file_name in files:
                        abs_path = os.path.join(root, file_name)
                        rel_path = os.path.relpath(abs_path, skill_dir)
                        zf.write(abs_path, arcname=os.path.join(skill_name, rel_path))
            return f"{skill_name}.zip", buf.getvalue()

        return await asyncio.to_thread(_zip)
