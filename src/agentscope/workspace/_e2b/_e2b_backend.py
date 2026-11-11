# -*- coding: utf-8 -*-
"""E2B sandbox :class:`BackendBase` implementation."""

from __future__ import annotations

import asyncio
import posixpath
import shlex
from typing import Any

from ...tool import BackendBase, ExecResult, ShellHandle, ShellSnapshot


class _E2BShellHandle(ShellHandle):
    """Buffered E2B background command handle."""

    def __init__(
        self,
        handle: Any,
        *,
        sandbox: Any,
        cwd: str,
    ) -> None:
        self._handle = handle
        self._sandbox = sandbox
        self._cwd = cwd
        self._result: ExecResult | None = None

    def snapshot(self) -> ShellSnapshot:
        stdout = getattr(self._handle, "stdout", "") or ""
        stderr = getattr(self._handle, "stderr", "") or ""
        return ShellSnapshot(
            exit_code=None if self._result is None else self._result.exit_code,
            stdout=str(stdout).encode("utf-8"),
            stderr=str(stderr).encode("utf-8"),
        )

    async def wait(
        self,
        timeout: float | None = None,
    ) -> ExecResult | None:
        if self._result is not None:
            return self._result
        try:
            if timeout is None:
                result = await self._handle.wait()
            else:
                result = await asyncio.wait_for(self._handle.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

        self._result = ExecResult(
            exit_code=int(getattr(result, "exit_code", 0) or 0),
            stdout=str(getattr(result, "stdout", "") or "").encode("utf-8"),
            stderr=str(getattr(result, "stderr", "") or "").encode("utf-8"),
        )
        return self._result

    async def terminate(self) -> None:
        pid = getattr(self._handle, "pid", None)
        if pid:
            signal_script = (
                f"kill -TERM -- -{pid} 2>/dev/null "
                f"|| kill -TERM {pid} 2>/dev/null || true; "
                "sleep 1; "
                f"kill -KILL -- -{pid} 2>/dev/null "
                f"|| kill -KILL {pid} 2>/dev/null || true"
            )
            try:
                await self._sandbox.commands.run(
                    signal_script,
                    cwd=self._cwd,
                    timeout=5,
                )
            except Exception:  # noqa: BLE001
                pass
        await self._handle.kill()


class E2BBackend(BackendBase):
    """Backend that delegates to a running E2B sandbox."""

    def __init__(self, sandbox: Any, workdir: str) -> None:
        self._sandbox = sandbox
        self._workdir = workdir

    async def getcwd(self) -> str:
        return self._workdir

    async def exec_shell(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        try:
            handle = await self.start_shell(
                command,
                cwd=cwd,
                env=env,
            )
        except Exception as e:  # noqa: BLE001
            return ExecResult(
                exit_code=-1,
                stdout=b"",
                stderr=str(e).encode("utf-8"),
            )
        result = await handle.wait(timeout=timeout)
        if result is not None:
            return result
        await handle.terminate()
        return ExecResult(exit_code=-1, stdout=b"", stderr=b"timed out")

    async def start_shell(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> ShellHandle:
        command_line = " ".join(shlex.quote(arg) for arg in command)
        wrapped_command = (
            "if command -v setsid >/dev/null 2>&1; then "
            f"exec setsid {command_line}; "
            f"else exec {command_line}; fi"
        )
        resolved_cwd = cwd or self._workdir
        handle = await self._sandbox.commands.run(
            wrapped_command,
            cwd=resolved_cwd,
            envs=env,
            background=True,
        )
        return _E2BShellHandle(
            handle,
            sandbox=self._sandbox,
            cwd=resolved_cwd,
        )

    async def read_file(self, path: str) -> bytes:
        from e2b import FileNotFoundException

        try:
            data = await self._sandbox.files.read(path, format="bytes")
        except FileNotFoundException as exc:
            raise FileNotFoundError(f"not found in sandbox: {path}") from exc
        return bytes(data)

    async def write_file(self, path: str, data: bytes) -> None:
        parent = posixpath.dirname(path)
        if parent:
            await self.exec_shell(["mkdir", "-p", parent])
        await self._sandbox.files.write(path, data)

    async def ensure_dir(self, path: str) -> None:
        await self.exec_shell(["mkdir", "-p", path])
