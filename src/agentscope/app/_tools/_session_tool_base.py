# -*- coding: utf-8 -*-
"""Shared helpers for framework-builtin testing tools."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Any, TYPE_CHECKING

from starlette.datastructures import UploadFile

from ...event import (
    ConfirmResult,
    ExternalExecutionResultEvent,
    UserConfirmResultEvent,
)
from ...message import (
    Msg,
    TextBlock,
    ToolCallBlock,
    ToolCallState,
    ToolResultBlock,
    ToolResultState,
)
from ...permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from ...tool import ToolBase, ToolChunk
from ...tool._builtin._backend import LocalBackend
from .._reply_state import get_current_reply_msg

if TYPE_CHECKING:
    from .._manager import ChatRunRegistry
    from .._service import AgentAssetStore, ChatService
    from ..message_bus import MessageBus
    from ..storage import AgentRecord, SessionRecord, StorageBase, UserRecord
    from ...workspace import WorkspaceBase

_CANCEL_POLL_INTERVAL_SECS = 0.1


class _SessionToolBase(ToolBase):
    """Shared request-scoped helpers for test-management tools."""

    is_concurrency_safe: bool = False
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(
        self,
        *,
        storage: "StorageBase",
        message_bus: "MessageBus",
        chat_service: "ChatService",
        chat_run_registry: "ChatRunRegistry",
        workspace: "WorkspaceBase",
        user_id: str,
        session_id: str,
        agent_id: str,
        workspace_id: str,
        agent_asset_store: "AgentAssetStore | None" = None,
    ) -> None:
        self._storage = storage
        self._message_bus = message_bus
        self._chat_service = chat_service
        self._chat_run_registry = chat_run_registry
        self._workspace = workspace
        self._user_id = user_id
        self._session_id = session_id
        self._agent_id = agent_id
        self._workspace_id = workspace_id
        self._agent_asset_store = agent_asset_store

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Always allow when the tool is attached to the agent."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message=f"{self.name} is allowed when attached to the agent.",
        )

    def _result(
        self,
        payload: dict[str, Any],
        *,
        state: ToolResultState = ToolResultState.SUCCESS,
    ) -> ToolChunk:
        """Build a JSON tool result that is easy for agents to consume."""
        return ToolChunk(
            content=[
                TextBlock(
                    text=json.dumps(
                        payload,
                        ensure_ascii=False,
                        indent=2,
                        default=str,
                    ),
                ),
            ],
            state=state,
            metadata=payload,
        )

    async def _get_owned_agent(
        self,
        agent_id: str,
    ) -> "AgentRecord | None":
        agent = await self._storage.get_agent(self._user_id, agent_id)
        if agent is None or agent.user_id != self._user_id:
            return None
        return agent

    async def _get_owned_session(
        self,
        *,
        agent_id: str,
        session_id: str,
    ) -> "SessionRecord | None":
        session = await self._storage.get_session(
            self._user_id,
            agent_id,
            session_id,
        )
        if session is None or session.user_id != self._user_id:
            return None
        if session.agent_id != agent_id:
            return None
        return session

    async def _get_user_record(self) -> "UserRecord | None":
        return await self._storage.get_user(self._user_id)

    async def _list_all_messages(self, session_id: str) -> list[Msg]:
        """Load all persisted messages in chronological order."""
        all_messages: list[Msg] = []
        offset = 0
        page_size = 200
        while True:
            page = await self._storage.list_messages(
                self._user_id,
                session_id,
                offset=offset,
                limit=page_size,
            )
            if not page:
                break
            all_messages.extend(page)
            if len(page) < page_size:
                break
            offset += len(page)
        return all_messages

    def _serialize_message(self, msg: Msg) -> dict[str, Any]:
        return msg.model_dump(mode="json")

    def _serialize_block(self, block: Any) -> dict[str, Any]:
        return block.model_dump(mode="json")

    def _normalize_workspace_path(self, path: str) -> str:
        """Resolve one path against the current workspace and enforce bounds."""
        backend = self._workspace.get_backend()
        normalized_workdir = backend.normpath(self._workspace.workdir)
        absolute = backend.abspath(path, cwd=normalized_workdir)
        if absolute == normalized_workdir:
            return absolute
        boundary = normalized_workdir.rstrip("/\\")
        if absolute.startswith(boundary + "/") or absolute.startswith(
            boundary + "\\",
        ):
            return absolute
        raise ValueError("Path must stay inside the current workspace.")

    async def _materialize_workspace_path(
        self,
        path: str,
    ) -> tuple[str, tempfile.TemporaryDirectory[str] | None]:
        """Copy one workspace path to local disk when needed."""
        backend = self._workspace.get_backend()
        absolute = self._normalize_workspace_path(path)
        if not await backend.file_exists(absolute):
            raise FileNotFoundError(f"Workspace path not found: {path}")

        if isinstance(backend, LocalBackend):
            return absolute, None

        tmpdir = tempfile.TemporaryDirectory(prefix="agentscope-import-")
        local_root = tmpdir.name
        if await backend.is_dir(absolute):
            target_dir = os.path.join(local_root, Path(absolute).name)
            os.makedirs(target_dir, exist_ok=True)
            for remote_file in await backend.list_dir(absolute, recursive=True):
                relative = os.path.relpath(remote_file, absolute)
                local_path = os.path.join(target_dir, relative)
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                with open(local_path, "wb") as f:
                    f.write(await backend.read_file(remote_file))
            return target_dir, tmpdir

        local_path = os.path.join(local_root, Path(absolute).name)
        with open(local_path, "wb") as f:
            f.write(await backend.read_file(absolute))
        return local_path, tmpdir

    def _zip_local_directory(
        self,
        directory: str,
        *,
        prefix: str,
    ) -> tuple[str, tempfile.TemporaryDirectory[str]]:
        """Create a local ZIP archive for one directory."""
        tmpdir = tempfile.TemporaryDirectory(prefix=prefix)
        zip_path = os.path.join(tmpdir.name, f"{Path(directory).name}.zip")
        with zipfile.ZipFile(
            zip_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            strict_timestamps=False,
        ) as zf:
            for root, dirs, files in os.walk(directory):
                dirs.sort()
                files.sort()
                for file_name in files:
                    abs_path = os.path.join(root, file_name)
                    arcname = os.path.relpath(abs_path, directory)
                    zf.write(abs_path, arcname)
        return zip_path, tmpdir

    def _build_upload_file(self, path: str) -> UploadFile:
        """Wrap one local ZIP file as an UploadFile."""
        return UploadFile(
            filename=os.path.basename(path),
            file=open(path, "rb"),
        )

    async def _spawn_chat_run(
        self,
        *,
        agent_id: str,
        session_id: str,
        input_msg: Any,
        task_name: str,
    ) -> None:
        """Start one chat run after checking the session lock."""
        if await self._message_bus.session_is_running(session_id):
            raise RuntimeError(f"Session '{session_id}' already has a running chat.")
        self._chat_run_registry.spawn(
            self._chat_service.run(
                user_id=self._user_id,
                session_id=session_id,
                agent_id=agent_id,
                input_msg=input_msg,
            ),
            session_id=session_id,
            name=task_name,
        )

    def _get_current_reply(
        self,
        session: "SessionRecord",
    ) -> Msg | None:
        """Return the current persisted assistant reply for one session."""
        agent = type(
            "_SessionAgentView",
            (),
            {
                "name": "",
                "state": session.state,
            },
        )()
        current_reply = get_current_reply_msg(agent)
        if current_reply is not None:
            return current_reply
        if session.state.context:
            last_msg = session.state.context[-1]
            if last_msg.role == "assistant":
                return last_msg
        return None

    def _build_confirm_event(
        self,
        *,
        reply_id: str,
        tool_calls: list[ToolCallBlock],
        confirmed: bool,
    ) -> UserConfirmResultEvent:
        return UserConfirmResultEvent(
            reply_id=reply_id,
            confirm_results=[
                ConfirmResult(
                    confirmed=confirmed,
                    tool_call=tool_call,
                )
                for tool_call in tool_calls
            ],
        )

    def _build_external_results_event(
        self,
        *,
        reply_id: str,
        execution_results: list[ToolResultBlock],
    ) -> ExternalExecutionResultEvent:
        return ExternalExecutionResultEvent(
            reply_id=reply_id,
            execution_results=execution_results,
        )

    def _resolve_session_stop_reason(
        self,
        session: "SessionRecord",
    ) -> tuple[str, str | None, list[dict[str, Any]]]:
        """Return the single allowed next-step category for one session."""
        current_reply = self._get_current_reply(session)
        if current_reply is None:
            return ("reply_completed", None, [])

        tool_calls = current_reply.get_content_blocks("tool_call")
        asking = [
            tool_call
            for tool_call in tool_calls
            if tool_call.state == ToolCallState.ASKING
        ]
        if asking:
            return (
                "require_user_confirm",
                current_reply.id,
                [self._serialize_block(tool_call) for tool_call in asking],
            )

        submitted = [
            tool_call
            for tool_call in tool_calls
            if tool_call.state == ToolCallState.SUBMITTED
        ]
        if submitted:
            return (
                "require_external_execution",
                current_reply.id,
                [self._serialize_block(tool_call) for tool_call in submitted],
            )

        return ("reply_completed", current_reply.id, [])

    async def _interrupt_managed_session(
        self,
        *,
        agent_id: str,
        session_id: str,
        reason: str | None = None,
        timeout: float = 10.0,
    ) -> tuple[bool, "SessionRecord | None"]:
        """Interrupt a managed session via the unified chat-service path."""
        _ = reason
        was_running = await self._message_bus.session_is_running(session_id)
        await self._chat_service.interrupt(
            user_id=self._user_id,
            session_id=session_id,
            agent_id=agent_id,
        )

        released = True
        if was_running:
            deadline = asyncio.get_running_loop().time() + timeout
            while True:
                if not await self._message_bus.session_is_running(session_id):
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    released = False
                    break
                await asyncio.sleep(_CANCEL_POLL_INTERVAL_SECS)

        return released, await self._get_owned_session(
            agent_id=agent_id,
            session_id=session_id,
        )
