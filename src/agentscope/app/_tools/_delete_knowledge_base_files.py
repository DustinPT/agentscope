# -*- coding: utf-8 -*-
"""Builtin tool for deleting file records from the current user's KB."""

from __future__ import annotations

import json
import os
from typing import Any

from pydantic import Field, field_validator

from ...message import TextBlock, ToolResultState
from ...permission import PermissionBehavior, PermissionContext, PermissionDecision
from ...tool import ParamsBase, ToolBase, ToolChunk
from .._service._knowledge_base import KnowledgeBaseService


class _DeleteKnowledgeBaseFilesParams(ParamsBase):
    """The params of the delete-files knowledge-base tool."""

    file_paths: list[str] = Field(
        description=(
            "One or more absolute local file paths whose knowledge-base "
            "records should be deleted. This does not delete files from disk."
        ),
        min_length=1,
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


class DeleteKnowledgeBaseFiles(ToolBase):
    """Delete knowledge-base file records without touching local files."""

    name = "DeleteKnowledgeBaseFiles"
    description = (
        "Delete one or more file records from the current user's knowledge "
        "base without deleting the underlying local files. Use this when you "
        "want the files removed from KB search results but kept on disk."
    )
    input_schema: dict[str, Any] = _DeleteKnowledgeBaseFilesParams.model_json_schema()
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
            message="Deleting knowledge-base file records is allowed.",
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
    ):
        payload = await self._knowledge_base_service.delete_files(
            user_id=self._user_id,
            file_paths=file_paths,
        )
        return self._result(payload)
