# -*- coding: utf-8 -*-
"""BackendBase implementation that sandboxes builtin operations selectively."""

from __future__ import annotations

import asyncio
import base64
import json
import os
from dataclasses import dataclass
from typing import Any

from ...tool import BackendBase, DirEntry, ExecResult, LocalBackend
from ...tool._tool_call_context import get_current_tool_call
from ._sandbox_runtime_executor import SandboxCommandResult, SandboxRuntimeExecutor


@dataclass(frozen=True, slots=True)
class PreviewDirEntry:
    """One directory entry with an inline preview sample."""

    entry: DirEntry
    sample_bytes: bytes | None = None


@dataclass(frozen=True, slots=True)
class PreviewFileStat:
    """One file stat result with an inline preview sample."""

    entry: DirEntry | None
    sample_bytes: bytes | None = None


class SRTBackend(BackendBase):
    """Backend that locally handles workspace paths and sandboxes the rest."""

    _path_module = os.path

    def __init__(
        self,
        workdir: str,
        srt_settings_path: str,
        node_executable: str,
        sandbox_runtime_module_path: str,
        bridge_path: str,
        timeout: float | None = 30.0,
    ) -> None:
        self._workdir = workdir
        self._workdir_realpath = os.path.realpath(workdir)
        self._timeout = timeout
        self._violations_by_tool_call: dict[str, list[dict[str, Any]]] = {}
        self._local_backend = LocalBackend()
        self._executor = SandboxRuntimeExecutor(
            node_executable=node_executable,
            sandbox_runtime_module_path=sandbox_runtime_module_path,
            bridge_path=bridge_path,
            runtime_config=_load_runtime_config(srt_settings_path),
        )

    async def aclose(self) -> None:
        """Release backend resources."""
        return None

    def consume_tool_call_violations(self, tool_call_id: str) -> list[dict[str, Any]]:
        """Return and clear cached sandbox violations for one tool call."""
        return self._violations_by_tool_call.pop(tool_call_id, [])

    def _record_violations(
        self,
        result: SandboxCommandResult,
    ) -> None:
        tool_call = get_current_tool_call()
        if tool_call is None or not result.violations:
            return
        self._violations_by_tool_call.setdefault(tool_call.tool_call_id, []).extend(
            [record.to_dict() for record in result.violations],
        )

    async def getcwd(self) -> str:
        return self._workdir

    async def expanduser(self, path: str) -> str:
        return os.path.expanduser(path)

    async def exec_shell(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        tool_call = get_current_tool_call()
        result = await self._executor.exec_command(
            command=command,
            cwd=cwd or self._workdir,
            timeout=timeout,
            tool_call_id=None if tool_call is None else tool_call.tool_call_id,
            tool_name=None if tool_call is None else tool_call.tool_name,
        )
        self._record_violations(result)
        return ExecResult(
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    async def read_file(self, path: str) -> bytes:
        if self._should_use_local_file_backend(path):
            return await self._local_backend.read_file(path)
        result = await self._exec_file_operation("read-file", path)
        return result.stdout

    async def write_file(self, path: str, data: bytes) -> None:
        if self._should_use_local_file_backend(path):
            await self._local_backend.write_file(path, data)
            return
        await self._exec_file_operation("write-file", path, data=data)

    async def ensure_dir(self, path: str) -> None:
        if self._should_use_local_file_backend(path):
            await self._local_backend.ensure_dir(path)
            return
        await self._exec_file_operation("ensure-dir", path)

    async def file_exists(self, path: str) -> bool:
        if self._should_use_local_file_backend(path):
            return await self._local_backend.file_exists(path)
        result = await self._exec_file_operation("exists", path)
        return result.stdout.decode("utf-8").strip() == "1"

    async def is_dir(self, path: str) -> bool:
        if self._should_use_local_file_backend(path):
            return await self._local_backend.is_dir(path)
        result = await self._exec_file_operation("is-dir", path)
        return result.stdout.decode("utf-8").strip() == "1"

    async def list_dir(
        self,
        path: str,
        *,
        recursive: bool = False,
    ) -> list[str]:
        if self._should_use_local_file_backend(path):
            return await self._local_backend.list_dir(path, recursive=recursive)
        result = await self._exec_file_operation(
            "list-dir",
            path,
            recursive=recursive,
        )
        return list(json.loads(result.stdout.decode("utf-8")))

    async def scandir(self, path: str) -> list[DirEntry]:
        if self._should_use_local_file_backend(path):
            return await self._local_backend.scandir(path)
        result = await self._exec_file_operation("scandir", path)
        return [DirEntry(**entry) for entry in json.loads(result.stdout.decode("utf-8"))]

    async def scandir_with_samples(
        self,
        path: str,
        *,
        sample_size: int = 4096,
    ) -> list[PreviewDirEntry]:
        if self._should_use_local_file_backend(path):
            return await self._local_scandir_with_samples(
                path,
                sample_size=sample_size,
            )
        result = await self._exec_file_operation(
            "scandir-with-samples",
            path,
            sample_size=sample_size,
        )
        payload = json.loads(result.stdout.decode("utf-8"))
        return [
            PreviewDirEntry(
                entry=DirEntry(
                    name=item["name"],
                    is_dir=item["is_dir"],
                    size_bytes=item.get("size_bytes"),
                    mtime=item.get("mtime"),
                ),
                sample_bytes=None
                if item.get("sample_b64") is None
                else base64.b64decode(item["sample_b64"].encode("ascii")),
            )
            for item in payload
        ]

    async def stat(self, path: str) -> DirEntry | None:
        if self._should_use_local_file_backend(path):
            return await self._local_backend.stat(path)
        result = await self._exec_file_operation("stat", path)
        payload = json.loads(result.stdout.decode("utf-8"))
        return None if payload is None else DirEntry(**payload)

    async def stat_with_sample(
        self,
        path: str,
        *,
        sample_size: int = 4096,
    ) -> PreviewFileStat:
        if self._should_use_local_file_backend(path):
            return await self._local_stat_with_sample(
                path,
                sample_size=sample_size,
            )
        result = await self._exec_file_operation(
            "stat-with-sample",
            path,
            sample_size=sample_size,
        )
        payload = json.loads(result.stdout.decode("utf-8"))
        if payload is None:
            return PreviewFileStat(entry=None, sample_bytes=None)
        return PreviewFileStat(
            entry=DirEntry(
                name=payload["name"],
                is_dir=payload["is_dir"],
                size_bytes=payload.get("size_bytes"),
                mtime=payload.get("mtime"),
            ),
            sample_bytes=None
            if payload.get("sample_b64") is None
            else base64.b64decode(payload["sample_b64"].encode("ascii")),
        )

    async def stat_mtime(self, path: str) -> float | None:
        if self._should_use_local_file_backend(path):
            return await self._local_backend.stat_mtime(path)
        result = await self._exec_file_operation("stat-mtime", path)
        return json.loads(result.stdout.decode("utf-8"))["mtime"]

    async def delete_path(self, path: str) -> None:
        if self._should_use_local_file_backend(path):
            await self._local_backend.delete_path(path)
            return
        await self._exec_file_operation("delete-path", path)

    async def read_stream(
        self,
        path: str,
        chunk_size: int = 1024 * 1024,
    ):
        if self._should_use_local_file_backend(path):
            async for chunk in self._local_backend.read_stream(
                path,
                chunk_size=chunk_size,
            ):
                yield chunk
            return
        tool_call = get_current_tool_call()
        stream = await self._executor.exec_file_read_stream(
            path=path,
            cwd=self._workdir,
            tool_call_id=None if tool_call is None else tool_call.tool_call_id,
            tool_name=None if tool_call is None else tool_call.tool_name,
        )
        completed = False
        try:
            async for chunk in stream.iter_chunks(chunk_size):
                yield chunk
            completed = True
        finally:
            if completed:
                result = await stream.wait_result()
                self._record_violations(result)
                if result.exit_code != 0:
                    raise _file_operation_error(result)
            else:
                await stream.aclose()

    async def _exec_file_operation(
        self,
        operation: str,
        path: str,
        *,
        recursive: bool = False,
        data: bytes | None = None,
        sample_size: int | None = None,
    ) -> SandboxCommandResult:
        tool_call = get_current_tool_call()
        result = await self._executor.exec_file_operation(
            operation=operation,
            path=path,
            cwd=self._workdir,
            tool_call_id=None if tool_call is None else tool_call.tool_call_id,
            tool_name=None if tool_call is None else tool_call.tool_name,
            recursive=recursive,
            data=data,
            sample_size=sample_size,
        )
        self._record_violations(result)
        if result.exit_code != 0:
            raise _file_operation_error(result)
        return result

    def _should_use_local_file_backend(self, path: str) -> bool:
        """Return whether one path is fully contained in the workspace root."""
        resolved_path = self._resolve_real_path(path)
        if resolved_path is None:
            return False
        try:
            return os.path.commonpath(
                [self._workdir_realpath, resolved_path],
            ) == self._workdir_realpath
        except ValueError:
            return False

    def _resolve_real_path(self, path: str) -> str | None:
        """Resolve one path against the workspace root for containment checks."""
        if not path:
            candidate = self._workdir
        elif os.path.isabs(path):
            candidate = path
        else:
            candidate = os.path.join(self._workdir, path)
        try:
            return os.path.realpath(candidate)
        except OSError:
            return None

    async def _local_scandir_with_samples(
        self,
        path: str,
        *,
        sample_size: int,
    ) -> list[PreviewDirEntry]:
        """List directory entries and inline file samples without sandbox overhead."""
        payload = await self._local_backend.scandir(path)
        entries: list[PreviewDirEntry] = []
        for entry in payload:
            sample_bytes: bytes | None = None
            if not entry.is_dir:
                sample_path = os.path.join(path, entry.name)
                sample_bytes = await _read_local_sample(
                    sample_path,
                    sample_size,
                )
            entries.append(
                PreviewDirEntry(
                    entry=entry,
                    sample_bytes=sample_bytes,
                ),
            )
        return entries

    async def _local_stat_with_sample(
        self,
        path: str,
        *,
        sample_size: int,
    ) -> PreviewFileStat:
        """Return file stat and preview sample without sandbox overhead."""
        entry = await self._local_backend.stat(path)
        if entry is None or entry.is_dir:
            return PreviewFileStat(entry=entry, sample_bytes=None)
        return PreviewFileStat(
            entry=entry,
            sample_bytes=await _read_local_sample(path, sample_size),
        )


def _load_runtime_config(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as file_obj:
        return json.load(file_obj)


async def _read_local_sample(path: str, sample_size: int) -> bytes:
    """Read a small local file prefix for MIME sniffing and preview."""
    if sample_size <= 0:
        return b""
    return await asyncio.to_thread(_read_local_sample_sync, path, sample_size)


def _read_local_sample_sync(path: str, sample_size: int) -> bytes:
    with open(path, "rb") as file_obj:
        return file_obj.read(sample_size)


def _file_operation_error(result: SandboxCommandResult) -> Exception:
    detail = _decode_helper_error(result.stderr)
    error_type = detail.get("error_type")
    message = str(
        detail.get("message") or result.stderr.decode("utf-8", errors="replace"),
    )
    if error_type == "PermissionError":
        return PermissionError(message)
    if error_type == "FileNotFoundError":
        return FileNotFoundError(message)
    if error_type == "NotADirectoryError":
        return NotADirectoryError(message)
    if error_type == "IsADirectoryError":
        return IsADirectoryError(message)
    if error_type == "ValueError":
        return ValueError(message)
    return RuntimeError(message)


def _decode_helper_error(stderr: bytes) -> dict[str, Any]:
    if not stderr:
        return {}
    try:
        return json.loads(stderr.decode("utf-8"))
    except Exception:
        return {"message": stderr.decode("utf-8", errors="replace")}
