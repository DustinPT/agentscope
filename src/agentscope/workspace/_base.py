# -*- coding: utf-8 -*-
"""WorkspaceBase — abstract interface for agent workspaces.

A workspace provides:

- **Resources** — skills available to the agent.
- **Tools** — MCPs and built-in tools for operating on resources.
- **Offload** — persistence of compressed context and tool results
  for agentic retrieval.

Three concrete implementations:

- `LocalWorkspace` — local filesystem.
- `DockerWorkspace` — Docker container.
- `E2BWorkspace` — E2B cloud sandbox.

Consumers:

- **Agent** — calls ``list_mcps``, ``list_skills``, ``list_tools``,
  ``offload_context``, ``offload_tool_result``.
- **User** — dynamically adds/removes MCPs and skills via
  ``add_mcp``, ``remove_mcp``, ``add_skill``, ``remove_skill``.
- **Developer** — manages lifecycle via ``initialize`` / ``close``.
"""

import os
import uuid
from abc import abstractmethod
from typing import Self

from ..mcp import MCPClient
from ..message import Msg, ToolResultBlock
from ..skill import Skill
from ..tool import BackendBase, ToolBase

DEFAULT_LOCAL_DIRECTORY_BOUNDARY_INSTRUCTIONS = """### Local Directory Boundary
1. Only treat a local path as a user directory when the user has explicitly provided it or explicitly authorized checking it.
2. The system's default working directory exists only to run the agent or tools and is not the same as a user directory.
3. Do not proactively scan local files merely because a system default working directory exists, unless the user has explicitly authorized it.

### Project Directory Discipline
1. If the user needs file operations and has not provided a project directory, call `CreateProjectDirectory` before creating, downloading, extracting, editing, or generating task files.
2. After `CreateProjectDirectory` returns a directory, treat that directory as the canonical project root for the current task.
3. Unless the user explicitly provides or approves another path, keep all task files under that project directory, including source files, generated artifacts, intermediate files, temporary work files, scripts, and downloaded assets.
4. Do not place task files in `/tmp`, the process current working directory, or arbitrary workspace root locations just because they are convenient.
5. If a tool or command must briefly use an OS temp location, move the resulting files back into the project directory before presenting them as task outputs or continuing later file operations.
6. When using shell commands for task files, prefer running them in the project directory or with explicit paths rooted in that project directory."""


