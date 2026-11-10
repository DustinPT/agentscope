# -*- coding: utf-8 -*-
"""DSH-style list_agents tool for managed subagent trees."""
from __future__ import annotations

from typing import Any

from ...message import TextBlock
from ...permission import PermissionBehavior, PermissionContext, PermissionDecision
from ...state import AgentState
from ...tool import ParamsBase, ToolBase, ToolChunk
from .._subagent_runtime import collect_subagent_catalog, get_subagent_meta, root_session_id_for


class _ListAgentsParams(ParamsBase):
    """No-input schema for list_agents."""


class ListAgents(ToolBase):
    name = "list_agents"
    description = (
        "List your entire subagent tree: the root agent and all reachable continuable descendants, including "
        "yourself. Rows carry names, descriptions, and status. A descendant's name is its path from the root, "
        "such as reviewer/checker. You can message any other agent in the subagent tree. `running` means it is "
        "working; `inactive` means no turn is executing; it does not indicate task completion, success, or failure. "
        "While you remain active, you will be notified when a direct child finishes; there is no need to keep "
        "checking its status. Use send_message to continue the conversation."
    )
    input_schema: dict[str, Any] = _ListAgentsParams.model_json_schema()
    is_concurrency_safe = True
    is_read_only = True
    is_state_injected = True
    is_external_tool = False
    is_mcp = False
    mcp_name: str | None = None

    def __init__(self, storage, message_bus, user_id: str, session_id: str, agent_id: str) -> None:
        self._storage = storage
        self._message_bus = message_bus
        self._user_id = user_id
        self._session_id = session_id
        self._agent_id = agent_id

    async def check_permissions(self, tool_input: dict[str, Any], context: PermissionContext) -> PermissionDecision:
        return PermissionDecision(behavior=PermissionBehavior.ALLOW, message="list_agents is allowed when attached to the agent.")

    async def __call__(self, _agent_state: AgentState | None = None) -> ToolChunk:
        session = await self._storage.get_session(self._user_id, self._agent_id, self._session_id)
        if session is None:
            return ToolChunk(content=[TextBlock(text="[]")], metadata={"agents": []})
        session_state = (
            _agent_state
            if _agent_state is not None and get_subagent_meta(_agent_state) is not None
            else session.state
        )
        root_session_id = root_session_id_for(session, session_state)
        root_session = session if root_session_id == session.id else await self._storage.get_session_meta(self._user_id, root_session_id)
        if root_session is None:
            return ToolChunk(content=[TextBlock(text="[]")], metadata={"agents": []})
        rows = await collect_subagent_catalog(
            self._storage,
            self._message_bus,
            self._user_id,
            root_session,
        )
        rendered = "\n".join(
            f'{row["name"]} [{row["status"]}]'
            + (
                f' — {row["description"]}'
                if isinstance(row.get("description"), str) and row["description"]
                else ""
            )
            for row in rows
        )
        return ToolChunk(content=[TextBlock(text=rendered)], metadata={"agents": rows})
