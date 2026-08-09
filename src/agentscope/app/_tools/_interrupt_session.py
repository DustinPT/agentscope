# -*- coding: utf-8 -*-
"""Builtin tool for interrupting one managed session."""
from __future__ import annotations

from typing import Any

from pydantic import Field

from ...message import ToolResultState
from ...tool import ParamsBase
from ._session_tool_base import _SessionToolBase


class _InterruptSessionParams(ParamsBase):
    """Parameters for :class:`InterruptSession`."""

    agent_id: str = Field(description="Target managed agent id.")
    session_id: str = Field(description="Target session id to interrupt.")
    reason: str | None = Field(
        default=None,
        description="Optional human-readable reason recorded for the interruption.",
    )


class InterruptSession(_SessionToolBase):
    """Interrupt a running or waiting managed session."""

    name = "InterruptSession"
    description = (
        "Interrupt a managed session in any state. Use this as the only "
        "allowed operation when you must abort a running or stuck session, "
        "or when you do not want to continue the current waiting round."
    )
    input_schema: dict[str, Any] = _InterruptSessionParams.model_json_schema()
    is_read_only = False

    async def call(
        self,
        agent_id: str,
        session_id: str,
        reason: str | None = None,
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

        released, updated_session = await self._interrupt_managed_session(
            agent_id=agent_id,
            session_id=session_id,
            reason=reason,
        )
        stop_reason = None
        reply_id = None
        tool_calls: list[dict[str, Any]] = []
        if updated_session is not None:
            stop_reason, reply_id, tool_calls = self._resolve_session_stop_reason(
                updated_session,
            )

        return self._result(
            {
                "status": "interrupt_requested",
                "agent_id": agent_id,
                "session_id": session_id,
                "released": released,
                "reason": reason,
                "stop_reason": stop_reason,
                "reply_id": reply_id,
                "tool_calls": tool_calls,
            },
        )
