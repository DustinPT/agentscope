# -*- coding: utf-8 -*-
"""Helpers for inspecting the current reply state of an agent."""
from __future__ import annotations

from typing import Any

from ..message import Msg, ToolCallState

REPLY_CHECKPOINT_REPLAY_ENTRY_ID_METADATA_KEY = (
    "checkpoint_replay_entry_id"
)
"""Metadata key storing the last replay-log entry covered by a checkpoint."""


def get_current_reply_msg(
    agent: Any,
) -> Msg | None:
    """Return the persisted assistant message for the current reply.

    The authority of the current reply message is the last assistant message
    in ``agent.state.context`` that belongs to the current agent.
    """
    if agent.state.context:
        last_msg = agent.state.context[-1]
        if last_msg.role == "assistant" and last_msg.name == agent.name:
            return last_msg

    return None


def get_reply_checkpoint_replay_entry_id(msg: Msg | None) -> str | None:
    """Return the replay-log entry id already covered by *msg*."""
    if msg is None:
        return None
    entry_id = msg.metadata.get(
        REPLY_CHECKPOINT_REPLAY_ENTRY_ID_METADATA_KEY,
    )
    return entry_id if isinstance(entry_id, str) and entry_id else None


def set_reply_checkpoint_replay_entry_id(
    msg: Msg,
    entry_id: str | None,
) -> None:
    """Persist the replay-log boundary already folded into *msg*."""
    if not entry_id:
        return
    msg.metadata[REPLY_CHECKPOINT_REPLAY_ENTRY_ID_METADATA_KEY] = entry_id


def is_reply_awaiting_tool_interaction(agent: Any) -> bool:
    """Return whether the current reply is waiting for outside interaction."""
    current_reply_msg = get_current_reply_msg(agent)
    if current_reply_msg is None:
        return False

    return any(
        tool_call.state in (ToolCallState.ASKING, ToolCallState.SUBMITTED)
        for tool_call in current_reply_msg.get_content_blocks("tool_call")
    )
