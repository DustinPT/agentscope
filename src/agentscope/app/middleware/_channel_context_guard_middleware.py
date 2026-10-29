# -*- coding: utf-8 -*-
"""Middleware that guides channel-main-session context resets."""
from __future__ import annotations

from datetime import datetime

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
_DEFAULT_REMINDER_IDLE_SECONDS = 1800.0
"""Minimum idle time before injecting the per-turn reminder again."""


class ChannelContextGuardMiddleware(MiddlewareBase):  # pylint: disable=abstract-method
    """Expose the channel context reset tool and usage guidance."""

    def __init__(
        self,
        *,
        reminder_idle_seconds: float = _DEFAULT_REMINDER_IDLE_SECONDS,
    ) -> None:
        """Store the idle threshold used to suppress frequent reminders."""
        self._reminder_idle_seconds = max(0.0, reminder_idle_seconds)

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
    def _parse_timestamp(value: str | None) -> float | None:
        """Parse one ISO-format timestamp into epoch seconds."""
        if not value:
            return None

        try:
            return datetime.fromisoformat(
                value.replace("Z", "+00:00"),
            ).timestamp()
        except ValueError:
            return None

    @classmethod
    def _get_msg_timestamp(cls, msg) -> float | None:
        """Return the most suitable timestamp for one message."""
        return cls._parse_timestamp(
            getattr(msg, "finished_at", None),
        ) or cls._parse_timestamp(
            getattr(msg, "created_at", None),
        )

    @classmethod
    def _get_latest_context_interaction_timestamp(
        cls,
        agent,
    ) -> float | None:
        """Return the latest prior user/assistant interaction timestamp."""
        for msg in reversed(agent.state.context):
            if getattr(msg, "role", None) not in {"user", "assistant"}:
                continue
            timestamp = cls._get_msg_timestamp(msg)
            if timestamp is not None:
                return timestamp
        return None

    @classmethod
    def _get_latest_incoming_user_timestamp(
        cls,
        incoming_user_msgs,
    ) -> float | None:
        """Return the latest timestamp among incoming user messages."""
        latest_timestamp = None
        for msg in incoming_user_msgs:
            timestamp = cls._get_msg_timestamp(msg)
            if timestamp is None:
                continue
            if latest_timestamp is None or timestamp > latest_timestamp:
                latest_timestamp = timestamp
        return latest_timestamp

    def _needs_current_turn_reminder(
        self,
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
        has_prior_context = has_prior_user_context or bool(agent.state.summary)
        if not has_prior_context:
            return False

        if self._reminder_idle_seconds <= 0:
            return True

        current_user_timestamp = self._get_latest_incoming_user_timestamp(
            incoming_user_msgs,
        )
        latest_interaction_timestamp = (
            self._get_latest_context_interaction_timestamp(agent)
        )
        if (
            current_user_timestamp is None
            or latest_interaction_timestamp is None
        ):
            return True

        return (
            current_user_timestamp - latest_interaction_timestamp
            > self._reminder_idle_seconds
        )

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
