# -*- coding: utf-8 -*-
"""Builtin tool for searching the current user's knowledge base."""

from __future__ import annotations

import json
import os
from typing import Any

from pydantic import Field, field_validator

from ...message import TextBlock, ToolResultState
from ...permission import PermissionBehavior, PermissionContext, PermissionDecision
from ...tool import ParamsBase, ToolBase, ToolChunk
from .._service._knowledge_base import KnowledgeBaseService


class _SearchKnowledgeBaseParams(ParamsBase):
    """The params of the knowledge-base search tool."""

    query: str = Field(
        description="What to search for in the current user's knowledge base.",
        min_length=1,
    )
    top_k: int = Field(
        default=10,
        ge=1,
        le=20,
        description="Maximum number of knowledge-base hits to return.",
    )
    directory_paths: list[str] | None = Field(
        default=None,
        description=(
            "Optional absolute directory paths used to limit search scope to "
            "those directories and their subdirectories."
        ),
    )

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        """Require a non-empty trimmed query."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("Query must not be empty.")
        return normalized

    @field_validator("directory_paths")
    @classmethod
    def validate_directory_paths(
        cls,
        value: list[str] | None,
    ) -> list[str] | None:
        """Require absolute directory paths when provided."""
        if value is None:
            return None
        normalized: list[str] = []
        for item in value:
            path = item.strip()
            if not path:
                raise ValueError("Directory paths must not be empty.")
            if not os.path.isabs(path):
                raise ValueError(
                    "Each directory path must be an absolute local path.",
                )
            normalized.append(path)
        return normalized


class SearchKnowledgeBase(ToolBase):
    """Search the current user's KB with fixed hybrid retrieval settings."""

    name = "SearchKnowledgeBase"
    description = (
        "Search the current user's knowledge base with fixed hybrid retrieval. "
        "Use this when document content may answer the task or when you need "
        "to discover relevant files before reading nearby chunks."
    )
    input_schema: dict[str, Any] = _SearchKnowledgeBaseParams.model_json_schema()
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
        knowledge_base_service: KnowledgeBaseService,
    ) -> None:
        super().__init__()
        self._user_id = user_id
        self._knowledge_base_service = knowledge_base_service

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        del tool_input, context
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="Searching the current user's knowledge base is allowed.",
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
        top_k: int = 10,
        directory_paths: list[str] | None = None,
    ):
        payload = await self._knowledge_base_service.search(
            user_id=self._user_id,
            query=query,
            top_k=top_k,
            directory_paths=directory_paths,
        )
        return self._result(payload)
