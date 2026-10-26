# -*- coding: utf-8 -*-
"""Builtin tool for deleting one skill from the skill library."""

from __future__ import annotations

import json
from typing import Any

from pydantic import Field

from ...message import TextBlock, ToolResultState
from ...permission import PermissionBehavior, PermissionContext, PermissionDecision
from ...tool import ParamsBase, ToolBase, ToolChunk
from .._service._skill_library import SkillLibraryService


class _DeleteSkillFromLibraryParams(ParamsBase):
    """The params of the delete skill tool."""

    skill: str = Field(
        description="Exact skill name to delete from the current user's skill library.",
        min_length=1,
    )


class DeleteSkillFromLibrary(ToolBase):
    """Delete one user-owned skill and all of its versions from the skill library."""

    name = "DeleteSkillFromLibrary"
    description = (
        "Delete a skill from the current user's skill library by its exact "
        "name. This removes the current version and all historical versions."
    )
    input_schema: dict[str, Any] = _DeleteSkillFromLibraryParams.model_json_schema()
    is_concurrency_safe: bool = False
    is_read_only = False
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(
        self,
        *,
        user_id: str,
        skill_library_service: SkillLibraryService,
    ) -> None:
        super().__init__()
        self._user_id = user_id
        self._skill_library_service = skill_library_service

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        del tool_input, context
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="Managing owned skills is allowed.",
        )

    def _result(
        self,
        payload: dict[str, object],
        *,
        state: ToolResultState = ToolResultState.SUCCESS,
    ) -> ToolChunk:
        """Build a compact JSON result for the agent."""
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
            state=state,
            metadata=payload,
        )

    async def call(
        self,
        skill: str,
    ):
        try:
            await self._skill_library_service.delete_skill(
                user_id=self._user_id,
                skill_name=skill,
            )
            return self._result(
                {
                    "skill": skill,
                    "deleted": True,
                },
            )
        except Exception as exc:  # noqa: BLE001
            return self._result(
                {
                    "error": str(exc),
                    "skill": skill,
                },
                state=ToolResultState.ERROR,
            )
