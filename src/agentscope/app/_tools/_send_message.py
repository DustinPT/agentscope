# -*- coding: utf-8 -*-
"""DSH-style send_message tool for managed subagent sessions."""
from __future__ import annotations

import json
from typing import Any

from pydantic import Field

from .._bus_ops import deliver_to_inbox
from .._subagent_runtime import (
    agent_label_for,
    agent_name_for,
    agent_short_name_for,
    build_agent_message_hint,
    build_task_record,
    get_subagent_meta,
    load_session_state,
    resolve_tree_target,
    root_session_id_for,
)
from ...message import TextBlock, ToolResultState
from ...permission import PermissionBehavior, PermissionContext, PermissionDecision
from ...state import AgentState
from ...tool import ParamsBase, ToolBase, ToolChunk
from ..storage import SessionSource


class _SendMessageParams(ParamsBase):
    agent: str = Field(description="The name of another agent in your subagent tree, as shown by list_agents.")
    message: str = Field(description="The message to deliver to the agent.")


class SendMessage(ToolBase):
    name = "send_message"
    description = (
        "Send a message to an agent. A working agent receives it at its next step; an idle agent starts a new turn "
        "with it. Returns delivery confirmation."
    )
    input_schema: dict[str, Any] = _SendMessageParams.model_json_schema()
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

    @staticmethod
    def _json_result(payload: dict[str, Any], *, state: ToolResultState = ToolResultState.SUCCESS) -> ToolChunk:
        return ToolChunk(
            content=[TextBlock(text=json.dumps(payload, ensure_ascii=False))],
            state=state,
            metadata=payload,
        )

    async def check_permissions(self, tool_input: dict[str, Any], context: PermissionContext) -> PermissionDecision:
        return PermissionDecision(behavior=PermissionBehavior.ALLOW, message="send_message is allowed when attached to the agent.")

    async def __call__(self, agent: str, message: str, _agent_state: AgentState | None = None) -> ToolChunk:
        caller_session = await self._storage.get_session(self._user_id, self._agent_id, self._session_id)
        if caller_session is None:
            return self._json_result({"error": "send_message: the current session is unavailable."}, state=ToolResultState.ERROR)
        target_name = agent.strip()
        if not target_name:
            return self._json_result({"error": "send_message: agent is required."}, state=ToolResultState.ERROR)

        caller_state = (
            _agent_state
            if _agent_state is not None and get_subagent_meta(_agent_state) is not None
            else caller_session.state
        )
        root_session_id = root_session_id_for(caller_session, caller_state)
        root_session = caller_session if root_session_id == caller_session.id else await self._storage.get_session_meta(self._user_id, root_session_id)
        if root_session is None:
            return self._json_result({"error": "send_message: the subagent tree root is unavailable."}, state=ToolResultState.ERROR)

        target = await resolve_tree_target(self._storage, self._user_id, root_session, target_name)
        if target is None:
            return self._json_result({"error": f"send_message: agent '{agent}' was not found in the current subagent tree."}, state=ToolResultState.ERROR)
        if target.id == self._session_id:
            return self._json_result({"error": "send_message: you cannot message the current agent."}, state=ToolResultState.ERROR)

        runtime_context = _agent_state.tool_context.runtime_context if _agent_state is not None else None
        current_tool_call_id = runtime_context.current_tool_call_id if runtime_context is not None else None
        if not current_tool_call_id:
            return self._json_result({"error": "send_message: current tool call id is unavailable."}, state=ToolResultState.ERROR)

        sender_state = (
            _agent_state
            if _agent_state is not None and get_subagent_meta(_agent_state) is not None
            else caller_session.state
        )
        sender_name = agent_name_for(caller_session, sender_state)
        target_state = await load_session_state(self._storage, self._user_id, target)
        target_path = agent_name_for(target, target_state)
        task_id = None
        active_task = await self._storage.get_active_subagent_task_by_child_session(
            self._user_id,
            target.id,
        )
        if active_task is not None and active_task.status == "running":
            task_id = active_task.id
        else:
            if target.source == SessionSource.SUBAGENT:
                task_record = build_task_record(
                    user_id=self._user_id,
                    parent_session_id=self._session_id,
                    child_session_id=target.id,
                    child_agent_id=target.agent_id,
                    parent_tool_call_id=current_tool_call_id,
                    parent_task_id=None,
                    name=agent_short_name_for(target, target_state),
                    path=target_path,
                    label=agent_label_for(target, target_state),
                    mode="message",
                    root_session_id=root_session_id,
                    parent_agent_id=self._agent_id,
                )
                await self._storage.upsert_subagent_task(self._user_id, task_record)
                await self._storage.mark_active_subagent_task(self._user_id, target.id, task_record.id)
                task_id = task_record.id

        hint = build_agent_message_hint(sender_name=sender_name, message=message, task_id=task_id)
        await deliver_to_inbox(
            self._message_bus,
            user_id=self._user_id,
            session_id=target.id,
            agent_id=target.agent_id,
            payload=hint.model_dump(mode="json"),
        )
        payload = {
            "status": "delivered",
            "agent": target_path,
            "session_id": target.id,
            "task_id": task_id,
            "mode": "message",
        }
        return self._json_result(payload)
