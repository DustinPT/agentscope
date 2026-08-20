# -*- coding: utf-8 -*-
"""SRT-backed local workspace."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import sys
import time
import uuid
from pathlib import Path
from typing import Any


from ..._logging import logger
from ...mcp import MCPClient
from .._gateway_client import GatewayClient
from .._local_workspace import LocalWorkspace
from .._utils import DEFAULT_WORKSPACE_INSTRUCTIONS
from ._srt_backend import SRTBackend


class SRTWorkspace(LocalWorkspace):
    """Local workspace whose builtin tools and stdio MCPs run under SRT."""

    def __init__(
        self,
        *,
        workdir: str,
        srt_settings_path: str,
        workspace_id: str | None = None,
        instructions: str = DEFAULT_WORKSPACE_INSTRUCTIONS,
        srt_executable: str = "srt",
        service_host: str = "127.0.0.1",
        service_port: int | None = None,
        service_token: str | None = None,
        startup_timeout: float = 30.0,
    ) -> None:
        super().__init__(
            workdir=workdir,
            workspace_id=workspace_id,
            instructions=instructions,
        )
        self.instructions = instructions.format(
            backend="SRT-based local sandbox",
            workdir=self.workdir,
        )
        self.srt_settings_path = os.path.abspath(srt_settings_path)
        self.srt_executable = srt_executable
        self.service_host = service_host
        self.service_port = service_port
        self.service_token = service_token or uuid.uuid4().hex
        self.startup_timeout = startup_timeout

        self._gateway: GatewayClient | None = None
        self._loaded_agent_namespaces: set[str] = set()
        self._runtime_process: asyncio.subprocess.Process | None = None
        self._runtime_log_path = os.path.join(self.workdir, "srt-runtime.log")
        self._runtime_log_fp: Any = None
        self._backend = None

    async def initialize(self) -> None:
        """Start the sandboxed local runtime service."""
        if self.is_alive:
            return

        os.makedirs(self.workdir, exist_ok=True)
        for subdir in ("agents", "sessions", "data", "projects", "scratch"):
            os.makedirs(os.path.join(self.workdir, subdir), exist_ok=True)

        if not os.path.isfile(self.srt_settings_path):
            raise FileNotFoundError(
                f"SRT settings file not found: {self.srt_settings_path}",
            )

        port = self.service_port or _allocate_local_port(self.service_host)
        self.service_port = port
        base_url = f"http://{self.service_host}:{port}"

        await self._start_runtime_process(port)
        self._backend = SRTBackend(
            base_url=base_url,
            token=self.service_token,
            workdir=self.workdir,
            timeout=self.startup_timeout,
        )
        self._gateway = GatewayClient(
            base_url=base_url,
            token=self.service_token,
            timeout=self.startup_timeout,
        )

        try:
            await self._wait_for_runtime_ready()
        except Exception:
            await self.close()
            raise

        self._loaded_agent_namespaces = set()
        self.is_alive = True

    async def reset(self) -> None:
        """Reset persisted workspace state while keeping the runtime alive."""
        async with self._mcp_lock, self._skill_lock:
            if self._gateway is not None:
                for namespace in list(self._loaded_agent_namespaces):
                    for gw_client in await self._gateway.list_mcps(
                        namespace=namespace,
                    ):
                        try:
                            await gw_client.close()
                        except Exception as exc:
                            logger.warning(
                                "SRTWorkspace MCP %r close failed during reset: %s",
                                gw_client.name,
                                exc,
                            )
            self._loaded_agent_namespaces = set()

            for subdir in ("agents", "sessions", "data", "projects", "scratch"):
                path = os.path.join(self.workdir, subdir)
                if os.path.isdir(path):
                    await asyncio.to_thread(shutil.rmtree, path)

    async def close(self) -> None:
        """Stop the runtime process and release host-side clients."""
        if self._gateway is not None:
            try:
                await self._gateway.aclose()
            except Exception:
                pass
            self._gateway = None

        if isinstance(self._backend, SRTBackend):
            try:
                await self._backend.aclose()
            except Exception:
                pass

        await self._shutdown_runtime_process()

        self._backend = None
        self._loaded_agent_namespaces = set()
        self.is_alive = False

    async def _start_runtime_process(self, port: int) -> None:
        """Launch the sandboxed local runtime service process."""
        srt_executable = _resolve_srt_executable(self.srt_executable)
        os.makedirs(os.path.dirname(self._runtime_log_path), exist_ok=True)
        self._runtime_log_fp = open(self._runtime_log_path, "ab")
        cmd = [
            srt_executable,
            "--settings",
            self.srt_settings_path,
            sys.executable,
            "-m",
            "agentscope.workspace._srt._local_runtime_service",
            "--host",
            self.service_host,
            "--port",
            str(port),
            "--token",
            self.service_token,
        ]
        self._runtime_process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self.workdir,
            stdout=self._runtime_log_fp,
            stderr=self._runtime_log_fp,
        )

    async def _wait_for_runtime_ready(self) -> None:
        """Wait until the local runtime service reports healthy."""
        assert self._gateway is not None
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if await self._gateway.health():
                return
            if (
                self._runtime_process is not None
                and self._runtime_process.returncode is not None
            ):
                raise RuntimeError(
                    "SRT local runtime exited before becoming ready:\n"
                    f"{_read_log_tail(self._runtime_log_path)}",
                )
            await asyncio.sleep(0.2)
        raise TimeoutError(
            "Timed out waiting for SRT local runtime to become ready.\n"
            f"{_read_log_tail(self._runtime_log_path)}",
        )

    async def _shutdown_runtime_process(self) -> None:
        """Stop the runtime process and close the log handle."""
        process = self._runtime_process
        self._runtime_process = None
        if process is not None:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            else:
                await process.wait()

        if self._runtime_log_fp is not None:
            self._runtime_log_fp.close()
            self._runtime_log_fp = None

    async def _ensure_agent_namespace_loaded(self, agent_id: str) -> None:
        """Restore one agent namespace's persisted MCPs into the runtime."""
        if agent_id in self._loaded_agent_namespaces:
            return
        await self._ensure_agent_dirs(agent_id)
        specs: list[dict[str, Any]] = []
        mcp_file = self._agent_mcp_file(agent_id)
        if os.path.isfile(mcp_file):
            try:
                with open(mcp_file, encoding="utf-8") as file_obj:
                    specs = json.load(file_obj)
            except Exception as exc:
                logger.warning(
                    "SRTWorkspace failed to read %s: %s",
                    mcp_file,
                    exc,
                )

        assert self._gateway is not None
        for spec in specs:
            client = self._gateway.make_client(spec, namespace=agent_id)
            await client.connect()
            if client.connection_status != "connected":
                logger.warning(
                    "SRTWorkspace restored MCP %r in %s state: %s",
                    spec.get("name", "?"),
                    client.connection_status,
                    client.connection_error,
                )
        if not specs:
            await self._save_agent_mcp_file(agent_id)
        self._loaded_agent_namespaces.add(agent_id)

    async def _save_agent_mcp_file(self, agent_id: str) -> None:
        """Persist one agent namespace's registered MCPs to ``.mcp``."""
        assert self._gateway is not None
        mcp_file = self._agent_mcp_file(agent_id)
        os.makedirs(os.path.dirname(mcp_file), exist_ok=True)
        try:
            mcps = await self._gateway.list_mcps(namespace=agent_id)
            with open(mcp_file, "w", encoding="utf-8") as file_obj:
                json.dump(
                    [mcp.model_dump(mode="json") for mcp in mcps],
                    file_obj,
                    indent=2,
                    ensure_ascii=False,
                )
        except Exception as exc:
            logger.warning("Failed to save .mcp to %s: %s", mcp_file, exc)

    async def list_mcps(self) -> list[MCPClient]:
        """Return MCPs for the default agent namespace."""
        return await self._list_agent_mcps(self._default_agent_id())

    async def _list_agent_mcps(
        self,
        agent_id: str,
    ) -> list[MCPClient]:
        await self._ensure_agent_namespace_loaded(agent_id)
        assert self._gateway is not None
        return await self._gateway.list_mcps(namespace=agent_id)

    async def add_mcp(self, mcp_client: MCPClient) -> None:
        """Add an MCP to the default agent namespace."""
        await self._add_agent_mcp(self._default_agent_id(), mcp_client)

    async def _add_agent_mcp(
        self,
        agent_id: str,
        mcp_client: MCPClient,
    ) -> None:
        async with self._mcp_lock:
            await self._ensure_agent_namespace_loaded(agent_id)
            current = await self._list_agent_mcps(agent_id)
            if any(client.name == mcp_client.name for client in current):
                raise ValueError(
                    f"MCP {mcp_client.name!r} already exists in workspace.",
                )
            assert self._gateway is not None
            gw_client = self._gateway.make_client(
                mcp_client.model_dump(mode="json"),
                namespace=agent_id,
            )
            await gw_client.connect()
            if gw_client.connection_status != "connected":
                logger.warning(
                    "SRTWorkspace added MCP %r in %s state: %s",
                    gw_client.name,
                    gw_client.connection_status,
                    gw_client.connection_error,
                )
            await self._save_agent_mcp_file(agent_id)

    async def remove_mcp(self, name: str) -> None:
        """Remove an MCP from the default agent namespace."""
        await self._remove_agent_mcp(self._default_agent_id(), name)

    async def reconnect_mcp(self, name: str) -> MCPClient:
        """Reconnect an MCP from the default agent namespace."""
        return await self._reconnect_agent_mcp(self._default_agent_id(), name)

    async def _remove_agent_mcp(self, agent_id: str, name: str) -> None:
        async with self._mcp_lock:
            await self._ensure_agent_namespace_loaded(agent_id)
            current = await self._list_agent_mcps(agent_id)
            gw_client = next((client for client in current if client.name == name), None)
            if gw_client is None:
                logger.warning("MCP %r not found in workspace", name)
                return
            try:
                await gw_client.close()
            except Exception as exc:
                logger.warning("MCP %r close failed: %s", name, exc)
            await self._save_agent_mcp_file(agent_id)

    async def _reconnect_agent_mcp(
        self,
        agent_id: str,
        name: str,
    ) -> MCPClient:
        async with self._mcp_lock:
            await self._ensure_agent_namespace_loaded(agent_id)
            current = await self._list_agent_mcps(agent_id)
            gw_client = next((client for client in current if client.name == name), None)
            if gw_client is None:
                raise ValueError(f"MCP {name!r} not found in workspace.")
            await gw_client.reconnect()
            await self._save_agent_mcp_file(agent_id)
            return gw_client


def _allocate_local_port(host: str) -> int:
    """Allocate a currently free localhost TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def _resolve_srt_executable(executable: str) -> str:
    """Resolve an SRT executable path or raise a clear error."""
    if os.path.isabs(executable):
        if os.path.isfile(executable) and os.access(executable, os.X_OK):
            return executable
        raise FileNotFoundError(f"SRT executable not found or not executable: {executable}")

    resolved = shutil.which(executable)
    if resolved:
        return resolved
    raise FileNotFoundError(
        f"SRT executable {executable!r} not found in PATH. "
        "Install @anthropic-ai/sandbox-runtime first.",
    )


def _read_log_tail(path: str, max_chars: int = 4000) -> str:
    """Read the tail of the runtime log for diagnostics."""
    if not os.path.isfile(path):
        return "<runtime log is empty>"
    data = Path(path).read_text(encoding="utf-8", errors="replace")
    return data[-max_chars:] or "<runtime log is empty>"
