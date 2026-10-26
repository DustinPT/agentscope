# -*- coding: utf-8 -*-
"""Middleware that guides channel-main-session context resets."""
from __future__ import annotations

from ...middleware import MiddlewareBase
from ...tool import ToolBase
from .._tools import ChannelContextReset

_CHANNEL_CONTEXT_GUARD_PROMPT_SUFFIX = (
    "## Channel session context reset\n\n"
    "You are in a main channel session where the same session may be reused "
    "for many unrelated tasks.\n\n"
    "For each new user message, first judge whether the existing "
    "conversation history is still relevant to the current request.\n\n"
    "If the earlier history is unrelated to the current request, call "
    "`ChannelContextReset` before any further analysis or tool usage. "
    "After it succeeds, continue the new task using the cleaned runtime "
    "context.\n\n"
    "If the earlier history is still relevant, do not call "
    "`ChannelContextReset`.\n\n"
    "Do not use `ChannelContextReset` just because the context is long; it "
    "is only for unrelated-task switches."
)


class ChannelContextGuardMiddleware(MiddlewareBase):  # pylint: disable=abstract-method
    """Expose the channel context reset tool and usage guidance."""

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

    async def list_tools(self) -> list[ToolBase]:
        """Return the context-reset tool for this agent run."""
        return [ChannelContextReset()]
