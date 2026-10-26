# -*- coding: utf-8 -*-
"""Builtin tool for clearing irrelevant channel-session history."""
from __future__ import annotations

import json
from typing import Any

from ...message import TextBlock
from ...permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from ...state import AgentState
from ...tool import ParamsBase, ToolBase
from ...tool._response import ToolChunk


class _ChannelContextResetParams(ParamsBase):
    """Parameters for :class:`ChannelContextReset`."""


class ChannelContextReset(ToolBase):
    """Clear runtime history before the current user message."""

    name = "ChannelContextReset"
    description = (
        "Use this tool only in a main channel session when the prior "
        "conversation history is unrelated to the current user message. "
        "Call it before any other analysis or tool usage for the new task. "
        "It clears all runtime context messages before the current user "
        "message and also clears the compressed summary. Do not use it just "
        "because the context is long."
    )
    input_schema: dict[str, Any] = _ChannelContextResetParams.model_json_schema()
    is_concurrency_safe = False
    is_read_only = False
    is_state_injected = True
    is_external_tool = False
    is_mcp = False
    mcp_name: str | None = None

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Allow the tool whenever it is attached to the current run."""
        del tool_input, context
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="ChannelContextReset is allowed when attached to the agent.",
        )

    @staticmethod
    def _result(payload: dict[str, Any]) -> ToolChunk:
        """Build a compact JSON result for the model."""
        return ToolChunk(
            content=[
                TextBlock(
                    text=json.dumps(
                        payload,
                        ensure_ascii=False,
                        indent=2,
                    ),
                ),
            ],
            metadata=payload,
        )

    async def call(
        self,
        _agent_state: AgentState,
    ) -> ToolChunk:
        """Clear runtime context before the latest user message."""
        context = list(_agent_state.context)
        keep_from_index = None
        for index in range(len(context) - 1, -1, -1):
            if context[index].role == "user":
                keep_from_index = index
                break

        if keep_from_index is None:
            summary_cleared = bool(_agent_state.summary)
            _agent_state.summary = ""
            return self._result(
                {
                    "cleared_message_count": 0,
                    "remaining_message_count": len(context),
                    "summary_cleared": summary_cleared,
                },
            )

        _agent_state.context = context[keep_from_index:]
        cleared_message_count = keep_from_index
        summary_cleared = bool(_agent_state.summary)
        _agent_state.summary = ""

        return self._result(
            {
                "cleared_message_count": cleared_message_count,
                "remaining_message_count": len(_agent_state.context),
                "summary_cleared": summary_cleared,
            },
        )
