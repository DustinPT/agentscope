# -*- coding: utf-8 -*-
"""The PowerShell tool in agentscope."""

import base64
import os
from typing import AsyncGenerator, Any, List

from ._backend import BackendBase, LocalBackend, _normalize_newlines
from .._base import ToolBase, ToolMiddlewareBase
from .._response import ToolChunk
from ...message import TextBlock, ToolResultState
from ...permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
    PermissionRule,
)

_SHELL_CANDIDATES = ("pwsh", "powershell.exe")


class PowerShell(ToolBase):
    """Execute PowerShell commands through a workspace backend."""

    name: str = "PowerShell"
    description: str = """Executes a PowerShell command and returns its output.

Each command starts in the configured working directory, but PowerShell
session state does not persist between commands. Commands run without
loading the user's PowerShell profile."""
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The PowerShell command to execute.",
            },
            "description": {
                "type": "string",
                "description": "Clear, concise description of the command.",
            },
            "timeout": {
                "type": "integer",
                "description": (
                    "Optional timeout in milliseconds "
                    "(default: 120000, max: 600000)"
                ),
                "default": 120000,
                "maximum": 600000,
                "minimum": 0,
            },
        },
        "required": ["command"],
    }

    is_mcp: bool = False
    is_read_only: bool = False
    is_concurrency_safe: bool = False
    is_external_tool: bool = False
    is_state_injected: bool = False

    def __init__(
        self,
        cwd: str | os.PathLike[str] | None = None,
        middlewares: List[ToolMiddlewareBase] | None = None,
        backend: BackendBase | None = None,
    ) -> None:
        super().__init__(middlewares=middlewares)
        self._cwd = os.fspath(cwd) if cwd is not None else None
        self._backend = backend or LocalBackend()
        self._executable: str | None = None

    async def _resolve_executable(self) -> str:
        if self._executable is None:
            for candidate in _SHELL_CANDIDATES:
                probe = await self._backend.exec_shell(
                    [
                        candidate,
                        "-NoLogo",
                        "-NoProfile",
                        "-NonInteractive",
                        "-Command",
                        "exit 0",
                    ],
                    timeout=10.0,
                )
                if probe.exit_code != 127:
                    self._executable = candidate
                    break
            else:
                self._executable = "powershell.exe"
        return self._executable

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message="Execute PowerShell command",
            decision_reason="PowerShell command validation is not enabled",
        )

    def generate_suggestions(
        self,
        tool_input: dict[str, Any],
    ) -> List[PermissionRule]:
        return []

    async def call(
        self,
        command: str,
        description: str = "",
        timeout: int = 120000,
    ) -> AsyncGenerator[ToolChunk, None]:
        timeout_ms = min(timeout, 600000)
        encoded_user_command = base64.b64encode(
            command.encode("utf-16-le"),
        ).decode("ascii")
        powershell_script = (
            "$ProgressPreference = "
            "[System.Management.Automation.ActionPreference]::"
            "SilentlyContinue\n"
            "$OutputEncoding = [Console]::OutputEncoding = "
            "[System.Text.UTF8Encoding]::new($false)\n"
            "$AgentScopeCommand = [System.Text.Encoding]::Unicode.GetString("
            "[System.Convert]::FromBase64String("
            f"'{encoded_user_command}'))\n"
            "& ([ScriptBlock]::Create($AgentScopeCommand))"
        )
        encoded_command = base64.b64encode(
            powershell_script.encode("utf-16-le"),
        ).decode("ascii")
        try:
            executable = await self._resolve_executable()
            result = await self._backend.exec_shell(
                [
                    executable,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-EncodedCommand",
                    encoded_command,
                ],
                cwd=self._cwd,
                timeout=timeout_ms / 1000.0,
            )
        except Exception as exc:
            yield ToolChunk(
                content=[
                    TextBlock(text=f"Command failed: {command}\nError: {exc}"),
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )
            return

        stdout = _normalize_newlines(
            result.stdout.decode("utf-8", errors="replace"),
        )
        stderr = _normalize_newlines(
            result.stderr.decode("utf-8", errors="replace"),
        )
        if result.exit_code == -1 and result.stderr == b"timed out":
            yield ToolChunk(
                content=[
                    TextBlock(
                        text=f"Command timed out after {timeout_ms}ms: {command}",
                    ),
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )
            return

        if not result.ok():
            error_result = f"Command failed: {command}\n"
            if stdout:
                error_result += f"\nStdout:\n{stdout}"
            if stderr:
                error_result += f"\nStderr:\n{stderr}"
            yield ToolChunk(
                content=[TextBlock(text=error_result)],
                state=ToolResultState.ERROR,
                is_last=True,
            )
            return

        output = stdout
        if stderr:
            output = f"{output}\n{stderr}" if output else stderr
        yield ToolChunk(
            content=[TextBlock(text=output)],
            state=ToolResultState.SUCCESS,
            is_last=True,
        )
