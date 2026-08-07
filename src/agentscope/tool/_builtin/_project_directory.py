# -*- coding: utf-8 -*-
"""Builtin tool for creating the current task's project directory."""

from typing import Any, List

from ...exception import DeveloperOrientedException
from ...message import TextBlock, ToolResultState
from ...permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from ...state import AgentState
from ._backend import BackendBase, LocalBackend
from .._base import ToolBase, ToolMiddlewareBase
from .._response import ToolChunk


class CreateProjectDirectory(ToolBase):
    """Create the fixed project directory for the current session."""

    name: str = "CreateProjectDirectory"
    description: str = (
        "Creates the project directory for the current task.\n\n"
        "Use this tool only when the user did not provide a project "
        "directory and you need to work with files. If the user already "
        "provided a project directory, work in that directory instead and "
        "do not call this tool. After calling it, tell the user which "
        "directory you are using."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
    }
    is_mcp: bool = False
    is_read_only: bool = False
    is_concurrency_safe: bool = True
    is_external_tool: bool = False
    is_state_injected: bool = True

    def __init__(
        self,
        backend: BackendBase | None = None,
        middlewares: List[ToolMiddlewareBase] | None = None,
    ) -> None:
        """Initialize the tool."""
        super().__init__(middlewares=middlewares)
        self._backend = backend or LocalBackend()

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Always allow this constrained directory creation tool."""
        del tool_input, context
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="Project directory creation is allowed.",
        )

    async def call(self, _agent_state: AgentState) -> ToolChunk:
        """Create or reuse the fixed project directory."""
        if not isinstance(_agent_state, AgentState):
            raise DeveloperOrientedException(
                "Error: CreateProjectDirectory requires AgentState to be "
                f"provided, got {_agent_state} instead.",
            )

        runtime_context = _agent_state.tool_context.runtime_context
        if runtime_context is None:
            raise DeveloperOrientedException(
                "Error: CreateProjectDirectory requires "
                "tool_context.runtime_context to be populated before use.",
            )

        project_dir = self._backend.join_path(
            runtime_context.workdir,
            "projects",
            runtime_context.session_id,
        )
        existed = await self._backend.is_dir(project_dir)
        await self._backend.ensure_dir(project_dir)

        action = "already exists" if existed else "created"
        return ToolChunk(
            content=[
                TextBlock(
                    text=(
                        f"Project directory {action}: "
                        f"{project_dir}"
                    ),
                ),
            ],
            state=ToolResultState.SUCCESS,
            is_last=True,
        )
