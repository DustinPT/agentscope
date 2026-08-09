# -*- coding: utf-8 -*-
"""Builtin tool for confirming or rejecting pending tool calls."""
from __future__ import annotations

from typing import Any

from pydantic import Field

from ...message import ToolCallState, ToolResultState
from ...tool import ParamsBase
from ._session_tool_base import _SessionToolBase


class _ConfirmToolCallsParams(ParamsBase):
    """Parameters for :class:`ConfirmToolCalls`."""

    agent_id: str = Field(description="Target managed agent id.")
    session_id: str = Field(description="Target session id.")
    reply_id: str = Field(description="Reply id currently waiting for confirmation.")
    tool_call_ids: list[str] = Field(
        description="Tool call ids to confirm or reject.",
        min_length=1,
    )
    confirmed: bool = Field(description="Whether to allow these tool calls.")


class ConfirmToolCalls(_SessionToolBase):
    """Resume a paused session with human confirmation results."""

    name = "ConfirmToolCalls"
    description = (
        "Confirm or reject asking tool calls for a managed session and "
        "resume the session. Use this only when WaitNewMessages.stop_reason "
        "is require_user_confirm, and only for the asking tool calls "
        "returned by that wait result."
    )
    input_schema: dict[str, Any] = _ConfirmToolCallsParams.model_json_schema()
    is_read_only = False

    async def call(
        self,
        agent_id: str,
        session_id: str,
        reply_id: str,
        tool_call_ids: list[str],
        confirmed: bool,
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
        stop_reason, current_reply_id, actionable_tool_calls = (
            self._resolve_session_stop_reason(session)
        )
        if stop_reason != "require_user_confirm":
            return self._result(
                {
                    "error": (
                        "ConfirmToolCalls is only allowed when the managed "
                        "session is stopped at require_user_confirm."
                    ),
                    "stop_reason": stop_reason,
                    "reply_id": current_reply_id,
                    "tool_calls": actionable_tool_calls,
                },
                state=ToolResultState.ERROR,
            )

        current_reply = self._get_current_reply(session)
        if current_reply is None or current_reply.id != reply_id:
            return self._result(
                {"error": f"Reply '{reply_id}' is not the current pending reply."},
                state=ToolResultState.ERROR,
            )

        tool_calls = [
            tool_call
            for tool_call in current_reply.get_content_blocks("tool_call")
            if tool_call.id in set(tool_call_ids)
            and tool_call.state == ToolCallState.ASKING
        ]
        if len(tool_calls) != len(set(tool_call_ids)):
            return self._result(
                {
                    "error": (
                        "Some tool_call_ids are missing or are not waiting for "
                        "user confirmation."
                    ),
                    "reply_id": reply_id,
                },
                state=ToolResultState.ERROR,
            )

        try:
            await self._spawn_chat_run(
                agent_id=agent_id,
                session_id=session_id,
                input_msg=self._build_confirm_event(
                    reply_id=reply_id,
                    tool_calls=tool_calls,
                    confirmed=confirmed,
                ),
                task_name=f"tool-confirm-tool-calls:{session_id}",
            )
        except Exception as exc:  # noqa: BLE001
            return self._result(
                {"error": str(exc), "reply_id": reply_id},
                state=ToolResultState.ERROR,
            )

        return self._result(
            {
                "status": "started",
                "agent_id": agent_id,
                "session_id": session_id,
                "reply_id": reply_id,
                "confirmed": confirmed,
                "tool_call_ids": tool_call_ids,
            },
        )
