# -*- coding: utf-8 -*-
"""The local workspace class."""
import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import shutil
from copy import deepcopy
from pathlib import Path

import aiofiles
import aiofiles.ospath
from pydantic import AnyUrl

from ._base import WorkspaceBase
from ._skill_index import SkillIndexMixin
from ._utils import DEFAULT_WORKSPACE_INSTRUCTIONS
from ..mcp import MCPClient
from ..message import (
    TextBlock,
    DataBlock,
    ToolResultBlock,
    Msg,
    URLSource,
    Base64Source,
)
from ..tool import (
    ToolBase,
    Edit,
    Glob,
    Grep,
    Read,
    WebFetch,
    WebSearch,
    Write,
)
from ..tool._builtin._backend import LocalBackend
from ..tool._builtin._powershell import PowerShell
from .._logging import logger



class LocalWorkspace(SkillIndexMixin, WorkspaceBase):
    # pylint: disable=line-too-long
    """Local-directory workspace.

    Layout::

        {workdir}/
        ├── agents/       # per-agent MCP / skill / asset directories
        ├── data/         # offloaded multimodal files
        └── sessions/     # per-session context and tool-result files
    """  # noqa: E501

    def __init__(
        self,
        *,
        workdir: str,
        workspace_id: str | None = None,
        instructions: str = DEFAULT_WORKSPACE_INSTRUCTIONS,
    ) -> None:
        """Construct a :class:`LocalWorkspace`.

        Args:
            workdir (`str`):
                Filesystem path to the workspace root. Created on
                demand. Always resolved to an absolute path.
            workspace_id (`str | None`, optional):
                Existing workspace identifier to adopt. ``None``
                generates a fresh UUID.
            instructions (`str`, defaults to \
            `_DEFAULT_WORKSPACE_INSTRUCTIONS`):
                System-prompt fragment template returned by
                :meth:`get_instructions`. Supports the ``{workdir}``
                placeholder.
        """
        super().__init__(workspace_id=workspace_id)

        # ── serializable config ─────────────────────────────────
        self.workdir = os.path.abspath(workdir)
        self.instructions = instructions.format(
            backend="local",
            workdir=self.workdir,
        )

        # ── runtime state ───────────────────────────────────────
        self._backend = LocalBackend()
        self._agent_mcps: dict[str, list[MCPClient]] = {}

        self._skill_lock = asyncio.Lock()
        self._mcp_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Initialise the workspace.

        Prepares only the shared filesystem root. Agent-scoped MCP and
        skill resources are restored lazily when an
        :class:`AgentWorkspaceView` binds to this runtime.
        """
        if self.is_alive:
            return

        os.makedirs(self.workdir, exist_ok=True)
        for subdir in ("agents", "sessions", "data", "projects"):
            os.makedirs(os.path.join(self.workdir, subdir), exist_ok=True)

        self.is_alive = True

    async def get_instructions(self) -> str:
        """Get the workspace instructions."""
        return self.instructions

    def _default_agent_id(self) -> str:
        """Fallback agent namespace for direct workspace usage."""
        return self.workspace_id

    def _agent_resource_root(self, agent_id: str) -> str:
        """Return the agent-scoped resource root."""
        return os.path.join(self.workdir, "agents", agent_id)

    def _agent_mcp_file(self, agent_id: str) -> str:
        """Return the agent-scoped MCP persistence file."""
        return os.path.join(self._agent_resource_root(agent_id), ".mcp")

    def _agent_skills_dir(self, agent_id: str) -> str:
        """Return the agent-scoped skills directory."""
        return os.path.join(self._agent_resource_root(agent_id), "skills")

    def _agent_mcps_dir(self, agent_id: str) -> str:
        """Return the agent-scoped MCP asset directory."""
        return os.path.join(self._agent_resource_root(agent_id), "mcps")

    async def _ensure_agent_dirs(self, agent_id: str) -> None:
        """Ensure the agent-scoped resource directories exist."""
        resource_root = self._agent_resource_root(agent_id)
        os.makedirs(resource_root, exist_ok=True)
        os.makedirs(self._agent_skills_dir(agent_id), exist_ok=True)
        os.makedirs(self._agent_mcps_dir(agent_id), exist_ok=True)

    async def _load_agent_mcps(self, agent_id: str) -> list[MCPClient]:
        """Load and cache one agent's MCP client list."""
        cached = self._agent_mcps.get(agent_id)
        if cached is not None:
            return cached

        await self._ensure_agent_dirs(agent_id)
        mcp_file = self._agent_mcp_file(agent_id)
        mcps: list[MCPClient] = []
        if await aiofiles.ospath.exists(mcp_file):
            try:
                async with aiofiles.open(
                    mcp_file,
                    "r",
                    encoding="utf-8",
                ) as f:
                    raw_content = await f.read()
                raw_list = json.loads(raw_content) if raw_content.strip() else []
                for item in raw_list:
                    try:
                        mcps.append(MCPClient.model_validate(item))
                    except Exception as e:
                        logger.warning(
                            "Skipping invalid MCP entry '%s': %s",
                            item.get("name", "?"),
                            e,
                        )
            except Exception as e:
                logger.warning(
                    "Failed to load .mcp from %s: %s. Resetting to empty.",
                    mcp_file,
                    str(e),
                )

        for mcp in mcps:
            try:
                await self._activate_mcp(mcp)
            except Exception as e:
                logger.warning(
                    "Failed to activate MCP '%s': %s",
                    mcp.name,
                    e,
                )

        self._agent_mcps[agent_id] = mcps
        await self._save_agent_mcp_file(agent_id)
        return mcps

    @staticmethod
    async def _activate_mcp(mcp: MCPClient) -> None:
        """Probe one MCP and retain runtime failure state on error."""
        await mcp.warmup()

    async def _save_agent_mcp_file(self, agent_id: str) -> None:
        """Persist one agent's MCP client list to its own ``.mcp`` file."""
        await self._ensure_agent_dirs(agent_id)
        mcp_file = self._agent_mcp_file(agent_id)
        try:
            async with aiofiles.open(mcp_file, "w", encoding="utf-8") as f:
                await f.write(
                    json.dumps(
                        [
                            m.model_dump()
                            for m in self._agent_mcps.get(agent_id, [])
                        ],
                        indent=2,
                        ensure_ascii=False,
                    ),
                )
        except Exception as e:
            logger.warning("Failed to save .mcp to %s: %s", mcp_file, str(e))

    async def _offload_data_block(self, data_block: DataBlock) -> DataBlock:
        """Offload the data block by persisting it as local files. This avoids
        embedding large base64-encoded data directly in the offload files,
        keeping them lightweight and readable.

        Args:
            data_block (`DataBlock`):
                The data block with base64 source.

        Returns:
            `DataBlock`:
                A new data block with the same metadata but with the source
                replaced by the local file path where the data is stored.
        """
        if isinstance(data_block.source, URLSource):
            return data_block

        # Use the full SHA-256 hex digest (256-bit) as the filename stem.
        # A full hash collision is computationally infeasible, so an existing
        # file with the same name is guaranteed to have identical content —
        # no need to read and compare bytes.
        hash_str = hashlib.sha256(data_block.source.data.encode()).hexdigest()
        ext = mimetypes.guess_extension(data_block.source.media_type) or ".bin"
        path = os.path.join(self.workdir, "data", f"{hash_str}{ext}")

        # Reuse the existing file directly — same hash ⟹ same content.
        if not await aiofiles.ospath.exists(path):
            # Write decoded bytes to disk and return a URL-source DataBlock.
            os.makedirs(os.path.dirname(path), exist_ok=True)
            async with aiofiles.open(path, "wb") as f:
                await f.write(base64.b64decode(data_block.source.data))

        return DataBlock(
            id=data_block.id,
            name=data_block.name,
            source=URLSource(
                url=AnyUrl(Path(path).as_uri()),
                media_type=data_block.source.media_type,
            ),
        )

    async def offload_context(
        self,
        session_id: str,
        msgs: list[Msg],
    ) -> str:
        """Offload the compressed messages into the local directory for
        further processing.

        Args:
            session_id (`str`):
                The session id.
            msgs (`list[Msg]`):
                The messages to offload.

        Returns:
            `str`:
                The file path to the offloaded message.
        """
        path = os.path.join(
            self.workdir,
            "sessions",
            session_id,
            "context.jsonl",
        )

        copied_msgs = deepcopy(msgs)
        msgs_strs = []
        for msg in copied_msgs:
            if not isinstance(msg.content, str):
                content = []
                for block in msg.content:
                    if isinstance(block, DataBlock) and isinstance(
                        block.source,
                        Base64Source,
                    ):
                        content.append(await self._offload_data_block(block))
                    else:
                        content.append(block)
                msg.content = content
            msgs_strs.append(msg.model_dump_json())

        msgs_str = "\n".join(msgs_strs)
        # Create parent directory if it doesn't exist
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Offload the context into the local file
        # Always end with a newline to ensure proper JSONL format when
        # appending
        async with aiofiles.open(
            path,
            mode="a",
            encoding="utf-8",
        ) as file:
            await file.write(msgs_str + "\n")
        return path

    async def offload_tool_result(
        self,
        session_id: str,
        tool_result: ToolResultBlock,
    ) -> str:
        """Offload the tool results into the local directory for agentic
        retrieval.

        Args:
            session_id (`str`):
                The session id.
            tool_result (`ToolResultBlock`):
                The tool result.

        Returns:
            `str`:
                The file path to the offloaded tool results.
        """
        path = os.path.join(
            self.workdir,
            "sessions",
            session_id,
            f"tool_result-{tool_result.id}.txt",
        )

        # Avoid filename conflict
        index = 1
        while os.path.exists(path):
            path = os.path.join(
                self.workdir,
                "sessions",
                session_id,
                f"tool_result-{tool_result.id}({index}).txt",
            )
            index += 1

        res_strs = []
        if isinstance(tool_result.output, str):
            res_strs.append(tool_result.output)
        else:
            for block in tool_result.output:
                if isinstance(block, TextBlock):
                    res_strs.append(block.text)
                elif isinstance(block, DataBlock):
                    if isinstance(block.source, Base64Source):
                        data_block = await self._offload_data_block(block)
                        url = data_block.source.url
                    else:
                        url = block.source.url
                    res_strs.append(
                        f"<data url='{url}' name='{block.name}' "
                        f"media_type='{block.source.media_type}'/>",
                    )

        # Create parent directory if it doesn't exist
        os.makedirs(os.path.dirname(path), exist_ok=True)
        async with aiofiles.open(path, mode="w", encoding="utf-8") as file:
            await file.write("".join(res_strs))

        return path

    async def close(self) -> None:
        """Close every stateful MCP attached to this workspace.

        ``LocalWorkspace`` itself owns no resources (the workdir is
        the persistence layer and is left untouched), but stdio /
        stateful HTTP MCPs hold long-lived sessions that have to be
        closed explicitly. Stateless HTTP MCPs are skipped — they
        spin up an ad-hoc session per call and have nothing to close.
        """
        async with self._mcp_lock:
            for mcps in self._agent_mcps.values():
                for mcp in mcps:
                    if mcp.is_stateful and mcp.is_connected:
                        try:
                            await mcp.close()
                        except Exception as e:
                            logger.warning(
                                (
                                    "Failed to close MCP %r "
                                    "when closing local workspace: %s"
                                ),
                                mcp.name,
                                e,
                            )
        self.is_alive = False

    async def reset(self) -> None:
        """Return the workspace to an empty state.

        Closes and drops all agent-scoped MCPs and deletes
        ``agents/``, ``sessions/``, ``data/``, and ``projects/``.
        """
        async with self._mcp_lock:
            for mcps in self._agent_mcps.values():
                for mcp in mcps:
                    if mcp.is_stateful and mcp.is_connected:
                        try:
                            await mcp.close()
                        except Exception as e:
                            logger.warning(
                                "MCP %r close failed during reset: %s",
                                mcp.name,
                                e,
                            )
            self._agent_mcps = {}

        async with self._skill_lock:
            path = os.path.join(self.workdir, "agents")
            if await aiofiles.ospath.isdir(path):
                await asyncio.to_thread(shutil.rmtree, path)

        for sub in ("sessions", "data", "projects"):
            path = os.path.join(self.workdir, sub)
            if await aiofiles.ospath.isdir(path):
                await asyncio.to_thread(shutil.rmtree, path)

    async def list_tools(self) -> list[ToolBase]:
        """Return builtin tools, using PowerShell as the shell on Windows."""
        if os.name != "nt":
            return await super().list_tools()

        backend = self.get_backend()
        glob_kwargs: dict = {"backend": backend}
        if self._glob_helper_path is not None:
            glob_kwargs["glob_helper_path"] = self._glob_helper_path
        return [
            PowerShell(cwd=self.workdir, backend=backend),
            Edit(backend=backend),
            Glob(**glob_kwargs),
            Grep(backend=backend),
            Read(backend=backend),
            WebSearch(backend=backend),
            WebFetch(backend=backend),
            Write(backend=backend),
        ]

    async def list_mcps(self) -> list[MCPClient]:
        """Return MCPs for the direct-use default agent namespace."""
        return await self._list_agent_mcps(self._default_agent_id())

    async def _list_agent_mcps(
        self,
        agent_id: str,
    ) -> list[MCPClient]:
        """Return MCPs scoped to one agent namespace."""
        return await self._load_agent_mcps(agent_id)

    async def _save_mcp_file(self) -> None:
        """Persist MCPs for the direct-use default agent namespace."""
        await self._save_agent_mcp_file(self._default_agent_id())

    async def add_mcp(self, mcp_client: MCPClient) -> None:
        """Add an MCP to the direct-use default agent namespace."""
        await self._add_agent_mcp(self._default_agent_id(), mcp_client)

    async def _add_agent_mcp(
        self,
        agent_id: str,
        mcp_client: MCPClient,
    ) -> None:
        """Add an MCP client, connect it if stateful, and persist.

        Args:
            mcp_client: The MCP client to add.
        """
        async with self._mcp_lock:
            mcps = await self._load_agent_mcps(agent_id)
            if any(existing.name == mcp_client.name for existing in mcps):
                raise ValueError(
                    f"MCP client {mcp_client.name!r} already exists in workspace",
                )
            mcps.append(mcp_client)
            self._agent_mcps[agent_id] = mcps
            try:
                await self._activate_mcp(mcp_client)
            except Exception as e:
                logger.warning(
                    "Failed to activate MCP '%s' on add: %s",
                    mcp_client.name,
                    e,
                )
            await self._save_agent_mcp_file(agent_id)

    async def remove_mcp(self, name: str) -> None:
        """Remove an MCP from the direct-use default agent namespace."""
        await self._remove_agent_mcp(self._default_agent_id(), name)

    async def reconnect_mcp(self, name: str) -> MCPClient:
        """Reconnect an MCP in the direct-use default agent namespace."""
        return await self._reconnect_agent_mcp(self._default_agent_id(), name)

    async def _remove_agent_mcp(
        self,
        agent_id: str,
        name: str,
    ) -> None:
        """Remove an MCP client by name, disconnecting it if stateful.

        Args:
            name: The ``name`` field of the client to remove.
        """
        async with self._mcp_lock:
            mcps = await self._load_agent_mcps(agent_id)
            for i, mcp in enumerate(mcps):
                if mcp.name == name:
                    if mcp.is_stateful and mcp.is_connected:
                        await mcp.close()
                    mcps.pop(i)
                    self._agent_mcps[agent_id] = mcps
                    await self._save_agent_mcp_file(agent_id)
                    return
        logger.warning("MCP client %r not found in workspace", name)

    async def _reconnect_agent_mcp(
        self,
        agent_id: str,
        name: str,
    ) -> MCPClient:
        """Reconnect one MCP client by name and retain failures."""
        async with self._mcp_lock:
            mcps = await self._load_agent_mcps(agent_id)
            for mcp in mcps:
                if mcp.name != name:
                    continue
                if mcp.is_stateful and mcp.is_connected:
                    await mcp.close(ignore_errors=True)
                try:
                    await self._activate_mcp(mcp)
                except Exception as e:
                    logger.warning(
                        "Failed to reconnect MCP '%s': %s",
                        mcp.name,
                        e,
                    )
                self._agent_mcps[agent_id] = mcps
                await self._save_agent_mcp_file(agent_id)
                return mcp
        raise ValueError(f"MCP client {name!r} not found in workspace")

    async def sync_mcp_asset(
        self,
        name: str,
        source_dir: str,
        content_hash: str,
    ) -> tuple[str, bool]:
        """Sync an MCP asset for the direct-use default agent namespace."""
        return await self._sync_agent_mcp_asset(
            self._default_agent_id(),
            name,
            source_dir,
            content_hash,
        )

    async def _sync_agent_mcp_asset(
        self,
        agent_id: str,
        name: str,
        source_dir: str,
        content_hash: str,
    ) -> tuple[str, bool]:
        """Copy one MCP asset directory into ``mcps/`` under the workspace."""
        target_root = self._agent_mcps_dir(agent_id)
        target_dir = os.path.join(target_root, name)
        marker_file = os.path.join(target_dir, ".agentscope_asset_hash")
        async with self._mcp_lock:
            os.makedirs(target_root, exist_ok=True)
            if await aiofiles.ospath.isdir(target_dir) and await aiofiles.ospath.isfile(
                marker_file,
            ):
                async with aiofiles.open(
                    marker_file,
                    "r",
                    encoding="utf-8",
                ) as f:
                    existing_hash = (await f.read()).strip()
                if existing_hash == content_hash:
                    return target_dir, False
            if await aiofiles.ospath.isdir(target_dir):
                await asyncio.to_thread(shutil.rmtree, target_dir)
            await asyncio.to_thread(
                shutil.copytree,
                source_dir,
                target_dir,
                dirs_exist_ok=False,
            )
            async with aiofiles.open(
                marker_file,
                "w",
                encoding="utf-8",
            ) as f:
                await f.write(content_hash)
        return target_dir, True

    async def remove_mcp_asset(self, name: str) -> None:
        """Remove an MCP asset from the direct-use default agent namespace."""
        await self._remove_agent_mcp_asset(self._default_agent_id(), name)

    async def _remove_agent_mcp_asset(
        self,
        agent_id: str,
        name: str,
    ) -> None:
        """Delete one MCP asset directory from ``mcps/``."""
        target_dir = os.path.join(self._agent_mcps_dir(agent_id), name)
        async with self._mcp_lock:
            if await aiofiles.ospath.isdir(target_dir):
                await asyncio.to_thread(shutil.rmtree, target_dir)

    async def list_mcp_asset_names(self) -> list[str]:
        """List MCP assets for the direct-use default agent namespace."""
        return await self._list_agent_mcp_asset_names(
            self._default_agent_id(),
        )

    async def _list_agent_mcp_asset_names(
        self,
        agent_id: str,
    ) -> list[str]:
        """List MCP asset directory names from ``mcps/``."""
        target_root = self._agent_mcps_dir(agent_id)
        async with self._mcp_lock:
            if not await aiofiles.ospath.isdir(target_root):
                return []
            entries = await asyncio.to_thread(os.listdir, target_root)
            results: list[str] = []
            for entry in entries:
                entry_path = os.path.join(target_root, entry)
                if await aiofiles.ospath.isdir(entry_path):
                    results.append(entry)
            return sorted(results)
