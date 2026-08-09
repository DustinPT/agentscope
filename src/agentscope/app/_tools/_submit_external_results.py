# -*- coding: utf-8 -*-
"""Builtin tool for submitting externally executed tool results."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ...message import ToolCallState, ToolResultBlock, ToolResultState
from ...tool import ParamsBase
from ._session_tool_base import _SessionToolBase


class _ExternalExecutionItem(BaseModel):
    """One externally produced tool result."""

    tool_call_id: str = Field(description="Pending submitted tool call id.")
    output: str = Field(description="Tool result text to send back.")
    state: ToolResultState = Field(
        default=ToolResultState.SUCCESS,
        description="Tool result state for this external execution.",
    )


class _SubmitExternalResultsParams(ParamsBase):
    """Parameters for :class:`SubmitExternalResults`."""

    agent_id: str = Field(description="Target managed agent id.")
    session_id: str = Field(description="Target session id.")
    reply_id: str = Field(description="Reply id currently waiting for external execution.")
    execution_results: list[_ExternalExecutionItem] = Field(
        description="Results to submit for externally executed tool calls.",
        min_length=1,
    )


class SubmitExternalResults(_SessionToolBase):
    """Resume a paused session with external tool execution results."""

    name = "SubmitExternalResults"
    description = (
        "Submit external execution results for submitted tool calls and "
        "resume the managed session. Use this only when "
        "WaitNewMessages.stop_reason is require_external_execution, and "
        "only for the submitted tool calls returned by that wait result. "
        "Never submit results for asking or pending tool calls."
    )
    input_schema: dict[str, Any] = _SubmitExternalResultsParams.model_json_schema()
    is_read_only = False

    async def call(
        self,
        agent_id: str,
        session_id: str,
        reply_id: str,
        execution_results: list[dict[str, Any]],
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
        if stop_reason != "require_external_execution":
            return self._result(
                {
                    "error": (
                        "SubmitExternalResults is only allowed when the "
                        "managed session is stopped at "
                        "require_external_execution."
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

        pending_by_id = {
            tool_call.id: tool_call
            for tool_call in current_reply.get_content_blocks("tool_call")
            if tool_call.state == ToolCallState.SUBMITTED
        }
        parsed_results = [
            _ExternalExecutionItem.model_validate(item)
            for item in execution_results
        ]
        missing = [
            item.tool_call_id
            for item in parsed_results
            if item.tool_call_id not in pending_by_id
        ]
        if missing:
            return self._result(
                {
                    "error": (
                        "Some tool_call_ids are missing or are not waiting for "
                        "external execution."
                    ),
                    "missing_tool_call_ids": missing,
                    "reply_id": reply_id,
                },
                state=ToolResultState.ERROR,
            )

        result_blocks = [
            ToolResultBlock(
                id=item.tool_call_id,
                name=pending_by_id[item.tool_call_id].name,
                output=item.output,
                state=item.state,
            )
            for item in parsed_results
        ]
        try:
            await self._spawn_chat_run(
                agent_id=agent_id,
                session_id=session_id,
                input_msg=self._build_external_results_event(
                    reply_id=reply_id,
                    execution_results=result_blocks,
                ),
                task_name=f"tool-submit-external-results:{session_id}",
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
                "execution_results": [
                    block.model_dump(mode="json") for block in result_blocks
                ],
            },
        )
