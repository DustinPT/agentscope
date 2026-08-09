# -*- coding: utf-8 -*-
"""Builtin tool for incrementally waiting for new session messages."""
from __future__ import annotations

import asyncio
from typing import Any

from pydantic import Field

from ...message import Msg, ToolResultState
from ...tool import ParamsBase
from ._session_tool_base import _SessionToolBase

_WAIT_POLL_INTERVAL_SECONDS = 0.5


class _WaitNewMessagesParams(ParamsBase):
    """Parameters for :class:`WaitNewMessages`."""

    agent_id: str = Field(description="Target managed agent id.")
    session_id: str = Field(description="Target session id.")
    last_message_id: str | None = Field(
        default=None,
        description=(
            "Last message id already consumed by the caller. "
            "Leave empty on the first call."
        ),
    )
    last_block_id: str | None = Field(
        default=None,
        description=(
            "Last block id already consumed inside last_message_id. "
            "Leave empty on the first call."
        ),
    )
    timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        le=600,
        description="Maximum time to wait before returning timeout.",
    )


class WaitNewMessages(_SessionToolBase):
    """Wait for incremental session output until the run stops or times out."""

    name = "WaitNewMessages"
    description = (
        "Wait for new messages and content blocks in a managed session. "
        "Returns only the unread delta plus the single allowed next action: "
        "reply_completed -> SendSessionMessage, "
        "require_user_confirm -> ConfirmToolCalls for asking tool calls, "
        "require_external_execution -> SubmitExternalResults for submitted "
        "tool calls. Never mix multiple progress operations in one round."
    )
    input_schema: dict[str, Any] = _WaitNewMessagesParams.model_json_schema()
    is_read_only = True

    def _collect_delta(
        self,
        messages: list[Msg],
        *,
        last_message_id: str | None,
        last_block_id: str | None,
    ) -> list[dict[str, Any]]:
        """Return only the messages / blocks the caller has not consumed."""
        if not messages:
            return []

        start_index = 0
        if last_message_id is not None:
            matched_index = next(
                (i for i, msg in enumerate(messages) if msg.id == last_message_id),
                None,
            )
            if matched_index is not None:
                start_index = matched_index

        new_messages: list[dict[str, Any]] = []
        for index in range(start_index, len(messages)):
            msg = messages[index]
            new_blocks = list(msg.content)
            if (
                index == start_index
                and last_message_id is not None
                and msg.id == last_message_id
            ):
                if last_block_id is None:
                    new_blocks = list(msg.content)
                else:
                    block_index = next(
                        (
                            block_offset
                            for block_offset, block in enumerate(msg.content)
                            if block.id == last_block_id
                        ),
                        None,
                    )
                    if block_index is None:
                        new_blocks = list(msg.content)
                    else:
                        new_blocks = list(msg.content[block_index + 1 :])
            if not new_blocks and msg.id == last_message_id:
                continue
            new_messages.append(
                {
                    "message": self._serialize_message(msg),
                    "new_blocks": [
                        self._serialize_block(block) for block in new_blocks
                    ],
                },
            )
        return new_messages

    async def call(
        self,
        agent_id: str,
        session_id: str,
        last_message_id: str | None = None,
        last_block_id: str | None = None,
        timeout_seconds: float = 30.0,
    ):
        session = await self._get_owned_session(
            agent_id=agent_id,
            session_id=session_id,
        )
        if session is None:
            return self._result(
                {"error": f"Session '{session_id}' not found for agent '{agent_id}'."},
                state=ToolResultState.ERROR,
            )

        deadline = asyncio.get_running_loop().time() + timeout_seconds
        observed_running = False
        observed_activity = False

        while True:
            session = await self._get_owned_session(
                agent_id=agent_id,
                session_id=session_id,
            )
            if session is None:
                return self._result(
                    {
                        "error": (
                            f"Session '{session_id}' disappeared while waiting."
                        ),
                    },
                    state=ToolResultState.ERROR,
                )

            messages = await self._list_all_messages(session_id)
            new_messages = self._collect_delta(
                messages,
                last_message_id=last_message_id,
                last_block_id=last_block_id,
            )
            if new_messages:
                observed_activity = True

            is_running = await self._message_bus.session_is_running(session_id)
            if is_running:
                observed_running = True
            stop_reason, reply_id, tool_calls = self._resolve_session_stop_reason(
                session,
            )
            if not (observed_activity or observed_running) and stop_reason == (
                "reply_completed"
            ):
                stop_reason = None
                reply_id = None
                tool_calls = []
            if not is_running and stop_reason is not None:
                latest_message_id = messages[-1].id if messages else last_message_id
                latest_block_id = (
                    messages[-1].content[-1].id
                    if messages and messages[-1].content
                    else last_block_id
                )
                return self._result(
                    {
                        "agent_id": agent_id,
                        "session_id": session_id,
                        "new_messages": new_messages,
                        "latest_message_id": latest_message_id,
                        "latest_block_id": latest_block_id,
                        "stop_reason": stop_reason,
                        "reply_id": reply_id,
                        "tool_calls": tool_calls,
                    },
                )

            if asyncio.get_running_loop().time() >= deadline:
                latest_message_id = messages[-1].id if messages else last_message_id
                latest_block_id = (
                    messages[-1].content[-1].id
                    if messages and messages[-1].content
                    else last_block_id
                )
                return self._result(
                    {
                        "agent_id": agent_id,
                        "session_id": session_id,
                        "new_messages": new_messages,
                        "latest_message_id": latest_message_id,
                        "latest_block_id": latest_block_id,
                        "stop_reason": "timeout",
                        "reply_id": None,
                        "tool_calls": [],
                    },
                )

            await asyncio.sleep(_WAIT_POLL_INTERVAL_SECONDS)
