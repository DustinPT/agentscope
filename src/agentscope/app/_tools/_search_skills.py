# -*- coding: utf-8 -*-
"""Builtin tool for searching user-owned skills."""

from __future__ import annotations

import json
from typing import Any

from pydantic import Field, field_validator

from ...message import TextBlock, ToolResultState
from ...permission import PermissionContext, PermissionDecision, PermissionBehavior
from ...tool import ParamsBase, ToolBase, ToolChunk
from .._service._skill_library import SkillLibraryService


class _SearchSkillsParams(ParamsBase):
    """The params of the search skills tool."""

    query: str = Field(
        description=(
            "Describe in English the capability you need, the task you want "
            "to solve, or the tool behavior you are looking for."
        ),
        min_length=1,
    )
    limit: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum number of candidate skills to return.",
    )

    @field_validator("query")
    @classmethod
    def validate_query_is_english(cls, value: str) -> str:
        """Require an English-only query for skill retrieval."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("Query must not be empty.")
        if not normalized.isascii():
            raise ValueError(
                "Query must be written in English using ASCII characters only.",
            )
        return normalized


class SearchSkills(ToolBase):
    """Search top-N skills from the current user's skill library."""

    name = "SearchSkills"
    description = (
        "Find skills that may help with the current task. Use this before "
        "downloading a skill when you do not already know the exact skill "
        "name. Provide a short description of the capability you need, and "
        "the tool will return the most relevant skill names and summaries."
    )
    input_schema: dict[str, Any] = _SearchSkillsParams.model_json_schema()
    is_concurrency_safe: bool = False
    is_read_only = True
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
            message="Searching owned skills is always allowed.",
        )

    def _result(
        self,
        payload: dict[str, Any],
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
        query: str,
        limit: int = 5,
    ):
        hits = await self._skill_library_service.search_skills(
            user_id=self._user_id,
            query=query,
            limit=limit,
        )
        return self._result(
            {
                "skills": [
                    {
                        "name": item.record.name,
                        "description": item.record.description,
                    }
                    for item in hits
                ],
            },
        )
