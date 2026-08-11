# -*- coding: utf-8 -*-
"""Docker container :class:`BackendBase` implementation."""

from __future__ import annotations

import asyncio
import io
import posixpath
import tarfile
import time
from typing import Any

from ...tool import BackendBase, ExecResult


class DockerBackend(BackendBase):
    """Backend that delegates to a running Docker container."""

    def __init__(self, container: Any, workdir: str) -> None:
        self._container = container
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
        async def _run() -> ExecResult:
            exec_obj = await self._container.exec(
                cmd=command,
                workdir=cwd or self._workdir,
            )
            stdout_parts: list[bytes] = []
            stderr_parts: list[bytes] = []
            async with exec_obj.start() as stream:
                while True:
                    msg = await stream.read_out()
                    if msg is None:
                        break
                    if msg.stream == 1:
                        stdout_parts.append(msg.data)
                    else:
                        stderr_parts.append(msg.data)
            inspect = await exec_obj.inspect()
            code = inspect.get("ExitCode", -1)
            if code is None:
                code = -1
            return ExecResult(
                exit_code=int(code),
                stdout=b"".join(stdout_parts),
                stderr=b"".join(stderr_parts),
            )

        if timeout is None:
            return await _run()
        try:
            return await asyncio.wait_for(_run(), timeout=timeout)
        except asyncio.TimeoutError:
            return ExecResult(exit_code=-1, stdout=b"", stderr=b"timed out")

    async def read_file(self, path: str) -> bytes:
        from aiodocker import exceptions as aiodocker_exceptions

        try:
            tar = await self._container.get_archive(path)
        except aiodocker_exceptions.DockerError as exc:
            if exc.status == 404:
                raise FileNotFoundError(f"not found in container: {path}") from exc
            raise

        try:
            for member in tar.getmembers():
                if member.isfile():
                    f = tar.extractfile(member)
                    if f:
                        return f.read()
        finally:
            tar.close()
        raise FileNotFoundError(f"not found in container: {path}")

    async def write_file(self, path: str, data: bytes) -> None:
        parent = posixpath.dirname(path) or "/"
        name = posixpath.basename(path)
        await self.exec_shell(["mkdir", "-p", parent])
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            # Preserve a sensible file mtime in the container instead of
            # leaving tar headers at the Unix epoch default.
            info.mtime = int(time.time())
            tf.addfile(info, io.BytesIO(data))
        await self._container.put_archive(parent, buf.getvalue())

    async def ensure_dir(self, path: str) -> None:
        await self.exec_shell(["mkdir", "-p", path])
