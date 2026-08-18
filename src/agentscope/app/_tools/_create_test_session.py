# -*- coding: utf-8 -*-
"""Builtin tool for creating a test session in the current workspace."""
from __future__ import annotations

from typing import Any

from pydantic import Field

from ...message import ToolResultState
from ...permission import PermissionMode
from ...tool import ParamsBase
from ..storage import SessionConfig
from ...state import AgentState
from ...permission import PermissionContext
from ._session_tool_base import _SessionToolBase


class _CreateTestSessionParams(ParamsBase):
    """Parameters for :class:`CreateTestSession`."""

    agent_id: str = Field(description="Target managed agent id to test.")
    session_name: str = Field(
        description="Required test-session name shown in the UI.",
        min_length=1,
    )


class CreateTestSession(_SessionToolBase):
    """Create a new test session bound to the current workspace."""

    name = "CreateTestSession"
    description = (
        "Create a new test session for a managed agent. "
        "The session always reuses the current workspace, uses the "
        "user's unspecified_high model, and sets permission mode to bypass."
    )
    input_schema: dict[str, Any] = _CreateTestSessionParams.model_json_schema()
    is_read_only = False

    async def call(
        self,
        agent_id: str,
        session_name: str,
    ):
        agent = await self._get_owned_agent(agent_id)
        if agent is None:
            return self._result(
                {"error": f"Agent '{agent_id}' not found."},
                state=ToolResultState.ERROR,
            )

        user_record = await self._get_user_record()
        model_config = None
        if user_record is not None:
            model_config = user_record.global_default_models.unspecified_high
        if model_config is None:
            return self._result(
                {
                    "error": (
                        "User default model 'unspecified_high' is not configured."
                    ),
                },
                state=ToolResultState.ERROR,
            )

        session = await self._storage.upsert_session(
            user_id=self._user_id,
            agent_id=agent_id,
            config=SessionConfig(
                workspace_id=self._workspace_id,
                name=session_name,
                chat_model_config=model_config,
                permission_mode=PermissionMode.BYPASS,
            ),
            state=AgentState(
                permission_context=PermissionContext(
                    mode=PermissionMode.BYPASS,
                ),
            ),
        )
        return self._result(
            {
                "session_id": session.id,
                "workspace_id": session.config.workspace_id,
                "session_name": session.config.name,
                "agent_id": agent_id,
                "chat_model_config": model_config.model_dump(mode="json"),
                "permission_mode": PermissionMode.BYPASS.value,
            },
        )
