# -*- coding: utf-8 -*-
"""Builtin tool for creating the current task's project directory."""

import os
from typing import Any, List

from ...message import TextBlock, ToolResultState
from ...permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
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

    def __init__(
        self,
        workdir: str,
        session_id: str,
        middlewares: List[ToolMiddlewareBase] | None = None,
    ) -> None:
        """Initialize the tool with the current workspace root and session."""
        super().__init__(middlewares=middlewares)
        self._project_dir = os.path.join(
            os.path.abspath(workdir),
            "projects",
            session_id,
        )

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

    async def call(self) -> ToolChunk:
        """Create or reuse the fixed project directory."""
        existed = os.path.isdir(self._project_dir)
        os.makedirs(self._project_dir, exist_ok=True)

        action = "already exists" if existed else "created"
        return ToolChunk(
            content=[
                TextBlock(
                    text=(
                        f"Project directory {action}: "
                        f"{self._project_dir}"
                    ),
                ),
            ],
            state=ToolResultState.SUCCESS,
            is_last=True,
        )
