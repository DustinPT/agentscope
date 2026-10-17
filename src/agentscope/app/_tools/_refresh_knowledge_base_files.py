# -*- coding: utf-8 -*-
"""Builtin tool for rebuilding files in the current user's KB."""

from __future__ import annotations

import json
import os
from typing import Any

from pydantic import Field, field_validator

from ...message import TextBlock, ToolResultState
from ...permission import PermissionBehavior, PermissionContext, PermissionDecision
from ...tool import ParamsBase, ToolBase, ToolChunk
from .._service._knowledge_base import KnowledgeBaseService


class _RefreshKnowledgeBaseFilesParams(ParamsBase):
    """The params of the refresh-files knowledge-base tool."""

    file_paths: list[str] = Field(
        description=(
            "One or more absolute local file paths to rebuild immediately in "
            "the current user's knowledge base."
        ),
        min_length=1,
    )
    wait_for_index: bool = Field(
        default=False,
        description=(
            "Whether to wait until queued rebuilds finish indexing before "
            "returning."
        ),
    )

    @field_validator("file_paths")
    @classmethod
    def validate_file_paths(cls, value: list[str]) -> list[str]:
        """Require absolute file paths."""
        normalized: list[str] = []
        for item in value:
            path = item.strip()
            if not path:
                raise ValueError("File paths must not be empty.")
            if not os.path.isabs(path):
                raise ValueError("Each file path must be an absolute local path.")
            normalized.append(path)
        return normalized


class RefreshKnowledgeBaseFiles(ToolBase):
    """Immediately re-extract and re-index files in the current user's KB."""

    name = "RefreshKnowledgeBaseFiles"
    description = (
        "Immediately rebuild one or more files in the current user's "
        "knowledge base so recent file changes can take effect without "
        "waiting for background polling. Optionally wait until the rebuild "
        "finishes indexing."
    )
    input_schema: dict[str, Any] = _RefreshKnowledgeBaseFilesParams.model_json_schema()
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
            message="Refreshing knowledge-base files is allowed.",
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
        file_paths: list[str],
        wait_for_index: bool = False,
    ):
        payload = await self._knowledge_base_service.refresh_files(
            user_id=self._user_id,
            file_paths=file_paths,
            wait_for_index=wait_for_index,
        )
        return self._result(payload)
