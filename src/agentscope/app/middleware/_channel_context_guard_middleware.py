# -*- coding: utf-8 -*-
"""Middleware that guides channel-main-session context resets."""
from __future__ import annotations

from ...event import HintBlockEvent, ReplyStartEvent
from ...message import AssistantMsg, HintBlock
from ...middleware import MiddlewareBase
from ...tool import ToolBase
from .._tools import ChannelContextReset

_CHANNEL_CONTEXT_GUARD_PROMPT_SUFFIX = (
    "## Channel session context reset\n\n"
    "You are in a main channel session where the same session may be reused "
    "for many unrelated tasks.\n\n"
    "For each new user message, treat context relevance checking as a "
    "required first step before normal reasoning.\n\n"
    "First judge whether the existing conversation history is still "
    "relevant to the current request.\n\n"
    "If the earlier history is unrelated to the current request, call "
    "`ChannelContextReset` before any further analysis or tool usage. "
    "After it succeeds, continue the new task using the cleaned runtime "
    "context.\n\n"
    "If the earlier history is still relevant, do not call "
    "`ChannelContextReset`.\n\n"
    "Do not use `ChannelContextReset` just because the context is long; it "
    "is only for unrelated-task switches."
)

_CHANNEL_CONTEXT_GUARD_SOURCE = (
    '{"label": "System", "sublabel": "Channel Context Guard"}'
)


class ChannelContextGuardMiddleware(MiddlewareBase):  # pylint: disable=abstract-method
    """Expose the channel context reset tool and usage guidance."""

    @staticmethod
    def _build_turn_reminder_block() -> HintBlock:
        """Build the persisted reminder block for the current turn."""
        return HintBlock(
            hint=(
                "<system-reminder>"
                "Before answering the current user message, first "
                "check whether the earlier conversation history or "
                "compressed summary is unrelated to the current "
                "request. If it is unrelated, call "
                "`ChannelContextReset` now before doing any other "
                "reasoning or tool usage. If it is still relevant, "
                "do not call the tool."
                "</system-reminder>"
            ),
            source=_CHANNEL_CONTEXT_GUARD_SOURCE,
        )

    @staticmethod
    def _needs_current_turn_reminder(
        agent,
        inputs,
    ) -> bool:
        """Return whether the current turn should receive a stronger reminder."""
        if not isinstance(inputs, (list, tuple)):
            inputs = [inputs]

        incoming_user_msgs = [
            msg
            for msg in inputs
            if getattr(msg, "role", None) == "user"
        ]
        if not incoming_user_msgs:
            return False

        has_prior_user_context = any(
            msg.role == "user"
            for msg in agent.state.context
        )
        return has_prior_user_context or bool(agent.state.summary)

    async def on_system_prompt(  # type: ignore[override]
        self,
        agent,
        current_prompt: str,
    ) -> str:
        """Append channel-specific context reset guidance once."""
        del agent
        if _CHANNEL_CONTEXT_GUARD_PROMPT_SUFFIX in current_prompt:
            return current_prompt
        return (
            f"{current_prompt}\n\n{_CHANNEL_CONTEXT_GUARD_PROMPT_SUFFIX}"
        )

    async def on_reply(
        self,
        agent,
        input_kwargs: dict,
        next_handler,
    ):
        """Inject a persisted reminder into the current reply context."""
        inputs = input_kwargs["inputs"]
        should_inject_reminder = self._needs_current_turn_reminder(
            agent,
            inputs,
        )
        reminder_injected = False

        async for item in next_handler(**input_kwargs):
            yield item
            if (
                should_inject_reminder
                and not reminder_injected
                and isinstance(item, ReplyStartEvent)
            ):
                reminder_injected = True
                reminder_block = self._build_turn_reminder_block()
                if len(agent.state.context) > 0:
                    last_msg = agent.state.context[-1]
                    if (
                        last_msg.role == "assistant"
                        and last_msg.name == agent.name
                        and last_msg.id == agent.state.reply_id
                    ):
                        last_msg.content.append(reminder_block)
                    else:
                        agent.state.context.append(
                            AssistantMsg(
                                id=agent.state.reply_id,
                                name=agent.name,
                                content=[reminder_block],
                            ),
                        )
                else:
                    agent.state.context.append(
                        AssistantMsg(
                            id=agent.state.reply_id,
                            name=agent.name,
                            content=[reminder_block],
                        ),
                    )
                yield HintBlockEvent(
                    reply_id=agent.state.reply_id,
                    block_id=reminder_block.id,
                    source=reminder_block.source,
                    hint=reminder_block.hint,
                )

    async def list_tools(self) -> list[ToolBase]:
        """Return the context-reset tool for this agent run."""
        return [ChannelContextReset()]
