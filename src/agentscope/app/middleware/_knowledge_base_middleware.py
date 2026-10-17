# -*- coding: utf-8 -*-
"""Middleware that injects KB tools and usage guidance."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...middleware import MiddlewareBase
from ...tool import ToolBase
from .._tools import (
    AddKnowledgeBaseFiles,
    DeleteKnowledgeBaseFiles,
    GetKnowledgeBaseFileChunks,
    RefreshKnowledgeBaseFiles,
    SearchKnowledgeBase,
)

if TYPE_CHECKING:
    from ...agent import Agent


_KNOWLEDGE_BASE_SYSTEM_PROMPT_SUFFIX = (
    "## Knowledge base\n\n"
    "You are equipped with a user-scoped knowledge base. Always treat it as "
    "belonging only to the current user.\n\n"
    "Supported file extensions are: `pdf`, `ppt`, `pptx`, `doc`, `docx`, "
    "`xls`, `xlsx`, `md`, `markdown`, and `txt`.\n\n"
    "Use `SearchKnowledgeBase` when the task may depend on existing "
    "document content, when you need to discover whether relevant files "
    "already exist, or when you need evidence from previously added files. "
    "The search result returns a chunk index and total chunk count for each "
    "hit. If one hit looks useful but you need nearby context, call "
    "`GetKnowledgeBaseFileChunks` with the hit's `file_id` and "
    "`chunk_index` instead of repeatedly broadening the search query."
)

_KNOWLEDGE_BASE_MANAGEMENT_PROMPT_SUFFIX = (
    "\n\n"
    "Only add documents that are clearly valuable for future reuse or that "
    "the user explicitly asks to add. Files added to the knowledge base must "
    "already be located inside a dedicated project directory under the "
    "workspace `projects/` directory. If a file is elsewhere, copy it into "
    "the project directory first. If the project directory does not exist "
    "yet, create the project directory first. "
    "The knowledge-base service automatically polls for file changes and "
    "removes records for files that no longer exist, so you usually do not "
    "need to call manual delete or refresh tools. Use the refresh tool only "
    "when you want file changes to take effect immediately. Use the delete "
    "tool only when you want to remove a file from the knowledge base while "
    "keeping the file on the local filesystem."
)


class KnowledgeBaseMiddleware(MiddlewareBase):  # pylint: disable=abstract-method
    """Expose KB tools and prompt guidance for using them well."""

    def __init__(
        self,
        *,
        workspace,
        user_id: str,
        knowledge_base_service,
        enable_management_tools: bool = False,
    ) -> None:
        self._workspace = workspace
        self._user_id = user_id
        self._knowledge_base_service = knowledge_base_service
        self._enable_management_tools = enable_management_tools

    async def on_system_prompt(  # type: ignore[override]
        self,
        agent: "Agent",
        current_prompt: str,
    ) -> str:
        """Append knowledge-base usage guidance to the system prompt."""
        del agent
        next_prompt = current_prompt
        if _KNOWLEDGE_BASE_SYSTEM_PROMPT_SUFFIX not in next_prompt:
            next_prompt = (
                f"{next_prompt}\n\n{_KNOWLEDGE_BASE_SYSTEM_PROMPT_SUFFIX}"
            )
        if (
            self._enable_management_tools
            and _KNOWLEDGE_BASE_MANAGEMENT_PROMPT_SUFFIX not in next_prompt
        ):
            next_prompt = (
                f"{next_prompt}{_KNOWLEDGE_BASE_MANAGEMENT_PROMPT_SUFFIX}"
            )
        return next_prompt

    async def list_tools(self) -> list[ToolBase]:
        """Return the KB tools attached for this agent run."""
        tools: list[ToolBase] = [
            SearchKnowledgeBase(
                user_id=self._user_id,
                knowledge_base_service=self._knowledge_base_service,
            ),
            GetKnowledgeBaseFileChunks(
                user_id=self._user_id,
                knowledge_base_service=self._knowledge_base_service,
            ),
        ]
        if self._enable_management_tools:
            tools.extend(
                [
                    AddKnowledgeBaseFiles(
                        workspace=self._workspace,
                        user_id=self._user_id,
                        knowledge_base_service=self._knowledge_base_service,
                    ),
                    DeleteKnowledgeBaseFiles(
                        user_id=self._user_id,
                        knowledge_base_service=self._knowledge_base_service,
                    ),
                    RefreshKnowledgeBaseFiles(
                        user_id=self._user_id,
                        knowledge_base_service=self._knowledge_base_service,
                    ),
                ],
            )
        return tools
