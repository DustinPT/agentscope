# -*- coding: utf-8 -*-
"""The glob tool in agentscope."""

from __future__ import annotations

import fnmatch
import json
import os
import sys
from typing import Any, List

from ...message import TextBlock, ToolResultState
from ...permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
    PermissionRule,
)
from .._base import ToolBase
from .._response import ToolChunk
from ._backend import BackendBase, LocalBackend


def _default_glob_helper_path() -> str:
    """Resolve the on-disk path of the bundled helper script."""
    import importlib.resources as _res

    ref = _res.files("agentscope.tool._builtin._scripts").joinpath(
        "_glob_helper.py",
    )
    return str(ref)


class Glob(ToolBase):
    """The glob tool for fast file pattern matching."""

    name: str = "Glob"
    description: str = """Fast file pattern matching tool that works with
any codebase size.

Supports glob patterns like "**/*.js" or "src/**/*.ts" and returns
matching file paths sorted by modification time (newest first).

Use this tool when you need to find files by pattern across the
codebase."""
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The glob pattern to match against "
                "(e.g., '**/*.py', 'src/**/*.ts')",
            },
            "path": {
                "type": "string",
                "description": "The base directory to search from "
                "(defaults to current working directory)",
            },
        },
        "required": ["pattern"],
    }

    is_mcp: bool = False
    is_read_only: bool = True
    is_concurrency_safe: bool = True
    is_external_tool: bool = False
    is_state_injected: bool = False

    def __init__(
        self,
        backend: BackendBase | None = None,
        glob_helper_path: str | None = None,
    ) -> None:
        self._backend = backend or LocalBackend()
        self._is_local = isinstance(self._backend, LocalBackend)
        self._glob_helper_path = (
            glob_helper_path
            if glob_helper_path is not None
            else _default_glob_helper_path()
        )

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.PASSTHROUGH,
            message="Glob pattern matching is read-only.",
        )

    def match_rule(
        self,
        rule_content: str | None,
        tool_input: dict[str, Any],
    ) -> bool:
        if rule_content is None:
            return True

        path = tool_input.get("path", "")
        if path and fnmatch.fnmatch(path, rule_content):
            return True

        pattern = tool_input.get("pattern", "")
        if pattern and fnmatch.fnmatch(pattern, rule_content):
            return True

        return False

    def generate_suggestions(
        self,
        tool_input: dict[str, Any],
    ) -> List[PermissionRule]:
        path = tool_input.get("path", "")
        if not path:
            path = os.getcwd()
        abs_path = os.path.abspath(path)
        pattern = abs_path.rstrip("/\\") + "/**"
        return [
            PermissionRule(
                tool_name=self.name,
                rule_content=pattern,
                behavior=PermissionBehavior.ALLOW,
                source="suggested",
            ),
        ]

    async def call(
        self,
        pattern: str,
        path: str | None = None,
    ) -> ToolChunk:
        base_dir = path if path else await self._backend.getcwd()
        if not await self._backend.is_dir(base_dir):
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"Error: Base directory does not exist: {base_dir}",
                    ),
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        helper_command = [
            sys.executable if self._is_local else "python3",
            self._glob_helper_path,
            base_dir,
            pattern,
        ]
        result = await self._backend.exec_shell(helper_command)
        if not result.ok():
            return ToolChunk(
                content=[
                    TextBlock(
                        text=result.stderr.decode("utf-8", errors="replace")
                        or "Glob helper failed.",
                    ),
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        try:
            payload = json.loads(result.stdout.decode("utf-8"))
        except Exception as exc:
            return ToolChunk(
                content=[TextBlock(text=f"Error parsing glob results: {exc}")],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        if not payload.get("ok"):
            return ToolChunk(
                content=[TextBlock(text=payload.get("error", "Glob failed"))],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        matches = payload.get("matches", [])
        if not matches:
            return ToolChunk(
                content=[TextBlock(text=f"No files found for pattern: {pattern}")],
                state=ToolResultState.SUCCESS,
                is_last=True,
            )

        return ToolChunk(
            content=[TextBlock(text="\n".join(matches))],
            state=ToolResultState.SUCCESS,
            is_last=True,
        )
