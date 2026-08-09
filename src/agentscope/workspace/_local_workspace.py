# -*- coding: utf-8 -*-
"""The local workspace class."""
import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import re
import shutil
from copy import deepcopy
from pathlib import Path
from typing import TypedDict

import aiofiles
import aiofiles.ospath
import frontmatter
from pydantic import AnyUrl

from ._base import (
    WorkspaceBase,
    DEFAULT_LOCAL_DIRECTORY_BOUNDARY_INSTRUCTIONS,
)
from ..mcp import MCPClient
from ..message import (
    TextBlock,
    DataBlock,
    ToolResultBlock,
    Msg,
    URLSource,
    Base64Source,
)
from ..skill import Skill
from ..tool import (
    ToolBase,
    CreateProjectDirectory,
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


class _SkillEntry(TypedDict):
    """A single entry in the .skills index file."""

    hash: str
    """SHA-256 hash of the skill's SKILL.md content."""
    skill_name: str
    """The name exposed to the agent (may differ from the directory name)."""


class _SkillsFile(TypedDict):
    """Schema of the .skills index file stored inside skills_dir."""

    skills_dir_mtime: float
    """mtime of skills_dir at the time the index was last written."""
    skills: dict[str, _SkillEntry]
    """Mapping from directory name (relative to skills_dir) to skill entry."""


def _sanitize_dir_name(name: str) -> str:
    """Sanitize a skill name into a safe directory name.

    Allowed characters: ASCII letters, digits, CJK unified ideographs,
    hyphens, and underscores. Everything else is replaced with ``_``.

    Args:
        name (`str`):
            The raw skill name from SKILL.md frontmatter.

    Returns:
        `str`:
            A sanitized string safe to use as a directory name on Windows,
            macOS, and Linux.
    """
    return re.sub(r"[^\w一-鿿-]", "_", name)


_DEFAULT_WORKSPACE_INSTRUCTIONS = f"""<workspace>
{DEFAULT_LOCAL_DIRECTORY_BOUNDARY_INSTRUCTIONS}

### Python Environment
- `uv` is recommended for managing and isolating Python environments per \
project:
```shell
uv venv && uv pip install ...
- Never install packages into a shared or global environment — each project \
must manage its own dependencies to avoid conflicts.
</workspace>"""


class LocalWorkspace(WorkspaceBase):
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
        default_mcps: list[MCPClient] | None = None,
        skill_paths: list[str] | None = None,
        instructions: str = _DEFAULT_WORKSPACE_INSTRUCTIONS,
    ) -> None:
        """Construct a :class:`LocalWorkspace`.

        Args:
            workdir (`str`):
                Filesystem path to the workspace root. Created on
                demand. Always resolved to an absolute path.
            workspace_id (`str | None`, optional):
                Existing workspace identifier to adopt. ``None``
                generates a fresh UUID.
            default_mcps (`list[MCPClient] | None`, optional):
                MCP clients seeded into a brand-new workspace.
                Ignored on subsequent restarts that already have a
                persisted ``<workdir>/.mcp`` file.
            skill_paths (`list[str] | None`, optional):
                Local skill directories seeded into
                ``<workdir>/skills`` on first :meth:`initialize`.
            instructions (`str`, defaults to \
            `_DEFAULT_WORKSPACE_INSTRUCTIONS`):
                System-prompt fragment template returned by
                :meth:`get_instructions`. Supports the ``{workdir}``
                placeholder.
        """
        super().__init__(workspace_id=workspace_id)

        # ── serializable config ─────────────────────────────────
        self.workdir = os.path.abspath(workdir)
        self.instructions = instructions.format(workdir=self.workdir)

        # ── seed-only ───────────────────────────────────────────
        self.default_mcps: list[MCPClient] = list(default_mcps or [])
        self.skill_paths: list[str] = list(skill_paths or [])

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

        failed: list[MCPClient] = []
        for mcp in mcps:
            if mcp.is_stateful and not mcp.is_connected:
                try:
                    await mcp.connect()
                except Exception as e:
                    logger.warning(
                        "Failed to connect stateful MCP '%s': %s, removing.",
                        mcp.name,
                        e,
                    )
                    failed.append(mcp)
        for mcp in failed:
            mcps.remove(mcp)

        self._agent_mcps[agent_id] = mcps
        await self._save_agent_mcp_file(agent_id)
        return mcps

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

    async def _ensure_agent_seeded(self, agent_id: str) -> None:
        """Seed default MCPs and skills once for a fresh agent namespace."""
        await self._ensure_agent_dirs(agent_id)

        async with self._mcp_lock:
            mcps = await self._load_agent_mcps(agent_id)
            if not mcps and self.default_mcps:
                seeded: list[MCPClient] = []
                for mcp in self.default_mcps:
                    model = MCPClient.model_validate(mcp.model_dump(mode="json"))
                    if model.is_stateful and not model.is_connected:
                        try:
                            await model.connect()
                        except Exception as e:
                            logger.warning(
                                "Failed to connect seeded MCP '%s': %s",
                                model.name,
                                e,
                            )
                            continue
                    seeded.append(model)
                self._agent_mcps[agent_id] = seeded
                await self._save_agent_mcp_file(agent_id)

        async with self._skill_lock:
            skills_dir = self._agent_skills_dir(agent_id)
            skills_file = await self._load_skills_file(skills_dir)
            if skills_file["skills"] or not self.skill_paths:
                return

            existing: dict[str, _SkillEntry] = skills_file["skills"]
            existing_hashes: set[str] = {e["hash"] for e in existing.values()}
            existing_agent_names: set[str] = {
                e["skill_name"] for e in existing.values()
            }
            existing_dir_names: set[str] = set(existing.keys())

            updated = False
            for skill_path in self.skill_paths:
                result = await self._validate_and_hash_skill(skill_path)
                if result is None:
                    continue

                _, raw_name, skill_hash = result
                if skill_hash in existing_hashes:
                    continue

                agent_name = raw_name
                counter = 1
                while agent_name in existing_agent_names:
                    agent_name = f"{raw_name} ({counter})"
                    counter += 1

                base_dir = _sanitize_dir_name(raw_name)
                dir_name = base_dir
                counter = 1
                while dir_name in existing_dir_names:
                    dir_name = f"{base_dir}_{counter}"
                    counter += 1

                dest_path = os.path.join(skills_dir, dir_name)
                if not os.path.realpath(dest_path).startswith(
                    os.path.realpath(skills_dir) + os.sep,
                ):
                    continue
                try:
                    await asyncio.to_thread(
                        shutil.copytree,
                        skill_path,
                        dest_path,
                        dirs_exist_ok=False,
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to seed skill '%s' from %s: %s",
                        raw_name,
                        skill_path,
                        str(e),
                    )
                    continue

                existing[dir_name] = {
                    "hash": skill_hash,
                    "skill_name": agent_name,
                }
                existing_hashes.add(skill_hash)
                existing_agent_names.add(agent_name)
                existing_dir_names.add(dir_name)
                updated = True

            if updated:
                skills_file["skills"] = existing
                skills_file["skills_dir_mtime"] = await aiofiles.ospath.getmtime(
                    skills_dir,
                )
                await self._save_skills_file(skills_dir, skills_file)

    async def _load_skills_file(self, skills_dir: str) -> _SkillsFile:
        """Load the .skills index file, returning an empty structure if absent.

        Args:
            skills_dir (`str`): The skills directory path.

        Returns:
            `_SkillsFile`: The parsed index, or a fresh empty structure.
        """
        path = os.path.join(skills_dir, ".skills")
        if not await aiofiles.ospath.exists(path):
            return {"skills_dir_mtime": 0.0, "skills": {}}

        try:
            async with aiofiles.open(path, "r", encoding="utf-8") as f:
                data = json.loads(await f.read())
            return _SkillsFile(
                skills_dir_mtime=float(data.get("skills_dir_mtime", 0.0)),
                skills=data.get("skills", {}),
            )
        except Exception as e:
            logger.warning("Failed to load .skills from %s: %s", path, str(e))
            return {"skills_dir_mtime": 0.0, "skills": {}}

    async def _save_skills_file(
        self,
        skills_dir: str,
        data: _SkillsFile,
    ) -> None:
        """Persist the .skills index file.

        Args:
            skills_dir (`str`): The skills directory path.
            data (`_SkillsFile`): The index to write.
        """
        path = os.path.join(skills_dir, ".skills")
        try:
            async with aiofiles.open(path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data, indent=2, ensure_ascii=False))
        except Exception as e:
            logger.warning("Failed to save .skills to %s: %s", path, str(e))

    async def _validate_skill(
        self,
        skill_path: str,
    ) -> tuple[str, str, str] | None:
        """Validate if a skill path contains a valid SKILL.md file.

        Args:
            skill_path (`str`):
                The path to the skill directory.

        Returns:
            `tuple[str, str, str] | None`:
                A tuple of (name, description, skill_md_content) if valid,
                None otherwise.
        """
        skill_md_path = os.path.join(skill_path, "SKILL.md")

        try:
            # Check if SKILL.md exists
            if not await aiofiles.ospath.isfile(skill_md_path):
                logger.warning(
                    "Invalid skill at %s: SKILL.md not found",
                    skill_path,
                )
                return None

            # Read and parse SKILL.md
            async with aiofiles.open(
                skill_md_path,
                "r",
                encoding="utf-8",
            ) as f:
                content_str = await f.read()

            # Parse frontmatter
            content = frontmatter.loads(content_str)
            name = content.get("name")
            description = content.get("description")

            if not name or not description:
                logger.warning(
                    "Invalid skill at %s: SKILL.md missing required "
                    "fields (name or description)",
                    skill_path,
                )
                return None

            return str(name), str(description), content_str

        except Exception as e:
            logger.warning(
                "Failed to validate skill at %s: %s",
                skill_path,
                str(e),
            )
            return None

    async def _validate_and_hash_skill(
        self,
        skill_path: str,
    ) -> tuple[str, str, str] | None:
        """Validate a skill and compute its hash.

        Args:
            skill_path (`str`):
                The path to the skill directory.

        Returns:
            `tuple[str, str, str] | None`:
                A tuple of (skill_path, skill_name, skill_hash) if valid,
                None otherwise.
        """
        validation_result = await self._validate_skill(skill_path)
        if validation_result is None:
            return None

        skill_name, _, skill_md_content = validation_result

        # Compute hash
        skill_hash = hashlib.sha256(
            skill_md_content.encode("utf-8"),
        ).hexdigest()

        return skill_path, skill_name, skill_hash

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
            CreateProjectDirectory(backend=backend),
        ]

    async def list_skills(self) -> list[Skill]:
        """List skills for the direct-use default agent namespace."""
        return await self._list_agent_skills(self._default_agent_id())

    async def _list_agent_skills(self, agent_id: str) -> list[Skill]:
        """List all skills available in the workspace.

        The method uses the .skills index for agent-facing names, compares the
        skills directory mtime to detect manual additions/removals since the
        last write, and reconciles the index when a change is found.

        Returns:
            `list[Skill]`:
                A list of Skill objects found in the workspace.
        """
        await self._ensure_agent_seeded(agent_id)
        skills_dir = self._agent_skills_dir(agent_id)
        async with self._skill_lock:
            if not await aiofiles.ospath.isdir(skills_dir):
                return []

            skills_file = await self._load_skills_file(skills_dir)
            current_mtime = await aiofiles.ospath.getmtime(skills_dir)

            # Detect if the skills directory has changed since last indexing
            if current_mtime != skills_file["skills_dir_mtime"]:
                skills_file = await self._reconcile_skills_dir(
                    skills_dir,
                    skills_file,
                    current_mtime,
                )

            # Load skills from disk using the index for the agent-facing name
            tasks = [
                self._load_single_skill(
                    os.path.join(skills_dir, dir_name),
                    entry["skill_name"],
                )
                for dir_name, entry in skills_file["skills"].items()
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            skills: list = []
            for dir_name, result in zip(skills_file["skills"], results):
                if isinstance(result, Exception):
                    logger.warning(
                        "Failed to load skill from %s: %s",
                        dir_name,
                        str(result),
                    )
                elif result is not None:
                    skills.append(result)

            return skills

    async def _reconcile_skills_dir(
        self,
        skills_dir: str,
        skills_file: _SkillsFile,
        current_mtime: float,
    ) -> _SkillsFile:
        """Reconcile the .skills index after the skills directory has changed.

        Handles:
        - Manually deleted subdirectories: removed from the index.
        - Manually added subdirectories: validated and added with conflict
          resolution for both directory name and agent-facing skill name.

        Args:
            skills_dir (`str`): Path to the skills directory.
            skills_file (`_SkillsFile`): The current (stale) index.
            current_mtime (`float`): The freshly-read mtime of skills_dir.

        Returns:
            `_SkillsFile`: The updated index (also persisted to disk).
        """
        existing: dict[str, _SkillEntry] = skills_file["skills"]
        original_mtime = skills_file["skills_dir_mtime"]

        # Collect actual subdirectories on disk
        def _list_dirs() -> set[str]:
            return {
                d
                for d in os.listdir(skills_dir)
                if os.path.isdir(os.path.join(skills_dir, d))
            }

        actual_dirs = await asyncio.to_thread(_list_dirs)
        indexed_dirs = set(existing.keys())

        updated = False

        # Remove entries for directories that no longer exist
        for removed in indexed_dirs - actual_dirs:
            logger.info(
                "Skill directory '%s' removed, updating index",
                removed,
            )
            del existing[removed]
            updated = True

        # Add entries for directories not yet in the index
        existing_agent_names: set[str] = {
            e["skill_name"] for e in existing.values()
        }
        existing_hashes: set[str] = {e["hash"] for e in existing.values()}

        for new_dir in actual_dirs - indexed_dirs:
            skill_path = os.path.join(skills_dir, new_dir)
            result = await self._validate_and_hash_skill(skill_path)
            if result is None:
                continue

            _, raw_name, skill_hash = result

            if skill_hash in existing_hashes:
                logger.info(
                    "Manually added skill '%s' already tracked by hash, "
                    "skipping",
                    new_dir,
                )
                continue

            agent_name = raw_name
            counter = 1
            while agent_name in existing_agent_names:
                agent_name = f"{raw_name} ({counter})"
                counter += 1

            entry: _SkillEntry = {"hash": skill_hash, "skill_name": agent_name}
            existing[new_dir] = entry
            existing_agent_names.add(agent_name)
            existing_hashes.add(skill_hash)
            updated = True
            logger.info(
                "Manually added skill '%s' indexed as agent name '%s'",
                new_dir,
                agent_name,
            )

        skills_file["skills"] = existing
        skills_file["skills_dir_mtime"] = current_mtime

        # Save if index changed OR if mtime needs updating
        # (mtime change without index change means non-skill files were
        # added/removed, we still need to record the new mtime to avoid
        # re-reconciling on every list_skills call)
        if updated or current_mtime != original_mtime:
            await self._save_skills_file(skills_dir, skills_file)

        return skills_file

    async def _load_single_skill(
        self,
        skill_dir: str,
        skill_name: str,
    ) -> Skill | None:
        """Load a single skill from disk using the agent-facing name from
        the index.

        Args:
            skill_dir (`str`):
                The skill directory path containing SKILL.md.
            skill_name (`str`):
                The agent-facing name stored in the .skills index.

        Returns:
            `Skill | None`:
                A Skill object or None if the SKILL.md is missing/invalid.
        """
        skill_md_path = os.path.join(skill_dir, "SKILL.md")

        try:
            if not await aiofiles.ospath.isfile(skill_md_path):
                return None

            async with aiofiles.open(
                skill_md_path,
                "r",
                encoding="utf-8",
            ) as f:
                content_str = await f.read()
                content = frontmatter.loads(content_str)

            description = content.get("description")
            if not description:
                logger.warning(
                    "SKILL.md in %s is missing 'description'. Skipping.",
                    skill_dir,
                )
                return None

            return Skill(
                name=skill_name,
                description=str(description),
                dir=skill_dir,
                markdown=content.content,
                content_hash=hashlib.sha256(
                    content_str.encode("utf-8"),
                ).hexdigest(),
            )

        except Exception as e:
            logger.warning(
                "Failed to load skill from %s: %s",
                skill_dir,
                str(e),
            )
            return None

    async def list_mcps(self) -> list[MCPClient]:
        """Return MCPs for the direct-use default agent namespace."""
        return await self._list_agent_mcps(self._default_agent_id())

    async def _list_agent_mcps(
        self,
        agent_id: str,
    ) -> list[MCPClient]:
        """Return MCPs scoped to one agent namespace."""
        await self._ensure_agent_seeded(agent_id)
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
            if mcp_client.is_stateful and not mcp_client.is_connected:
                await mcp_client.connect()
            mcps.append(mcp_client)
            self._agent_mcps[agent_id] = mcps
            await self._save_agent_mcp_file(agent_id)

    async def remove_mcp(self, name: str) -> None:
        """Remove an MCP from the direct-use default agent namespace."""
        await self._remove_agent_mcp(self._default_agent_id(), name)

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

    async def add_skill(self, skill_path: str) -> None:
        """Add a skill to the direct-use default agent namespace."""
        await self._add_agent_skill(self._default_agent_id(), skill_path)

    async def _add_agent_skill(
        self,
        agent_id: str,
        skill_path: str,
    ) -> None:
        """Add a skill to the workspace by copying from the given path.

        The skill directory must contain a valid ``SKILL.md`` file with
        ``name`` and ``description`` frontmatter fields.  Duplicate skills
        (identified by the SHA-256 hash of ``SKILL.md``) are silently skipped.
        Name and directory conflicts are resolved by appending a numeric
        suffix.

        Args:
            skill_path (`str`):
                Absolute or relative path to the skill directory to copy.

        Raises:
            ValueError: If the skill at ``skill_path`` is invalid (missing or
                malformed ``SKILL.md``).
        """
        skills_dir = self._agent_skills_dir(agent_id)
        async with self._skill_lock:
            os.makedirs(skills_dir, exist_ok=True)

            result = await self._validate_and_hash_skill(skill_path)
            if result is None:
                raise ValueError(
                    f"Invalid skill at {skill_path!r}: missing or malformed "
                    "SKILL.md (requires 'name' and 'description' fields).",
                )

            _, raw_name, skill_hash = result

            skills_file = await self._load_skills_file(skills_dir)
            existing: dict[str, _SkillEntry] = skills_file["skills"]

            existing_hashes: set[str] = {e["hash"] for e in existing.values()}
            if skill_hash in existing_hashes:
                logger.info(
                    "Skill '%s' (hash: %s...) already exists, skipping",
                    raw_name,
                    skill_hash[:8],
                )
                return

            existing_agent_names: set[str] = {
                e["skill_name"] for e in existing.values()
            }
            existing_dir_names: set[str] = set(existing.keys())

            # Resolve agent-facing name conflict
            agent_name = raw_name
            counter = 1
            while agent_name in existing_agent_names:
                agent_name = f"{raw_name} ({counter})"
                counter += 1

            # Resolve directory name conflict
            base_dir = _sanitize_dir_name(raw_name)
            dir_name = base_dir
            counter = 1
            while dir_name in existing_dir_names:
                dir_name = f"{base_dir}_{counter}"
                counter += 1

            dest_path = os.path.join(skills_dir, dir_name)

            if not os.path.realpath(dest_path).startswith(
                os.path.realpath(skills_dir) + os.sep,
            ):
                raise ValueError(
                    f"Skill path {skill_path!r} resolves outside skills_dir.",
                )

            await asyncio.to_thread(
                shutil.copytree,
                skill_path,
                dest_path,
                dirs_exist_ok=False,
            )

            logger.info(
                "Copied skill '%s' (agent name: '%s') from %s to %s",
                raw_name,
                agent_name,
                skill_path,
                dest_path,
            )

            existing[dir_name] = {"hash": skill_hash, "skill_name": agent_name}
            skills_file["skills"] = existing
            skills_file["skills_dir_mtime"] = await aiofiles.ospath.getmtime(
                skills_dir,
            )
            await self._save_skills_file(skills_dir, skills_file)

    async def remove_skill(self, name: str) -> None:
        """Remove a skill from the direct-use default agent namespace."""
        await self._remove_agent_skill(self._default_agent_id(), name)

    async def _remove_agent_skill(
        self,
        agent_id: str,
        name: str,
    ) -> None:
        """Remove a skill from the workspace by its agent-facing name.

        The skill directory is deleted from disk and the ``.skills`` index is
        updated.  If no skill with the given name is found, a warning is
        logged and the method returns without error.

        Args:
            name (`str`):
                The agent-facing name of the skill to remove (as stored in the
                ``.skills`` index, i.e. the ``name`` field from ``SKILL.md``
                possibly with a numeric suffix for de-duplication).
        """
        skills_dir = self._agent_skills_dir(agent_id)
        async with self._skill_lock:
            if not await aiofiles.ospath.isdir(skills_dir):
                logger.warning(
                    "Skills directory does not exist; cannot remove skill %r",
                    name,
                )
                return

            skills_file = await self._load_skills_file(skills_dir)
            existing: dict[str, _SkillEntry] = skills_file["skills"]

            target_dir: str | None = None
            for dir_name, entry in existing.items():
                if entry["skill_name"] == name:
                    target_dir = dir_name
                    break

            if target_dir is None:
                logger.warning("Skill %r not found in workspace", name)
                return

            skill_dir_path = os.path.join(skills_dir, target_dir)
            if await aiofiles.ospath.isdir(skill_dir_path):
                await asyncio.to_thread(shutil.rmtree, skill_dir_path)
                logger.info(
                    "Removed skill '%s' from %s",
                    name,
                    skill_dir_path,
                )
            else:
                logger.warning(
                    (
                        "Skill directory %r not found on disk; "
                        "removing index entry"
                    ),
                    skill_dir_path,
                )

            del existing[target_dir]
            skills_file["skills"] = existing
            skills_file["skills_dir_mtime"] = await aiofiles.ospath.getmtime(
                skills_dir,
            )
            await self._save_skills_file(skills_dir, skills_file)

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
