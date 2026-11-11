# -*- coding: utf-8 -*-
"""Backend abstraction for builtin tools."""

from __future__ import annotations

import asyncio
import os
import posixpath
import shlex
import signal
from abc import ABC, abstractmethod
from dataclasses import dataclass
from types import ModuleType
from typing import Any, AsyncIterator

import aiofiles

DEFAULT_READ_CHUNK_SIZE = 1024 * 1024
_FIND_ENTRY_FORMAT = "%Y\\t%s\\t%T@\\t%f\\0"


@dataclass(frozen=True, slots=True)
class ExecResult:
    """Result of running a shell command via a backend."""

    exit_code: int
    stdout: bytes
    stderr: bytes

    def ok(self) -> bool:
        """Whether the command exited successfully."""
        return self.exit_code == 0


@dataclass(frozen=True, slots=True)
class DirEntry:
    """One entry from :meth:`BackendBase.scandir`."""

    name: str
    is_dir: bool
    size_bytes: int | None = None
    mtime: float | None = None


@dataclass(frozen=True, slots=True)
class ShellSnapshot:
    """Best-effort snapshot of one running shell command."""

    exit_code: int | None
    stdout: bytes
    stderr: bytes


class ShellHandle(ABC):
    """Lifecycle handle for one started shell command."""

    @abstractmethod
    def snapshot(self) -> ShellSnapshot:
        """Return the latest buffered stdout/stderr snapshot."""

    @abstractmethod
    async def wait(
        self,
        timeout: float | None = None,
    ) -> ExecResult | None:
        """Wait for completion, or return ``None`` on timeout."""

    @abstractmethod
    async def terminate(self) -> None:
        """Stop the command and release its resources."""


