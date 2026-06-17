# -*- coding: utf-8 -*-
"""Helpers for inspecting the current reply state of an agent."""
from __future__ import annotations

from typing import Any

from ..message import Msg, ToolCallState


def get_current_reply_msg(
    agent: Any,
    fallback_msg: Msg | None = None,
) -> Msg | None:
    """Return the persisted assistant message for the current reply.

    When a reply pauses for outside interaction, the agent may yield a
    synthetic waiting ``Msg`` that is not persisted into
    ``agent.state.context``. Prefer the context-backed assistant message so
    callers can inspect the real tool-call state of the current reply.
    """
    if agent.state.context:
        last_msg = agent.state.context[-1]
        if (
            last_msg.role == "assistant"
            and last_msg.name == agent.name
            and last_msg.id == agent.state.reply_id
        ):
            return last_msg

    return fallback_msg


def is_reply_awaiting_tool_interaction(agent: Any) -> bool:
    """Return whether the current reply is waiting for outside interaction."""
    current_reply_msg = get_current_reply_msg(agent)
    if current_reply_msg is None:
        return False

    return any(
        tool_call.state in (ToolCallState.ASKING, ToolCallState.SUBMITTED)
        for tool_call in current_reply_msg.get_content_blocks("tool_call")
    )
