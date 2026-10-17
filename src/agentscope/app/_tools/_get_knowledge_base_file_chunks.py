# -*- coding: utf-8 -*-
"""Builtin tool for reading nearby chunks from one KB file."""

from __future__ import annotations

import json
from typing import Any

from pydantic import Field

from ...message import TextBlock, ToolResultState
from ...permission import PermissionBehavior, PermissionContext, PermissionDecision
from ...tool import ParamsBase, ToolBase, ToolChunk
from .._service._knowledge_base import KnowledgeBaseService


class _GetKnowledgeBaseFileChunksParams(ParamsBase):
    """The params of the get-file-chunks knowledge-base tool."""

    file_id: int = Field(
        description="The file ID returned by SearchKnowledgeBase.",
        ge=1,
    )
    anchor_chunk_index: int = Field(
        description="The hit chunk index returned by SearchKnowledgeBase.",
        ge=0,
    )
    before: int = Field(
        default=1,
        ge=0,
        le=5,
        description="How many chunks before the anchor chunk to return.",
    )
    after: int = Field(
        default=1,
        ge=0,
        le=5,
        description="How many chunks after the anchor chunk to return.",
    )


class GetKnowledgeBaseFileChunks(ToolBase):
    """Return one KB file chunk plus nearby context chunks."""

    name = "GetKnowledgeBaseFileChunks"
    description = (
        "Read the target chunk and nearby chunks from one file in the current "
        "user's knowledge base. Use this after SearchKnowledgeBase when a hit "
        "looks relevant and you need more local context around that chunk."
    )
    input_schema: dict[str, Any] = _GetKnowledgeBaseFileChunksParams.model_json_schema()
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
            message="Reading chunks from the current user's knowledge base is allowed.",
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
        file_id: int,
        anchor_chunk_index: int,
        before: int = 1,
        after: int = 1,
    ):
        payload = await self._knowledge_base_service.get_file_chunks(
            user_id=self._user_id,
            file_id=file_id,
            anchor_chunk_index=anchor_chunk_index,
            before=before,
            after=after,
        )
        return self._result(payload)
