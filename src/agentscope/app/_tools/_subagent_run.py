# -*- coding: utf-8 -*-
"""Tool for asynchronously running a managed sub-agent session."""
from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING, Any

from pydantic import Field

from ...event import CustomEvent
from ...message import HintBlock, TextBlock, ToolResultState
from ...tool import ParamsBase, ToolBase, ToolChunk
from ...permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from ..storage import SessionConfig, SessionSource

if TYPE_CHECKING:
    from ..message_bus import MessageBus
    from ..storage import StorageBase


class _SubAgentRunParams(ParamsBase):
    """Parameters for :class:`SubAgentRun`."""

    agent_id: str = Field(
        description="Managed agent id to run as a sub-agent.",
    )
    prompt: str = Field(
        description="Task instructions to hand off to the sub-agent.",
    )
    session_name: str | None = Field(
        default=None,
        description=(
            "Required when creating a new child session. Use a short, "
            "user-friendly task title in the user's language so it can be "
            "shown directly in the UI, for example '整理发布计划' or "
            "'Write onboarding email'. Do not use technical slugs, internal "
            "identifiers, filenames, or agent ids such as "
            "'presentation-builder-china-space-station'. Omit when resuming "
            "an existing child session."
        ),
    )
    session_id: str | None = Field(
        default=None,
        description=(
            "Existing child session to resume. Omit to create a new child "
            "session."
        ),
    )


