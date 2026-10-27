"""Python wrapper around sandbox-runtime bridge execution."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shlex
import sys
import uuid
from dataclasses import dataclass
from typing import Any

from ._sandbox_violation import SandboxViolationRecord, normalize_violations

_FILE_OPS_HELPER_PATH = os.path.join(
    os.path.dirname(__file__),
    "_sandbox_file_ops.py",
)


@dataclass(frozen=True, slots=True)
class SandboxCommandResult:
    """One sandboxed command execution result."""

    exit_code: int
    stdout: bytes
    stderr: bytes
    annotated_stderr: bytes
    violations: list[SandboxViolationRecord]


class SandboxCommandStream:
    """One streaming sandbox command execution."""

    def __init__(
        self,
        *,
        process: asyncio.subprocess.Process,
        stderr_task: asyncio.Task[bytes],
        command_id: str,
        tool_name: str | None,
    ) -> None:
        self._process = process
        self._stderr_task = stderr_task
        self._command_id = command_id
        self._tool_name = tool_name
        self._result: SandboxCommandResult | None = None

    async def iter_chunks(self, chunk_size: int = 1024 * 1024):
        """Yield stdout chunks from the sandboxed command."""
        while True:
            chunk = await self._process.stdout.read(chunk_size)
            if not chunk:
                break
            yield chunk

    async def wait_result(self) -> SandboxCommandResult:
        """Wait until the sandbox command finishes and return metadata."""
        if self._result is not None:
            return self._result

        stderr = await self._stderr_task
        returncode = await self._process.wait()
        try:
            bridge_result = json.loads(stderr.decode("utf-8"))
        except Exception:
            bridge_result = None

        if returncode != 0:
            self._result = SandboxCommandResult(
                exit_code=returncode,
                stdout=b"",
                stderr=stderr,
                annotated_stderr=stderr,
                violations=[],
            )
            return self._result

        assert bridge_result is not None
        violations = normalize_violations(
            bridge_result.get("violations", []),
            tool_call_id=self._command_id,
            tool_name=self._tool_name or "unknown",
        )
        self._result = SandboxCommandResult(
            exit_code=int(bridge_result["exit_code"]),
            stdout=b"",
            stderr=base64.b64decode(bridge_result["stderr"].encode("ascii")),
            annotated_stderr=base64.b64decode(
                bridge_result["annotated_stderr"].encode("ascii"),
            ),
            violations=violations,
        )
        return self._result

    async def aclose(self) -> None:
        """Stop the streaming command if it is still running."""
        if self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
        if self._process.stdout is not None:
            try:
                await self._process.stdout.read()
            except Exception:
                pass
        try:
            await self._stderr_task
        except Exception:
            pass
        transport = getattr(self._process, "_transport", None)
        if transport is not None:
            transport.close()


class SandboxRuntimeExecutor:
    """Run one command through sandbox-runtime and normalize violations."""

    def __init__(
        self,
        *,
        node_executable: str,
        sandbox_runtime_module_path: str,
        bridge_path: str,
        runtime_config: dict[str, Any],
    ) -> None:
        self._node_executable = node_executable
        self._sandbox_runtime_module_path = sandbox_runtime_module_path
        self._bridge_path = bridge_path
        self._runtime_config = runtime_config

    async def exec_command(
        self,
        *,
        command: list[str],
        cwd: str | None,
        timeout: float | None,
        tool_call_id: str | None,
        tool_name: str | None,
        command_text: str | None = None,
    ) -> SandboxCommandResult:
        """Execute one argv command through sandbox-runtime."""
        command_id = tool_call_id or f"srt-{uuid.uuid4().hex}"
        payload = {
            "sandboxRuntimeModulePath": self._sandbox_runtime_module_path,
            "runtimeConfig": self._runtime_config,
            "command": shlex.join(command),
            "cwd": cwd,
            "timeoutMs": None if timeout is None else max(int(timeout * 1000), 0),
            "commandId": command_id,
            "commandText": command_text or shlex.join(command),
        }
        process = await asyncio.create_subprocess_exec(
            self._node_executable,
            self._bridge_path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate(
            json.dumps(payload).encode("utf-8"),
        )
        if process.returncode != 0:
            raise RuntimeError(_decode_bridge_error(stderr))

        bridge_result = json.loads(stdout.decode("utf-8"))
        violations = normalize_violations(
            bridge_result.get("violations", []),
            tool_call_id=command_id,
            tool_name=tool_name or "unknown",
        )
        return SandboxCommandResult(
            exit_code=int(bridge_result["exit_code"]),
            stdout=base64.b64decode(bridge_result["stdout"].encode("ascii")),
            stderr=base64.b64decode(bridge_result["stderr"].encode("ascii")),
            annotated_stderr=base64.b64decode(
                bridge_result["annotated_stderr"].encode("ascii"),
            ),
            violations=violations,
        )

    async def exec_file_operation(
        self,
        *,
        operation: str,
        path: str,
        cwd: str | None,
        tool_call_id: str | None,
        tool_name: str | None,
        recursive: bool = False,
        data: bytes | None = None,
        sample_size: int | None = None,
    ) -> SandboxCommandResult:
        """Execute one filesystem helper operation in the sandbox."""
        command = [
            sys.executable,
            _FILE_OPS_HELPER_PATH,
            operation,
            "--path",
            path,
        ]
        if recursive:
            command.append("--recursive")
        if sample_size is not None:
            command.extend(
                [
                    "--sample-size",
                    str(sample_size),
                ],
            )
        if data is not None:
            command.extend(
                [
                    "--data-b64",
                    base64.b64encode(data).decode("ascii"),
                ],
            )
        return await self.exec_command(
            command=command,
            cwd=cwd,
            timeout=None,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            command_text=f"{operation} {path}",
        )

    async def exec_file_read_stream(
        self,
        *,
        path: str,
        cwd: str | None,
        tool_call_id: str | None,
        tool_name: str | None,
    ) -> SandboxCommandStream:
        """Execute one streaming filesystem read operation in the sandbox."""
        command_id = tool_call_id or f"srt-{uuid.uuid4().hex}"
        payload = {
            "sandboxRuntimeModulePath": self._sandbox_runtime_module_path,
            "runtimeConfig": self._runtime_config,
            "command": shlex.join(
                [
                    sys.executable,
                    _FILE_OPS_HELPER_PATH,
                    "read-file",
                    "--path",
                    path,
                ],
            ),
            "cwd": cwd,
            "timeoutMs": None,
            "commandId": command_id,
            "commandText": f"read-file {path}",
            "streamStdout": True,
        }
        process = await asyncio.create_subprocess_exec(
            self._node_executable,
            self._bridge_path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdin is not None
        process.stdin.write(json.dumps(payload).encode("utf-8"))
        await process.stdin.drain()
        process.stdin.close()
        return SandboxCommandStream(
            process=process,
            stderr_task=asyncio.create_task(process.stderr.read()),
            command_id=command_id,
            tool_name=tool_name,
        )


def _decode_bridge_error(stderr: bytes) -> str:
    if not stderr:
        return "sandbox-runtime bridge failed"
    try:
        payload = json.loads(stderr.decode("utf-8"))
    except Exception:
        return stderr.decode("utf-8", errors="replace")
    message = payload.get("message")
    if isinstance(message, str):
        return message
    return str(payload)