class WorkspaceBase:
    """Abstract base class for all workspace implementations.

    Subclasses provide concrete behaviour for one execution backend
    (local filesystem, Docker container, E2B sandbox). The base class
    only fixes the lifecycle contract (``initialize`` / ``close`` /
    ``reset``), the ``async with`` protocol, and the discovery /
    offload / add-remove method signatures consumed by ``Agent`` and
    by the workspace manager layer.

    State held on the base class is intentionally minimal:
    ``workspace_id`` (stable identifier, generated if not given) and
    ``is_alive`` (lifecycle flag). All backend-specific state lives on
    the subclass.
    """

    workspace_id: str
    """Unique identifier for this workspace instance."""

    workdir: str
    """Agent-visible root directory for workspace file operations."""

    is_alive: bool
    """If the workspace is still operational."""

    _backend: BackendBase | None
    """Current execution backend for builtin tools."""

    @property
    def _glob_helper_path(self) -> str | None:
        """Optional backend-side path to the glob helper script."""
        return None

    @property
    def resource_root(self) -> str:
        """Agent-visible resource root for MCP / skill assets.

        Default implementation points at ``workdir``. Agent-scoped
        workspace views override this to expose ``agents/<agent_id>``
        while still keeping ``workdir`` bound to the shared filesystem
        root used by builtin file tools.
        """
        return self.workdir

    def mcp_asset_runtime_dir(self, name: str) -> str:
        """Return the runtime directory of one MCP asset."""
        return os.path.join(self.resource_root, "mcps", name)

    def __init__(self, workspace_id: str | None) -> None:
        """Initialize the workspace base instance."""
        self.workspace_id = workspace_id or uuid.uuid4().hex
        self.is_alive = False
        self._backend = None

    # ── lifecycle (developer) ──────────────────────────────────────

    @abstractmethod
    async def initialize(self) -> None:
        """Provision resources, connect MCP servers, copy skills."""

    @abstractmethod
    async def close(self) -> None:
        """Release all resources and connections."""

    async def reset(self) -> None:
        """Reset the workspace to a clean state.

        Closes and removes all registered MCPs, deletes all skills,
        and wipes per-session state (offloaded context / tool results
        and any data files). Constructor-time ``default_mcps`` and
        ``skill_paths`` are **not** re-seeded — reset returns the
        workspace to an empty state, not its initial state.

        The default implementation is a no-op. Subclasses with user
        state must override this.
        """

    async def __aenter__(self) -> Self:
        """Context manager support for ``async with``. Calls ``initialize()``
        and returns the workspace instance.
        """
        await self.initialize()
        self.is_alive = True
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Context manager support for ``async with``. Calls ``close()``
        and returns the workspace instance.
        """
        await self.close()
        self.is_alive = False

    # ── instructions ───────────────────────────────────────────────

    @abstractmethod
    async def get_instructions(self) -> str:
        """Workspace-specific system prompt fragment."""

    # ── for Agent: tool & resource discovery ───────────────────────

    async def list_tools(self) -> list[ToolBase]:
        """Built-in tools scoped to this workspace."""
        from ..tool import (
            Bash,
            CreateProjectDirectory,
            Edit,
            Glob,
            Grep,
            Read,
            WebFetch,
            WebSearch,
            Write,
        )

        backend = self.get_backend()
        glob_kwargs: dict = {"backend": backend}
        if self._glob_helper_path is not None:
            glob_kwargs["glob_helper_path"] = self._glob_helper_path
        return [
            Bash(cwd=self.workdir, backend=backend),
            Edit(backend=backend),
            Glob(**glob_kwargs),
            Grep(backend=backend),
            Read(backend=backend),
            WebSearch(backend=backend),
            WebFetch(backend=backend),
            Write(backend=backend),
            CreateProjectDirectory(backend=backend),
        ]

    def get_backend(self) -> BackendBase:
        """Return the workspace's active builtin-tool backend."""
        if self._backend is None:
            raise RuntimeError(
                f"{type(self).__name__} has no active backend. "
                "Initialize the workspace before requesting its backend.",
            )
        return self._backend

    @abstractmethod
    async def list_mcps(self) -> list[MCPClient]:
        """Active MCP clients (each provides its own tools)."""

    @abstractmethod
    async def list_skills(self) -> list[Skill]:
        """Skills available in this workspace."""

    # ── for Agent: offload ─────────────────────────────────────────

    @abstractmethod
    async def offload_context(
        self,
        session_id: str,
        msgs: list[Msg],
    ) -> str:
        """Persist compressed context for agentic retrieval.

        Args:
            session_id: Unique session identifier used to
                partition offloaded data.
            msgs: Conversation messages to offload.

        Returns:
            Path or identifier for the offloaded data.
        """

    @abstractmethod
    async def offload_tool_result(
        self,
        session_id: str,
        tool_result: ToolResultBlock,
    ) -> str:
        """Persist a tool result for agentic retrieval.

        Args:
            session_id: Unique session identifier used to
                partition offloaded data.
            tool_result: The tool result block to offload.

        Returns:
            Path or identifier for the offloaded data.
        """

    # ── for User: dynamic MCP management ───────────────────────────

    @abstractmethod
    async def add_mcp(self, mcp_client: MCPClient) -> None:
        """Dynamically register a new MCP server.

        Args:
            mcp_client: An :class:`MCPClient` instance describing
                the MCP server to add.

        Raises:
            ValueError: If an MCP with the same name already exists.
        """

    @abstractmethod
    async def remove_mcp(self, name: str) -> None:
        """Dynamically remove an MCP server by name.

        Args:
            name: Name of the MCP server to remove.
        """

    # ── for User: dynamic skill management ─────────────────────────

    @abstractmethod
    async def add_skill(self, skill_path: str) -> None:
        """Add a skill from a local directory path.

        The directory must contain a ``SKILL.md`` with ``name``
        and ``description`` in its YAML front matter.

        Args:
            skill_path: Absolute or relative path to the skill
                directory on the local filesystem.
        """

    @abstractmethod
    async def remove_skill(self, name: str) -> None:
        """Remove a skill by its agent-facing name.

        Args:
            name: The ``name`` field from the skill's
                ``SKILL.md`` front matter.

        Raises:
            KeyError: If the skill is not found in the workspace.
        """

    @abstractmethod
    async def sync_mcp_asset(
        self,
        name: str,
        source_dir: str,
        content_hash: str,
    ) -> tuple[str, bool]:
        """Copy or upload one MCP asset directory into the workspace.

        Args:
            name: MCP asset name, used as the target directory name under
                ``<workdir>/mcps``.
            source_dir: Absolute path to the extracted MCP asset directory on
                the host filesystem.
            content_hash: Stable asset content hash used to detect whether the
                runtime MCP process must be refreshed.

        Returns:
            A tuple of ``(target_dir, changed)`` where ``changed`` indicates
            whether the asset content differs from the version already present
            in the workspace.
        """

    @abstractmethod
    async def remove_mcp_asset(self, name: str) -> None:
        """Remove one MCP asset directory from the workspace."""

    @abstractmethod
    async def list_mcp_asset_names(self) -> list[str]:
        """List MCP asset directory names currently present in the workspace."""

    # ── agent-scoped resource hooks for shared runtimes ────────────

    async def _list_agent_mcps(
        self,
        agent_id: str,
    ) -> list[MCPClient]:
        raise NotImplementedError

    async def _add_agent_mcp(
        self,
        agent_id: str,
        mcp_client: MCPClient,
    ) -> None:
        raise NotImplementedError

    async def _remove_agent_mcp(
        self,
        agent_id: str,
        name: str,
    ) -> None:
        raise NotImplementedError

    async def _list_agent_skills(
        self,
        agent_id: str,
    ) -> list[Skill]:
        raise NotImplementedError

    async def _add_agent_skill(
        self,
        agent_id: str,
        skill_path: str,
    ) -> None:
        raise NotImplementedError

    async def _remove_agent_skill(
        self,
        agent_id: str,
        name: str,
    ) -> None:
        raise NotImplementedError

    async def _sync_agent_mcp_asset(
        self,
        agent_id: str,
        name: str,
        source_dir: str,
        content_hash: str,
    ) -> tuple[str, bool]:
        raise NotImplementedError

    async def _remove_agent_mcp_asset(
        self,
        agent_id: str,
        name: str,
    ) -> None:
        raise NotImplementedError

    async def _list_agent_mcp_asset_names(
        self,
        agent_id: str,
    ) -> list[str]:
        raise NotImplementedError

    def _agent_resource_root(self, agent_id: str) -> str:
        raise NotImplementedError
