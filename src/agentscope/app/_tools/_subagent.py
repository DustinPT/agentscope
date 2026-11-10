# -*- coding: utf-8 -*-
"""DSH-style fresh subagent tool backed by managed AgentScope child sessions."""
from __future__ import annotations

import copy
import json
import re
from typing import Any

from pydantic import Field

from .._bus_ops import deliver_to_inbox
from .._subagent_runtime import (
    ROOT_AGENT_NAME,
    agent_name_for,
    allocate_child_name,
    attach_subagent_meta,
    build_task_hint_block,
    build_task_record,
    count_subagent_descendants,
    root_session_id_for,
)
from ...message import TextBlock, ToolResultState
from ...permission import PermissionBehavior, PermissionContext, PermissionDecision
from ...state import AgentState
from ...tool import ParamsBase, ToolBase, ToolChunk
from ..storage import SessionConfig, SessionSource

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


class _SubagentParams(ParamsBase):
    description: str = Field(description="A short (3-5 word) description of the delegated task, for display. Use the language of the user's current request.")
    prompt: str = Field(description="The complete, self-contained task for the subagent. It does not share this conversation's context, so include everything it needs.")
    agent_id: str = Field(description="Managed child agent preset id to use for the subagent.")
    name: str | None = Field(default=None, description="Optional short name for the subagent, unique among the subagents you create: lower-case letters, digits, and hyphens, starting with a letter, at most 32 characters; lead is reserved for the root agent. Omitted names default to agent-1, agent-2, and so on.")
    cwd: str | None = Field(default=None, description="Initial child working directory. Relative paths use your current directory; omitted inherits it. Later directory changes in either agent are independent.")


