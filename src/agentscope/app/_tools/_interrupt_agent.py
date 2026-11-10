# -*- coding: utf-8 -*-
"""DSH-style interrupt_agent tool for managed subagent trees."""
from __future__ import annotations

from typing import Any

from pydantic import Field

from .._subagent_runtime import (
    ROOT_AGENT_NAME,
    agent_name_for,
    get_subagent_meta,
    is_descendant_session,
    load_session_state,
    resolve_tree_target,
    root_session_id_for,
)
from ...message import TextBlock, ToolResultState
from ...state import AgentState
from ...tool import ParamsBase, ToolChunk
from ._session_tool_base import _SessionToolBase


class _InterruptAgentParams(ParamsBase):
    agent: str = Field(description="The name of an agent created under you: your direct child or a deeper descendant.")


class InterruptAgent(_SessionToolBase):
    name = "interrupt_agent"
    description = (
        "Ask a subagent to stop its current work. This call returns without waiting for it to stop. "
        "You can continue a local subagent's conversation later with send_message. "
        "External executions stop permanently and cannot receive follow-ups. Subagents it started will keep running."
    )
    input_schema: dict[str, Any] = _InterruptAgentParams.model_json_schema()
    is_read_only = False
    is_state_injected = True

    async def __call__(self, agent: str, _agent_state: AgentState | None = None):
        session = await self._storage.get_session(self._user_id, self._agent_id, self._session_id)
        if session is None:
            return self._result({"error": "Current session not found."}, state=ToolResultState.ERROR)
        session_state = (
            _agent_state
            if _agent_state is not None and get_subagent_meta(_agent_state) is not None
            else session.state
        )
        current_name = agent_name_for(session, session_state)
        if agent.strip() in {current_name, ROOT_AGENT_NAME if current_name == ROOT_AGENT_NAME else ""}:
            return self._result({"error": "interrupt_agent cannot target the current agent."}, state=ToolResultState.ERROR)

        root_session_id = root_session_id_for(session, session_state)
        root_session = session if root_session_id == session.id else await self._storage.get_session_meta(self._user_id, root_session_id)
        if root_session is None:
            return self._result({"error": "Subagent tree root not found."}, state=ToolResultState.ERROR)
        target = await resolve_tree_target(self._storage, self._user_id, root_session, agent.strip())
        if target is None or target.id == self._session_id:
            return self._result({"error": f"Agent '{agent}' not found in the current subagent tree."}, state=ToolResultState.ERROR)
        if not await is_descendant_session(
            self._storage,
            self._user_id,
            ancestor_session_id=self._session_id,
            session=target,
        ):
            return self._result({"error": f"Agent '{agent}' is not a descendant of the current agent."}, state=ToolResultState.ERROR)
        target_path = agent_name_for(
            target,
            await load_session_state(self._storage, self._user_id, target),
        )

        released, _updated_session = await self._interrupt_managed_session(
            agent_id=target.agent_id,
            session_id=target.id,
            reason=f"interrupt_agent:{self._session_id}",
        )
        if not released:
            return self._result(
                {"error": f"interrupt_agent could not interrupt agent '{target_path}'."},
                state=ToolResultState.ERROR,
            )
        return ToolChunk(
            content=[TextBlock(text=f"interrupt requested for agent {target_path}")],
            metadata={"accepted": True},
        )