def _normalize_newlines(text: str) -> str:
    """Normalize Windows and classic-Mac newlines to ``\\n``."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


class BackendBase(ABC):
    """Filesystem and subprocess interface for tools and workspace sync."""

    _path_module: ModuleType = posixpath

    def join_path(self, path: str, *paths: str) -> str:
        return self._path_module.join(path, *paths)

    def dirname(self, path: str) -> str:
        return self._path_module.dirname(path)

    def basename(self, path: str) -> str:
        return self._path_module.basename(path)

    def isabs(self, path: str) -> bool:
        return self._path_module.isabs(path)

    def normpath(self, path: str) -> str:
        return self._path_module.normpath(path)

    def abspath(self, path: str, *, cwd: str) -> str:
        if self._path_module.isabs(path):
            return self._path_module.normpath(path)
        return self._path_module.normpath(self._path_module.join(cwd, path))

    @abstractmethod
    async def exec_shell(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        """Run a program directly from an argument vector."""

    @abstractmethod
    async def start_shell(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> ShellHandle:
        """Start a program and return a handle for later observation."""

    @abstractmethod
    async def read_file(self, path: str) -> bytes:
        """Read the full contents of ``path`` as raw bytes."""

    @abstractmethod
    async def write_file(self, path: str, data: bytes) -> None:
        """Write ``data`` to ``path``, creating parent directories."""

    @abstractmethod
    async def ensure_dir(self, path: str) -> None:
        """Create ``path`` as a directory if it does not already exist."""

    async def upload_directory(self, local_dir: str, dest_dir: str) -> None:
        """Copy one host-local directory into the backend filesystem."""
        if not os.path.isdir(local_dir):
            raise FileNotFoundError(f"local directory not found: {local_dir}")

        await self.ensure_dir(dest_dir)
        for root, dirs, files in os.walk(local_dir):
            dirs.sort()
            files.sort()
            rel_root = os.path.relpath(root, local_dir)
            current_dest = dest_dir
            if rel_root != ".":
                current_dest = self.join_path(
                    dest_dir,
                    *rel_root.split(os.sep),
                )
                await self.ensure_dir(current_dest)

            for dir_name in dirs:
                await self.ensure_dir(self.join_path(current_dest, dir_name))

            for file_name in files:
                local_path = os.path.join(root, file_name)
                remote_path = self.join_path(current_dest, file_name)
                async with aiofiles.open(local_path, mode="rb") as file_obj:
                    await self.write_file(remote_path, await file_obj.read())

    async def write_stream(
        self,
        path: str,
        stream: AsyncIterator[bytes],
    ) -> None:
        chunks = [chunk async for chunk in stream]
        await self.write_file(path, b"".join(chunks))

    async def read_stream(
        self,
        path: str,
        chunk_size: int = DEFAULT_READ_CHUNK_SIZE,
    ) -> AsyncIterator[bytes]:
        data = await self.read_file(path)
        for start in range(0, len(data), chunk_size):
            yield data[start : start + chunk_size]

    async def getcwd(self) -> str:
        result = await self.exec_shell(["pwd"])
        return result.stdout.decode("utf-8", errors="replace").strip()

    async def expanduser(self, path: str) -> str:
        if not path or path[0] != "~":
            return path
        if len(path) > 1 and path[1] not in ("/", self._path_module.sep):
            return path
        result = await self.exec_shell(["printenv", "HOME"])
        home = result.stdout.decode("utf-8", errors="replace").strip()
        if not home:
            return path
        return home + path[1:]

    async def file_exists(self, path: str) -> bool:
        return (await self.exec_shell(["test", "-e", path])).ok()

    async def is_dir(self, path: str) -> bool:
        return (await self.exec_shell(["test", "-d", path])).ok()

    async def list_dir(
        self,
        path: str,
        *,
        recursive: bool = False,
    ) -> list[str]:
        if recursive:
            command = ["find", path, "-type", "f", "-print0"]
        else:
            command = [
                "find",
                path,
                "-mindepth",
                "1",
                "-maxdepth",
                "1",
                "-printf",
                "%f\\0",
            ]
        result = await self.exec_shell(command)
        if not result.ok():
            return []
        return [
            part.decode("utf-8", errors="surrogateescape")
            for part in result.stdout.split(b"\0")
            if part
        ]

    async def scandir(self, path: str) -> list[DirEntry]:
        return await self._find_entries(
            [
                "find",
                path,
                "-mindepth",
                "1",
                "-maxdepth",
                "1",
                "-printf",
                _FIND_ENTRY_FORMAT,
            ],
        )

    async def stat(self, path: str) -> DirEntry | None:
        entries = await self._find_entries(
            ["find", path, "-maxdepth", "0", "-printf", _FIND_ENTRY_FORMAT],
            skip_unresolvable=True,
        )
        return entries[0] if entries else None

    async def _find_entries(
        self,
        command: list[str],
        *,
        skip_unresolvable: bool = False,
    ) -> list[DirEntry]:
        result = await self.exec_shell(command)
        if not result.ok():
            return []

        entries: list[DirEntry] = []
        for record in result.stdout.split(b"\0"):
            if not record:
                continue
            fields = record.decode("utf-8", errors="surrogateescape").split(
                "\t",
                3,
            )
            if len(fields) != 4:
                continue
            kind, raw_size, raw_mtime, name = fields
            try:
                size: int | None = int(raw_size)
            except ValueError:
                size = None
            try:
                mtime: float | None = float(raw_mtime)
            except ValueError:
                mtime = None
            if kind in ("N", "L", "?"):
                if skip_unresolvable:
                    continue
                size, mtime = None, None
            is_dir = kind == "d"
            entries.append(
                DirEntry(
                    name=name,
                    is_dir=is_dir,
                    size_bytes=None if is_dir else size,
                    mtime=mtime,
                ),
            )
        return entries

    async def stat_mtime(self, path: str) -> float | None:
        quoted = shlex.quote(path)
        script = (
            f"stat -c %Y {quoted} 2>/dev/null || "
            f"stat -f %m {quoted} 2>/dev/null"
        )
        result = await self.exec_shell(["sh", "-c", script])
        if not result.ok():
            return None
        try:
            return float(
                result.stdout.decode("utf-8", errors="replace").strip(),
            )
        except ValueError:
            return None

    async def delete_path(self, path: str) -> None:
        await self.exec_shell(["rm", "-rf", path])


def _subprocess_creation_kwargs() -> dict[str, Any]:
    """Return platform-specific subprocess creation options."""
    if os.name != "nt":
        return {}

    import subprocess

    return {
        "creationflags": getattr(
            subprocess,
            "CREATE_NO_WINDOW",
            0x08000000,
        ),
    }


class LocalBackend(BackendBase):
    """Host-local backend implementation."""

    _path_module = os.path

    class _LocalShellHandle(ShellHandle):
        """Buffered subprocess handle for host-local shell execution."""

        def __init__(
            self,
            process: asyncio.subprocess.Process,
            stdout_task: asyncio.Task[None],
            stderr_task: asyncio.Task[None],
            stdout_buffer: bytearray,
            stderr_buffer: bytearray,
        ) -> None:
            self._process = process
            self._stdout_task = stdout_task
            self._stderr_task = stderr_task
            self._stdout_buffer = stdout_buffer
            self._stderr_buffer = stderr_buffer

        def snapshot(self) -> ShellSnapshot:
            return ShellSnapshot(
                exit_code=self._process.returncode,
                stdout=bytes(self._stdout_buffer),
                stderr=bytes(self._stderr_buffer),
            )

        async def wait(
            self,
            timeout: float | None = None,
        ) -> ExecResult | None:
            try:
                if timeout is None:
                    await self._process.wait()
                else:
                    await asyncio.wait_for(self._process.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                return None

            await asyncio.gather(
                self._stdout_task,
                self._stderr_task,
                return_exceptions=True,
            )
            snapshot = self.snapshot()
            return ExecResult(
                exit_code=snapshot.exit_code or 0,
                stdout=snapshot.stdout,
                stderr=snapshot.stderr,
            )

        async def terminate(self) -> None:
            if self._process.returncode is None:
                if os.name != "nt":
                    try:
                        os.killpg(
                            os.getpgid(self._process.pid),
                            signal.SIGTERM,
                        )
                    except ProcessLookupError:
                        pass
                    try:
                        await asyncio.wait_for(
                            self._process.wait(),
                            timeout=1.0,
                        )
                    except asyncio.TimeoutError:
                        try:
                            os.killpg(
                                os.getpgid(self._process.pid),
                                signal.SIGKILL,
                            )
                        except ProcessLookupError:
                            pass
                        await self._process.wait()
                else:
                    self._process.kill()
                    await self._process.wait()
            await asyncio.gather(
                self._stdout_task,
                self._stderr_task,
                return_exceptions=True,
            )

    @staticmethod
    async def _read_pipe(
        stream: asyncio.StreamReader | None,
        buffer: bytearray,
    ) -> None:
        if stream is None:
            return
        while True:
            chunk = await stream.read(DEFAULT_READ_CHUNK_SIZE)
            if not chunk:
                return
            buffer.extend(chunk)

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
        except (FileNotFoundError, NotADirectoryError, OSError) as exc:
            return ExecResult(
                exit_code=127,
                stdout=b"",
                stderr=str(exc).encode("utf-8"),
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
        kwargs = _subprocess_creation_kwargs()
        if cwd is not None:
            kwargs["cwd"] = cwd
        if env:
            kwargs["env"] = {**os.environ, **env}
        if os.name != "nt":
            kwargs["start_new_session"] = True

        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **kwargs,
        )
        stdout_buffer = bytearray()
        stderr_buffer = bytearray()
        stdout_task = asyncio.create_task(
            self._read_pipe(process.stdout, stdout_buffer),
        )
        stderr_task = asyncio.create_task(
            self._read_pipe(process.stderr, stderr_buffer),
        )
        return self._LocalShellHandle(
            process=process,
            stdout_task=stdout_task,
            stderr_task=stderr_task,
            stdout_buffer=stdout_buffer,
            stderr_buffer=stderr_buffer,
        )

    async def read_file(self, path: str) -> bytes:
        async with aiofiles.open(path, mode="rb") as f:
            return await f.read()

    async def write_file(self, path: str, data: bytes) -> None:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        async with aiofiles.open(path, mode="wb") as f:
            await f.write(data)

    async def ensure_dir(self, path: str) -> None:
        os.makedirs(path, exist_ok=True)

    async def upload_directory(self, local_dir: str, dest_dir: str) -> None:
        """Copy one local directory with ``copytree``."""
        if not os.path.isdir(local_dir):
            raise FileNotFoundError(f"local directory not found: {local_dir}")
        parent = os.path.dirname(dest_dir)
        if parent:
            os.makedirs(parent, exist_ok=True)

        import shutil

        await asyncio.to_thread(
            shutil.copytree,
            local_dir,
            dest_dir,
            dirs_exist_ok=False,
        )

    async def write_stream(
        self,
        path: str,
        stream: AsyncIterator[bytes],
    ) -> None:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        async with aiofiles.open(path, mode="wb") as f:
            async for chunk in stream:
                await f.write(chunk)

    async def read_stream(
        self,
        path: str,
        chunk_size: int = DEFAULT_READ_CHUNK_SIZE,
    ) -> AsyncIterator[bytes]:
        async with aiofiles.open(path, mode="rb") as f:
            while chunk := await f.read(chunk_size):
                yield chunk

    async def getcwd(self) -> str:
        return os.getcwd()

    async def expanduser(self, path: str) -> str:
        return os.path.expanduser(path)

    async def file_exists(self, path: str) -> bool:
        return os.path.exists(path)

    async def is_dir(self, path: str) -> bool:
        return os.path.isdir(path)

    async def list_dir(
        self,
        path: str,
        *,
        recursive: bool = False,
    ) -> list[str]:
        if recursive:
            results: list[str] = []
            for root, _dirs, files in os.walk(path):
                for file in files:
                    results.append(os.path.join(root, file))
            return results
        return os.listdir(path)

    async def scandir(self, path: str) -> list[DirEntry]:
        entries: list[DirEntry] = []
        with os.scandir(path) as it:
            for entry in it:
                try:
                    stat_result = entry.stat(follow_symlinks=True)
                    is_dir = entry.is_dir(follow_symlinks=True)
                    entries.append(
                        DirEntry(
                            name=entry.name,
                            is_dir=is_dir,
                            size_bytes=None if is_dir else stat_result.st_size,
                            mtime=stat_result.st_mtime,
                        ),
                    )
                except FileNotFoundError:
                    entries.append(
                        DirEntry(
                            name=entry.name,
                            is_dir=False,
                            size_bytes=None,
                            mtime=None,
                        ),
                    )
        return entries

    async def stat(self, path: str) -> DirEntry | None:
        if not os.path.exists(path):
            return None
        stat_result = os.stat(path)
        is_dir = os.path.isdir(path)
        return DirEntry(
            name=os.path.basename(path),
            is_dir=is_dir,
            size_bytes=None if is_dir else stat_result.st_size,
            mtime=stat_result.st_mtime,
        )

    async def stat_mtime(self, path: str) -> float | None:
        try:
            return os.path.getmtime(path)
        except OSError:
            return None

    async def delete_path(self, path: str) -> None:
        import shutil

        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