class SubAgentRun(ToolBase):
    """Spawn or resume a child session for a managed sub-agent."""

    name: str = "SubAgentRun"
    description: str = """Asynchronously run a managed sub-agent.

Use this tool when you want another configured agent to work on a sub-task in
parallel. The call returns immediately after the child session is scheduled.

Important:
- When creating a new child session, you MUST provide `session_name`.
- `session_name` MUST be a short, user-friendly task title written in the
  user's language, because it is shown directly in the product UI.
- Use natural titles such as `整理竞品调研结论`, `撰写发布公告`, or
  `Write onboarding email`.
- DO NOT use technical or internal names such as
  `presentation-builder-china-space-station`, `research_task_01`,
  filenames, repo paths, or agent ids.
- When resuming an existing child session, you MUST provide `session_id` and
  MUST leave `session_name` empty.
- Put the full task, context, constraints, and deliverables in `prompt` so the
  sub-agent can start working immediately.
- This tool returns immediately after the child session starts. DO NOT poll,
  query, or wait for the child session yourself. DO NOT call any waiting tool
  such as `bash sleep`.
- After the child session finishes, the parent session will be notified
  automatically. You have exactly two valid follow-up options:
  1. Continue with other independent tasks and ignore this sub-agent for now; or
  2. If there is nothing else to do, simply give a text reply without calling
     any tool, which ends the current reasoning loop.
"""
    input_schema: dict[str, Any] = _SubAgentRunParams.model_json_schema()
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(
        self,
        storage: "StorageBase",
        message_bus: "MessageBus",
        user_id: str,
        session_id: str,
        agent_id: str,
        allowed_subagents: list[dict[str, str]] | None = None,
    ) -> None:
        """Bind request-scoped identifiers and shared dependencies."""
        self._storage = storage
        self._message_bus = message_bus
        self._user_id = user_id
        self._session_id = session_id
        self._agent_id = agent_id
        self._allowed_subagents = allowed_subagents or []

        if self._allowed_subagents:
            available_lines = []
            allowed_ids = []
            for item in self._allowed_subagents:
                agent_id_value = item["agent_id"]
                agent_name = item["name"]
                agent_description = item.get("description", "").strip()
                allowed_ids.append(agent_id_value)
                line = f"- `{agent_id_value}` (`{agent_name}`)"
                if agent_description:
                    line += f" - {agent_description}"
                available_lines.append(line)

            available_text = "\n".join(available_lines)
            schema = copy.deepcopy(_SubAgentRunParams.model_json_schema())
            schema["properties"]["agent_id"] = {
                "type": "string",
                "enum": allowed_ids,
                "description": (
                    "Managed agent id to run as a sub-agent. "
                    "Choose from the configured allowlist.\n\n"
                    f"Available sub-agents:\n{available_text}"
                ),
            }
            self.input_schema = schema
        else:
            schema = copy.deepcopy(_SubAgentRunParams.model_json_schema())
            schema["properties"]["agent_id"]["description"] = (
                "Managed agent id to run as a sub-agent. "
                "No callable sub-agents are currently configured."
            )
            self.input_schema = schema

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Always allow when attached; attachment already enforces policy."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="SubAgentRun is allowed when attached to the agent.",
        )

    async def __call__(
        self,
        agent_id: str,
        prompt: str,
        session_name: str | None = None,
        session_id: str | None = None,
    ) -> ToolChunk:
        """Create or resume a child session and start the assigned task."""
        caller_agent = await self._storage.get_agent(self._user_id, self._agent_id)
        caller_session = await self._storage.get_session(
            self._user_id,
            self._agent_id,
            self._session_id,
        )
        if caller_agent is None or caller_session is None:
            return ToolChunk(
                content=[TextBlock(text="SubAgentRun: caller session not found.")],
                state=ToolResultState.ERROR,
            )

        if not caller_agent.data.allow_subagent_calls:
            return ToolChunk(
                content=[
                    TextBlock(
                        text=(
                            "SubAgentRun: this agent is not allowed to call "
                            "sub-agents."
                        ),
                    ),
                ],
                state=ToolResultState.ERROR,
            )

        if agent_id not in caller_agent.data.allowed_subagent_ids:
            return ToolChunk(
                content=[
                    TextBlock(
                        text=(
                            f"SubAgentRun: agent {agent_id!r} is not in the "
                            "allowed sub-agent list."
                        ),
                    ),
                ],
                state=ToolResultState.ERROR,
            )

        target_agent = await self._storage.get_agent(self._user_id, agent_id)
        if target_agent is None or target_agent.user_id != self._user_id:
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"SubAgentRun: target agent {agent_id!r} not found.",
                    ),
                ],
                state=ToolResultState.ERROR,
            )

        session_name_value = (session_name or "").strip()
        if session_id is None and not session_name_value:
            return ToolChunk(
                content=[
                    TextBlock(
                        text=(
                            "SubAgentRun: session_name is required when "
                            "creating a new child session."
                        ),
                    ),
                ],
                state=ToolResultState.ERROR,
            )
        if session_id is not None and session_name is not None:
            return ToolChunk(
                content=[
                    TextBlock(
                        text=(
                            "SubAgentRun: session_name must be omitted when "
                            "resuming an existing child session."
                        ),
                    ),
                ],
                state=ToolResultState.ERROR,
            )

        chat_model_config = (
            target_agent.data.default_chat_model_config
            or caller_session.config.chat_model_config
        )
        fallback_chat_model_config = (
            caller_session.config.fallback_chat_model_config
        )
        if chat_model_config is None:
            return ToolChunk(
                content=[
                    TextBlock(
                        text=(
                            "SubAgentRun: no chat model is available for the "
                            "target sub-agent."
                        ),
                    ),
                ],
                state=ToolResultState.ERROR,
            )

        mode = "resume" if session_id is not None else "new"
        child_session_id: str
        child_session_name: str
        if session_id is None:
            child_session = await self._storage.upsert_session(
                user_id=self._user_id,
                agent_id=agent_id,
                config=SessionConfig(
                    workspace_id=caller_session.config.workspace_id,
                    name=session_name_value,
                    chat_model_config=chat_model_config,
                    fallback_chat_model_config=fallback_chat_model_config,
                ),
                source=SessionSource.SUBAGENT,
                parent_session_id=self._session_id,
                parent_tool_call_id=None,
            )
            child_session_id = child_session.id
            child_session_name = child_session.config.name
            await self._message_bus.session_publish_event(
                self._session_id,
                CustomEvent(
                    name="subagent_sessions_updated",
                    value={
                        "parent_session_id": self._session_id,
                        "child_session_id": child_session_id,
                        "child_agent_id": agent_id,
                    },
                ).model_dump(mode="json"),
            )
        else:
            child_session = await self._storage.get_session(
                self._user_id,
                agent_id,
                session_id,
            )
            if child_session is None:
                return ToolChunk(
                    content=[
                        TextBlock(
                            text=(
                                f"SubAgentRun: child session {session_id!r} "
                                "not found."
                            ),
                        ),
                    ],
                    state=ToolResultState.ERROR,
                )
            if child_session.parent_session_id != self._session_id:
                return ToolChunk(
                    content=[
                        TextBlock(
                            text=(
                                "SubAgentRun: the requested session does not "
                                "belong to this parent session."
                            ),
                        ),
                    ],
                    state=ToolResultState.ERROR,
                )
            child_session = await self._storage.upsert_session(
                user_id=self._user_id,
                agent_id=agent_id,
                session_id=child_session.id,
                config=SessionConfig(
                    workspace_id=child_session.config.workspace_id,
                    name=child_session.config.name,
                    chat_model_config=chat_model_config,
                    fallback_chat_model_config=fallback_chat_model_config,
                ),
                state=child_session.state,
            )
            child_session_id = child_session.id
            child_session_name = child_session.config.name

        hint = HintBlock(
            hint=(
                f'<subagent-task parent_agent_id="{self._agent_id}" '
                f'parent_agent_name="{caller_agent.data.name}" '
                f'parent_session_id="{self._session_id}">\n'
                f"{prompt}\n"
                f"</subagent-task>"
            ),
            source=json.dumps(
                {
                    "label": "subagent_task",
                    "sublabel": caller_agent.data.name,
                    "parent_session_id": self._session_id,
                },
                ensure_ascii=False,
            ),
        )
        await self._message_bus.inbox_push(
            child_session_id,
            hint.model_dump(mode="json"),
        )
        await self._message_bus.enqueue_wakeup(
            user_id=self._user_id,
            session_id=child_session_id,
            agent_id=agent_id,
        )

        payload = {
            "status": "started",
            "agent_id": agent_id,
            "agent_name": target_agent.data.name,
            "session_id": child_session_id,
            "session_name": child_session_name,
            "mode": mode,
        }
        return ToolChunk(
            content=[TextBlock(text=json.dumps(payload, ensure_ascii=False))],
        )
