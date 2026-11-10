# -*- coding: utf-8 -*-
"""Middleware implementing child-session prompt guidance for subagents."""

from __future__ import annotations

from typing import Any, AsyncGenerator, Callable

from ..message_bus import MessageBus
from ..storage import StorageBase
from ...middleware import MiddlewareBase

_SUBAGENT_SYSTEM_PROMPT_SUFFIX = """
How to handle delegated sub-agent tasks:
- When you receive a message wrapped in `<subagent-task ...>...</subagent-task>`,
  the content inside that message is the delegated task from the parent agent.
- Complete that delegated task directly.
- When you are done, end your turn with one complete, self-contained final
  answer.
- The parent agent will receive your last assistant reply as the delegated
  task result. Treat your final answer as that last assistant reply.
- Do not refer to earlier messages with phrases like "as explained above" or
  "see previous analysis" when the missing context can be restated directly in
  the final answer.
- After sending the final answer, do not call tools, do not continue
  reasoning, and do not send any additional assistant message.
- If another instruction asks you to do post-task housekeeping such as version
  checks, telemetry, or optional follow-up actions, do that before
  the final answer when possible.
- If such housekeeping would require acting after the final answer, skip it
  instead of sending a second assistant message.
- If the final answer includes attachments or other data blocks, include them
  in the same last assistant reply.

Use a single complete, self-contained final answer as your last assistant
reply.
""".strip()


class SubAgentPromptMiddleware(MiddlewareBase):  # pylint: disable=abstract-method
    """Apply only child-session prompt guidance.

    Terminal result settlement is runtime-owned and handled by ChatService.
    """

    def __init__(
        self,
        storage: StorageBase,
        message_bus: MessageBus,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> None:
        """Bind dependencies and request-scoped identifiers."""
        _ = (storage, message_bus, user_id, agent_id, session_id)

    async def on_system_prompt(  # type: ignore[override]
        self,
        agent: Any,
        current_prompt: str,
    ) -> str:
        """Inject child-session protocol into the system prompt."""
        if _SUBAGENT_SYSTEM_PROMPT_SUFFIX in current_prompt:
            return current_prompt
        return f"{current_prompt}\n\n{_SUBAGENT_SYSTEM_PROMPT_SUFFIX}"

    async def on_reply(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Pass through reply streaming; runtime settlement happens elsewhere."""
        async for item in next_handler(**input_kwargs):
            yield item
