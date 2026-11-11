# -*- coding: utf-8 -*-
"""Docker container :class:`BackendBase` implementation."""

from __future__ import annotations

import asyncio
import io
import os
import posixpath
import shlex
import tarfile
import time
from typing import Any

from ...tool import BackendBase, ExecResult, ShellHandle, ShellSnapshot


class _DockerShellHandle(ShellHandle):
    """Buffered docker exec handle."""

    def __init__(
        self,
        *,
        container: Any,
        exec_obj: Any,
        stream_task: asyncio.Task[None],
        stdout_buffer: bytearray,
        stderr_buffer: bytearray,
        cwd: str,
    ) -> None:
        self._container = container
        self._exec_obj = exec_obj
        self._stream_task = stream_task
        self._stdout_buffer = stdout_buffer
        self._stderr_buffer = stderr_buffer
        self._cwd = cwd
        self._exit_code: int | None = None

    def snapshot(self) -> ShellSnapshot:
        return ShellSnapshot(
            exit_code=self._exit_code,
            stdout=bytes(self._stdout_buffer),
            stderr=bytes(self._stderr_buffer),
        )

    async def wait(
        self,
        timeout: float | None = None,
    ) -> ExecResult | None:
        try:
            if timeout is None:
                await self._stream_task
            else:
                await asyncio.wait_for(self._stream_task, timeout=timeout)
        except asyncio.TimeoutError:
            return None

        inspect = await self._exec_obj.inspect()
        code = inspect.get("ExitCode", -1)
        self._exit_code = -1 if code is None else int(code)
        snapshot = self.snapshot()
        return ExecResult(
            exit_code=snapshot.exit_code or 0,
            stdout=snapshot.stdout,
            stderr=snapshot.stderr,
        )

    async def terminate(self) -> None:
        inspect = await self._exec_obj.inspect()
        pid = inspect.get("Pid")
        if inspect.get("Running") and pid:
            await self._signal_process("TERM", int(pid))
            try:
                await asyncio.wait_for(self._stream_task, timeout=1.0)
            except asyncio.TimeoutError:
                await self._signal_process("KILL", int(pid))
        await asyncio.gather(self._stream_task, return_exceptions=True)

    async def _signal_process(self, signal_name: str, pid: int) -> None:
        signal_script = (
            f"kill -{signal_name} -- -{pid} 2>/dev/null "
            f"|| kill -{signal_name} {pid} 2>/dev/null || true"
        )
        kill_exec = await self._container.exec(
            cmd=["sh", "-lc", signal_script],
            workdir=self._cwd,
        )
        async with kill_exec.start() as stream:
            while await stream.read_out() is not None:
                pass


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
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        handle = await self.start_shell(
            command,
            cwd=cwd,
            env=env,
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
        command_line = shlex.join(command)
        wrapped_command = (
            "if command -v setsid >/dev/null 2>&1; then "
            f"exec setsid {command_line}; "
            f"else exec {command_line}; fi"
        )
        exec_obj = await self._container.exec(
            cmd=["sh", "-lc", wrapped_command],
            workdir=cwd or self._workdir,
            environment=env,
        )
        stdout_buffer = bytearray()
        stderr_buffer = bytearray()

        async def _drain_stream() -> None:
            async with exec_obj.start() as stream:
                while True:
                    msg = await stream.read_out()
                    if msg is None:
                        return
                    if msg.stream == 1:
                        stdout_buffer.extend(msg.data)
                    else:
                        stderr_buffer.extend(msg.data)

        return _DockerShellHandle(
            container=self._container,
            exec_obj=exec_obj,
            stream_task=asyncio.create_task(_drain_stream()),
            stdout_buffer=stdout_buffer,
            stderr_buffer=stderr_buffer,
            cwd=cwd or self._workdir,
        )

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

    async def upload_directory(self, local_dir: str, dest_dir: str) -> None:
        """Upload one local directory with a single ``put_archive`` call."""
        if not os.path.isdir(local_dir):
            raise FileNotFoundError(f"local directory not found: {local_dir}")

        parent = posixpath.dirname(dest_dir) or "/"
        await self.exec_shell(["mkdir", "-p", parent])

        buf = io.BytesIO()
        arcname = posixpath.basename(dest_dir.rstrip("/"))
        with tarfile.open(fileobj=buf, mode="w") as tf:
            tf.add(local_dir, arcname=arcname)
        await self._container.put_archive(parent, buf.getvalue())
