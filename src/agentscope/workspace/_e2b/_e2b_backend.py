# -*- coding: utf-8 -*-
"""E2B sandbox :class:`BackendBase` implementation."""

from __future__ import annotations

import posixpath
import shlex
from typing import Any

from ...tool import BackendBase, ExecResult


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
    ) -> ExecResult:
        from e2b import CommandExitException

        command_line = " ".join(shlex.quote(arg) for arg in command)
        kwargs: dict[str, Any] = {"cwd": cwd or self._workdir}
        if timeout is not None:
            kwargs["timeout"] = timeout
        try:
            res = await self._sandbox.commands.run(command_line, **kwargs)
            return ExecResult(
                exit_code=int(res.exit_code or 0),
                stdout=(res.stdout or "").encode("utf-8"),
                stderr=(res.stderr or "").encode("utf-8"),
            )
        except CommandExitException as e:
            return ExecResult(
                exit_code=int(e.exit_code or 1),
                stdout=(e.stdout or "").encode("utf-8"),
                stderr=(e.stderr or "").encode("utf-8"),
            )
        except Exception as e:  # noqa: BLE001
            return ExecResult(
                exit_code=-1,
                stdout=b"",
                stderr=str(e).encode("utf-8"),
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
