# -*- coding: utf-8 -*-
"""Builtin tool for starting one chat run on a managed session."""
from __future__ import annotations

from typing import Any

from pydantic import Field

from ...message import ToolResultState, UserMsg
from ...tool import ParamsBase
from ._session_tool_base import _SessionToolBase


class _SendSessionMessageParams(ParamsBase):
    """Parameters for :class:`SendSessionMessage`."""

    agent_id: str = Field(description="Target managed agent id.")
    session_id: str = Field(description="Existing session id to continue.")
    message: str = Field(description="The user message to send to the session.")


class SendSessionMessage(_SessionToolBase):
    """Start one chat run with a plain user message."""

    name = "SendSessionMessage"
    description = (
        "Send one user message to a managed session and start a chat run."
    )
    input_schema: dict[str, Any] = _SendSessionMessageParams.model_json_schema()
    is_read_only = False

    async def call(
        self,
        agent_id: str,
        session_id: str,
        message: str,
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

        try:
            await self._spawn_chat_run(
                agent_id=agent_id,
                session_id=session_id,
                input_msg=UserMsg("user", message),
                task_name=f"tool-send-session-message:{session_id}",
            )
        except Exception as exc:  # noqa: BLE001
            return self._result(
                {
                    "error": str(exc),
                    "agent_id": agent_id,
                    "session_id": session_id,
                },
                state=ToolResultState.ERROR,
            )

        return self._result(
            {
                "status": "started",
                "agent_id": agent_id,
                "session_id": session_id,
                "message": message,
            },
        )
