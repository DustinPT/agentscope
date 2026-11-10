# -*- coding: utf-8 -*-
"""Middleware implementing child-session prompt guidance for subagents."""

from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Callable

from ..message_bus import MessageBus
from .._subagent_runtime import ROOT_AGENT_NAME, agent_name_for, get_subagent_meta
from ..storage import StorageBase
from ...middleware import MiddlewareBase

_SUBAGENT_SYSTEM_PROMPT_SUFFIX = """
When you receive a message wrapped in `<subagent-task ...>...</subagent-task>`,
the content inside that message is the delegated task from the parent agent.

Your final answer is delivered automatically if your parent is still online.
Include the result and relevant evidence in that answer; the parent does not
receive your full transcript, tool output, or reasoning. If your last action
is a message to your parent, the completion notice omits your final answer;
include the result and relevant evidence in that message.
""".strip()


class SubAgentPromptMiddleware(MiddlewareBase):  # pylint: disable=abstract-method
    """Apply DSH-style subagent identity guidance and child task protocol.

    Terminal result settlement is runtime-owned and handled by ChatService.
    """

    def __init__(
        self,
        storage: StorageBase,
        message_bus: MessageBus,
        user_id: str,
        agent_id: str,
        session_id: str,
        session_is_subagent: bool,
        enable_subagent_controls: bool,
    ) -> None:
        """Bind dependencies and request-scoped identifiers."""
        _ = (storage, message_bus, user_id, agent_id, session_id)
        self._session_is_subagent = session_is_subagent
        self._enable_subagent_controls = enable_subagent_controls

    def _build_identity_prompt(self, agent: Any) -> str:
        """Build DSH-style identity guidance for root or child agents."""
        if not self._enable_subagent_controls:
            return ""

        state = getattr(agent, "state", None)
        meta = get_subagent_meta(state)
        if self._session_is_subagent:
            parent_name = (
                meta.get("parent_name")
                if isinstance(meta, dict) and isinstance(meta.get("parent_name"), str)
                else ROOT_AGENT_NAME
            )
            self_name = agent_name_for(None, state)  # type: ignore[arg-type]
            identity = (
                f"You are a subagent delegated by agent {json.dumps(parent_name, ensure_ascii=False)}.\n"
                f"Your name is {json.dumps(self_name, ensure_ascii=False)}.\n"
                f"The root agent of your collaboration tree is {json.dumps(ROOT_AGENT_NAME, ensure_ascii=False)}."
            )
        else:
            identity = (
                f"You are the root agent. Your name is "
                f"{json.dumps(ROOT_AGENT_NAME, ensure_ascii=False)}."
            )

        guidance = [
            'To message other agents, use send_message({ agent: "<agent-name>", message: "..." }).',
            'Use list_agents({}) to find your collaborator agents and their names.',
        ]
        return f"{identity}\n\n" + "\n".join(guidance)

    async def on_system_prompt(  # type: ignore[override]
        self,
        agent: Any,
        current_prompt: str,
    ) -> str:
        """Inject DSH-style subagent identity guidance and child protocol."""
        prompt = current_prompt
        identity_prompt = self._build_identity_prompt(agent)
        if identity_prompt and identity_prompt not in prompt:
            prompt = f"{prompt}\n\n{identity_prompt}"
        if self._session_is_subagent and _SUBAGENT_SYSTEM_PROMPT_SUFFIX not in prompt:
            prompt = f"{prompt}\n\n{_SUBAGENT_SYSTEM_PROMPT_SUFFIX}"
        return prompt

    async def on_reply(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Pass through reply streaming; runtime settlement happens elsewhere."""
        async for item in next_handler(**input_kwargs):
            yield item