class Subagent(ToolBase):
    name = "subagent"
    mode = "spawn"
    description = (
        "Delegate a self-contained task to a subagent (a separate agent that works in its own context) to offload "
        "focused, independent work — research, a scoped implementation, an analysis — so it does not consume this "
        "conversation's context. The subagent returns its result, not its intermediate steps. This tool starts an "
        "independently managed subagent and immediately returns its launch metadata. If you are still online when it "
        "finishes, the runtime automatically delivers its completion notice. The notice includes the final answer "
        "unless the child sent you a message just before finishing. Use `send_message` to steer it while running or "
        "continue its conversation after it finishes."
    )
    input_schema: dict[str, Any] = _SubagentParams.model_json_schema()
    is_concurrency_safe = True
    is_read_only = True
    is_state_injected = True
    is_external_tool = False
    is_mcp = False
    mcp_name: str | None = None

    def __init__(
        self,
        storage,
        message_bus,
        user_id: str,
        session_id: str,
        agent_id: str,
        allowed_subagents: list[dict[str, str]] | None = None,
        max_active_subagents: int = 8,
    ) -> None:
        self._storage = storage
        self._message_bus = message_bus
        self._user_id = user_id
        self._session_id = session_id
        self._agent_id = agent_id
        self._allowed_subagents = allowed_subagents or []
        self._max_active_subagents = max_active_subagents

        schema = copy.deepcopy(_SubagentParams.model_json_schema())
        if self._allowed_subagents:
            available_lines: list[str] = []
            allowed_ids: list[str] = []
            for item in self._allowed_subagents:
                agent_id_value = item["agent_id"]
                agent_name = item["name"]
                agent_description = item.get("description", "").strip()
                allowed_ids.append(agent_id_value)
                line = f"- `{agent_id_value}` (`{agent_name}`)"
                if agent_description:
                    line += f" - {agent_description}"
                available_lines.append(line)

            schema["properties"]["agent_id"] = {
                "type": "string",
                "enum": allowed_ids,
                "description": (
                    "Managed child agent preset id to use for the subagent.\n\nAvailable sub-agents:\n"
                    f"{chr(10).join(available_lines)}"
                ),
            }
        else:
            schema["properties"]["agent_id"]["description"] = (
                "Managed child agent preset id to use for the subagent. "
                "No callable sub-agents are currently configured."
            )
        self.input_schema = schema

    @staticmethod
    def _json_result(payload: dict[str, Any], *, state: ToolResultState = ToolResultState.SUCCESS) -> ToolChunk:
        return ToolChunk(
            content=[TextBlock(text=json.dumps(payload, ensure_ascii=False))],
            state=state,
            metadata=payload,
        )

    async def check_permissions(self, tool_input: dict[str, Any], context: PermissionContext) -> PermissionDecision:
        return PermissionDecision(behavior=PermissionBehavior.ALLOW, message="subagent is allowed when attached to the agent.")

    def _build_child_state(
        self,
        *,
        caller_state: AgentState,
        root_session_id: str,
        parent_name: str,
        child_name: str,
        child_path: str,
        description: str,
        cwd: str | None,
    ) -> AgentState:
        child_state = AgentState(
            permission_context=PermissionContext(
                mode=caller_state.permission_context.mode,
            ),
        )
        attach_subagent_meta(
            child_state,
            root_session_id=root_session_id,
            parent_session_id=self._session_id,
            parent_name=parent_name,
            name=child_name,
            path=child_path,
            label=description.strip(),
            mode=self.mode,
            cwd=(cwd or "").strip() or None,
        )
        return child_state

    async def __call__(self, description: str, prompt: str, agent_id: str, name: str | None = None, cwd: str | None = None, _agent_state: AgentState | None = None) -> ToolChunk:
        runtime_context = _agent_state.tool_context.runtime_context if _agent_state is not None else None
        current_tool_call_id = runtime_context.current_tool_call_id if runtime_context is not None else None
        if not current_tool_call_id:
            return self._json_result({"error": "subagent: current tool call id is unavailable."}, state=ToolResultState.ERROR)

        caller_agent = await self._storage.get_agent(self._user_id, self._agent_id)
        caller_session = await self._storage.get_session(self._user_id, self._agent_id, self._session_id)
        if caller_agent is None or caller_session is None:
            return self._json_result({"error": "subagent: the current session is unavailable."}, state=ToolResultState.ERROR)
        if not caller_agent.data.allow_subagent_calls:
            return self._json_result({"error": "subagent: this agent cannot start subagent tasks."}, state=ToolResultState.ERROR)

        requested_agent_id = agent_id.strip()
        if not requested_agent_id:
            return self._json_result(
                {"error": "subagent: agent_id is required."},
                state=ToolResultState.ERROR,
            )
        if requested_agent_id not in caller_agent.data.allowed_subagent_ids:
            return self._json_result(
                {
                    "error": (
                        f"subagent: agent '{requested_agent_id}' cannot be used "
                        "as a subagent here."
                    ),
                },
                state=ToolResultState.ERROR,
            )
        child_agent = await self._storage.get_agent(self._user_id, requested_agent_id)
        if child_agent is None:
            return self._json_result(
                {"error": f"subagent: agent '{requested_agent_id}' was not found."},
                state=ToolResultState.ERROR,
            )

        requested_name = (name or "").strip() or None
        if requested_name is not None and not _NAME_RE.fullmatch(requested_name):
            return self._json_result({"error": "subagent: name must start with a letter and contain only lowercase letters, digits, and hyphens."}, state=ToolResultState.ERROR)

        try:
            child_name = await allocate_child_name(self._storage, self._user_id, self._session_id, requested_name)
        except ValueError as exc:
            return self._json_result({"error": f"subagent: {exc}"}, state=ToolResultState.ERROR)

        chat_model_config = child_agent.data.default_chat_model_config or caller_session.config.chat_model_config
        fallback_chat_model_config = child_agent.data.default_fallback_chat_model_config or caller_session.config.fallback_chat_model_config
        if chat_model_config is None:
            return self._json_result({"error": "subagent: no chat model is available for the child session."}, state=ToolResultState.ERROR)

        caller_state = _agent_state if _agent_state is not None else caller_session.state
        root_session_id = root_session_id_for(caller_session, caller_state)
        active_descendants = await count_subagent_descendants(
            self._storage,
            self._user_id,
            root_session_id,
        )
        if active_descendants >= self._max_active_subagents:
            return self._json_result(
                {
                    "error": (
                        "subagent limit reached "
                        f"(active child limit: {self._max_active_subagents}); "
                        "wait for an existing child to finish or complete this work "
                        "with the current agents"
                    ),
                    "code": "ACTIVATION_LIMIT_REACHED",
                    "limit": self._max_active_subagents,
                    "active_children": active_descendants,
                },
                state=ToolResultState.ERROR,
            )

        parent_name = agent_name_for(caller_session, caller_state)
        child_path = child_name if parent_name == ROOT_AGENT_NAME else f"{parent_name}/{child_name}"

        child_state = self._build_child_state(
            caller_state=caller_state,
            root_session_id=root_session_id,
            parent_name=parent_name,
            child_name=child_name,
            child_path=child_path,
            description=description,
            cwd=cwd,
        )
        child_session = await self._storage.upsert_session(
            user_id=self._user_id,
            agent_id=child_agent.id,
            config=SessionConfig(
                workspace_id=caller_session.config.workspace_id,
                name=description.strip(),
                chat_model_config=chat_model_config,
                fallback_chat_model_config=fallback_chat_model_config,
                permission_mode=caller_session.config.permission_mode,
            ),
            state=child_state,
            source=SessionSource.SUBAGENT,
            parent_session_id=self._session_id,
        )
        if child_state.context:
            await self._storage.replace_messages(
                user_id=self._user_id,
                session_id=child_session.id,
                messages=child_state.context,
            )

        caller_active_task = None
        if caller_session.parent_session_id is not None:
            caller_active_task = await self._storage.get_active_subagent_task_by_child_session(self._user_id, self._session_id)
        parent_task_id = None
        if caller_active_task is not None and caller_active_task.status == "running":
            caller_active_task.open_descendant_count += 1
            await self._storage.upsert_subagent_task(self._user_id, caller_active_task)
            parent_task_id = caller_active_task.id

        task_record = build_task_record(
            user_id=self._user_id,
            parent_session_id=self._session_id,
            child_session_id=child_session.id,
            child_agent_id=child_agent.id,
            parent_tool_call_id=current_tool_call_id,
            parent_task_id=parent_task_id,
            name=child_name,
            path=child_path,
            label=description.strip(),
            mode=self.mode,
            root_session_id=root_session_id,
            parent_agent_id=self._agent_id,
        )
        await self._storage.upsert_subagent_task(self._user_id, task_record)
        await self._storage.mark_active_subagent_task(self._user_id, child_session.id, task_record.id)

        task_prompt = prompt.strip()
        if cwd and cwd.strip():
            task_prompt = f"Initial working directory: {cwd.strip()}\n\n{task_prompt}"
        hint = build_task_hint_block(
            parent_agent_id=self._agent_id,
            parent_agent_name=caller_agent.data.name,
            parent_session_id=self._session_id,
            prompt=task_prompt,
            task_id=task_record.id,
            mode=self.mode,
            parent_tool_call_id=current_tool_call_id,
        )
        await deliver_to_inbox(
            self._message_bus,
            user_id=self._user_id,
            session_id=child_session.id,
            agent_id=child_agent.id,
            payload=hint.model_dump(mode="json"),
        )
        payload = {
            "status": "started",
            "agent": child_path,
            "name": child_name,
            "agent_id": child_agent.id,
            "session_id": child_session.id,
            "task_id": task_record.id,
            "mode": self.mode,
            "description": description.strip(),
        }
        return self._json_result(payload)
