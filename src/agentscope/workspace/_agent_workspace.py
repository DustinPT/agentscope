# -*- coding: utf-8 -*-
"""Agent-scoped workspace view over a shared workspace runtime."""

from __future__ import annotations

from ..mcp import MCPClient
from ..message import Msg, ToolResultBlock
from ..skill import Skill
from ._base import WorkspaceBase


class AgentWorkspaceView(WorkspaceBase):
    """Bind one shared runtime to a specific ``agent_id``.

    The wrapped runtime owns the shared filesystem root, backend, and
    offload lifecycle. This view narrows MCP / skill / MCP-asset
    operations to ``agents/<agent_id>/`` while leaving builtin file
    tools pointed at the shared ``workdir``.
    """

    def __init__(
        self,
        runtime: WorkspaceBase,
        agent_id: str,
    ) -> None:
        super().__init__(workspace_id=runtime.workspace_id)
        self._runtime = runtime
        self.agent_id = agent_id
        self.workdir = runtime.workdir
        self.is_alive = runtime.is_alive
        self._backend = runtime.get_backend()

    @property
    def resource_root(self) -> str:
        """Agent-scoped resource root under the shared workspace."""
        return self._runtime._agent_resource_root(self.agent_id)

    @property
    def _glob_helper_path(self) -> str | None:
        """Delegate glob helper path to the shared runtime."""
        return self._runtime._glob_helper_path

    async def initialize(self) -> None:
        """Runtime is already initialized by the workspace manager."""
        return

    async def close(self) -> None:
        """The shared runtime is manager-owned; views do not close it."""
        return

    async def reset(self) -> None:
        """Reset the shared runtime."""
        await self._runtime.reset()

    async def get_instructions(self) -> str:
        """Delegate instructions to the shared runtime."""
        return await self._runtime.get_instructions()

    async def list_tools(self):
        """Use shared builtin tools rooted at the shared workspace."""
        return await self._runtime.list_tools()

    async def list_mcps(self) -> list[MCPClient]:
        """List MCPs visible to this agent."""
        return await self._runtime._list_agent_mcps(self.agent_id)

    async def list_skills(self) -> list[Skill]:
        """List skills visible to this agent."""
        return await self._runtime._list_agent_skills(self.agent_id)

    async def has_valid_skill_index(self) -> bool:
        """Return whether this agent namespace has a usable .skills."""
        checker = getattr(self._runtime, "_agent_has_valid_skill_index", None)
        if checker is None:
            return True
        return await checker(self.agent_id)

    async def reset_skills_state(self) -> None:
        """Clear this agent namespace's skills and recreate .skills."""
        resetter = getattr(self._runtime, "_reset_agent_skills_state", None)
        if resetter is None:
            return
        await resetter(self.agent_id)

    async def offload_context(
        self,
        session_id: str,
        msgs: list[Msg],
    ) -> str:
        """Persist context into the shared runtime's session area."""
        return await self._runtime.offload_context(session_id, msgs)

    async def offload_tool_result(
        self,
        session_id: str,
        tool_result: ToolResultBlock,
    ) -> str:
        """Persist tool results into the shared runtime's session area."""
        return await self._runtime.offload_tool_result(
            session_id,
            tool_result,
        )

    async def add_mcp(self, mcp_client: MCPClient) -> None:
        """Add one MCP scoped to this agent."""
        await self._runtime._add_agent_mcp(self.agent_id, mcp_client)

    async def remove_mcp(self, name: str) -> None:
        """Remove one MCP scoped to this agent."""
        await self._runtime._remove_agent_mcp(self.agent_id, name)

    async def add_skill(self, skill_path: str) -> None:
        """Add one skill scoped to this agent."""
        await self._runtime._add_agent_skill(self.agent_id, skill_path)

    async def remove_skill(self, name: str) -> None:
        """Remove one skill scoped to this agent."""
        await self._runtime._remove_agent_skill(self.agent_id, name)

    async def sync_mcp_asset(
        self,
        name: str,
        source_dir: str,
        content_hash: str,
    ) -> tuple[str, bool]:
        """Sync one MCP asset scoped to this agent."""
        return await self._runtime._sync_agent_mcp_asset(
            self.agent_id,
            name,
            source_dir,
            content_hash,
        )

    async def remove_mcp_asset(self, name: str) -> None:
        """Remove one MCP asset scoped to this agent."""
        await self._runtime._remove_agent_mcp_asset(self.agent_id, name)

    async def list_mcp_asset_names(self) -> list[str]:
        """List MCP assets visible to this agent."""
        return await self._runtime._list_agent_mcp_asset_names(self.agent_id)
