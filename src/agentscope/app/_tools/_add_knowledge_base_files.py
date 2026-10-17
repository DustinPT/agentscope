# -*- coding: utf-8 -*-
"""Builtin tool for adding files into the current user's knowledge base."""

from __future__ import annotations

import json
import os
from typing import Any

from pydantic import Field, field_validator

from ...message import TextBlock, ToolResultState
from ...permission import PermissionBehavior, PermissionContext, PermissionDecision
from ...tool import ParamsBase, ToolBase, ToolChunk
from ...workspace import WorkspaceBase
from .._service._knowledge_base import KnowledgeBaseService


class _AddKnowledgeBaseFilesParams(ParamsBase):
    """The params of the add-files knowledge-base tool."""

    file_paths: list[str] = Field(
        description=(
            "One or more absolute local file paths to add into the current "
            "user's knowledge base. Each file must already be inside a "
            "dedicated project directory under the workspace `projects/` "
            "directory."
        ),
        min_length=1,
    )
    wait_for_index: bool = Field(
        default=False,
        description=(
            "Whether to wait until newly created files finish indexing before "
            "returning."
        ),
    )

    @field_validator("file_paths")
    @classmethod
    def validate_file_paths(cls, value: list[str]) -> list[str]:
        """Require at least one absolute file path."""
        normalized: list[str] = []
        for item in value:
            path = item.strip()
            if not path:
                raise ValueError("File paths must not be empty.")
            if not os.path.isabs(path):
                raise ValueError("Each file path must be an absolute local path.")
            normalized.append(path)
        return normalized


class AddKnowledgeBaseFiles(ToolBase):
    """Add valuable local files into the current user's knowledge base."""

    name = "AddKnowledgeBaseFiles"
    description = (
        "Add one or more valuable local files into the current user's "
        "knowledge base so they can be searched later. Use this only for "
        "documents worth reusing across turns or when the user explicitly "
        "asks to add files. Files must already be inside a dedicated project "
        "directory under the workspace `projects/` directory. Optionally wait "
        "until newly added files finish indexing."
    )
    input_schema: dict[str, Any] = _AddKnowledgeBaseFilesParams.model_json_schema()
    is_concurrency_safe: bool = False
    is_read_only = False
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(
        self,
        *,
        workspace: WorkspaceBase,
        user_id: str,
        knowledge_base_service: KnowledgeBaseService,
    ) -> None:
        super().__init__()
        self._workspace = workspace
        self._user_id = user_id
        self._knowledge_base_service = knowledge_base_service

    def _validate_project_file_paths(self, file_paths: list[str]) -> list[str]:
        """Require files to live under a dedicated workspace project directory."""
        projects_root = os.path.realpath(
            os.path.join(self._workspace.workdir, "projects"),
        )
        normalized: list[str] = []
        for file_path in file_paths:
            resolved_path = os.path.realpath(file_path)
            try:
                common_root = os.path.commonpath([projects_root, resolved_path])
            except ValueError as exc:
                raise ValueError(
                    "Each file must be located under the workspace projects directory.",
                ) from exc
            if common_root != projects_root:
                raise ValueError(
                    "Each file must be located under the workspace `projects/` "
                    "directory. If the file is elsewhere, copy it into a "
                    "project directory first.",
                )
            relative_path = os.path.relpath(resolved_path, projects_root)
            if (
                relative_path == "."
                or relative_path.startswith("..")
                or len(relative_path.split(os.sep)) < 2
            ):
                raise ValueError(
                    "Each file must be inside a dedicated project subdirectory "
                    "under the workspace `projects/` directory.",
                )
            normalized.append(resolved_path)
        return normalized

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        del tool_input, context
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="Adding files to the current user's knowledge base is allowed.",
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
        normalized_paths = self._validate_project_file_paths(file_paths)
        payload = await self._knowledge_base_service.add_files(
            user_id=self._user_id,
            file_paths=normalized_paths,
            wait_for_index=wait_for_index,
        )
        return self._result(payload)
