# -*- coding: utf-8 -*-
"""Middleware that injects skill-library tools and usage guidance."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...middleware import MiddlewareBase
from ...tool import ToolBase

from .._tools import (
    DeleteSkillFromLibrary,
    DownloadSkill,
    SearchSkills,
    UploadSkillToLibrary,
)

if TYPE_CHECKING:
    from ...agent import Agent


_SKILL_LIBRARY_SYSTEM_PROMPT_SUFFIX = (
    "## Skill library\n\n"
    "You are equipped with a skill library.\n\n"
    "This skill library is separate from the skills already available to "
    "you. If present, the `<agent-skills>` section lists those available "
    "skills. Skills in the library are not available by default: you must "
    "search for them with `SearchSkills` and load them with "
    "`DownloadSkill` before using them.\n\n"
    "When you start a new task, first consider whether an installed skill "
    "already fits. If not, or if the work is unfamiliar, multi-step, "
    "domain-specific, or tool-heavy, proactively call `SearchSkills` early "
    "instead of waiting until you are blocked. Use a short English ASCII "
    "query that describes the capability or task you need. If the search "
    "returns a promising match, use `DownloadSkill` to load that skill "
    "before continuing.\n\n"
    "When using a library skill, if you find a meaningful, general-purpose, "
    "and valuable improvement, do not change the skill on your own. First "
    "tell the user what can be improved and let the user decide whether to "
    "proceed. Only after the user agrees may you modify the skill and update "
    "the library version. If you are a sub-agent without skill-library "
    "management tools, send the improvement suggestion to the main agent so "
    "the main agent can ask the user."
)

_SKILL_LIBRARY_MANAGEMENT_PROMPT_SUFFIX = (
    "\n\n"
    "You can also manage skills in the library. When you create or update "
    "a skill ZIP inside the current workspace, use `UploadSkillToLibrary` "
    "to add it to the library or replace the existing version. Use "
    "`DeleteSkillFromLibrary` only when you are sure a library skill "
    "should be removed by its exact name."
)


class SkillLibraryMiddleware(MiddlewareBase):  # pylint: disable=abstract-method
    """Expose skill-library tools and prompt guidance for using them."""

    def __init__(
        self,
        *,
        workspace,
        user_id: str,
        skill_library_service,
        enable_management_tools: bool = False,
    ) -> None:
        """Store the runtime dependencies needed by skill-library tools."""
        self._workspace = workspace
        self._user_id = user_id
        self._skill_library_service = skill_library_service
        self._enable_management_tools = enable_management_tools

    async def on_system_prompt(  # type: ignore[override]
        self,
        agent: "Agent",
        current_prompt: str,
    ) -> str:
        """Append skill-library tool guidance to the system prompt."""
        del agent
        next_prompt = current_prompt
        if _SKILL_LIBRARY_SYSTEM_PROMPT_SUFFIX not in next_prompt:
            next_prompt = f"{next_prompt}\n\n{_SKILL_LIBRARY_SYSTEM_PROMPT_SUFFIX}"
        if (
            self._enable_management_tools
            and _SKILL_LIBRARY_MANAGEMENT_PROMPT_SUFFIX not in next_prompt
        ):
            next_prompt = (
                f"{next_prompt}{_SKILL_LIBRARY_MANAGEMENT_PROMPT_SUFFIX}"
            )
        return next_prompt

    async def list_tools(self) -> list[ToolBase]:
        """Return the skill-library tools attached for this agent run."""
        tools: list[ToolBase] = [
            SearchSkills(
                user_id=self._user_id,
                skill_library_service=self._skill_library_service,
            ),
            DownloadSkill(
                workspace=self._workspace,
                user_id=self._user_id,
                skill_library_service=self._skill_library_service,
            ),
        ]
        if self._enable_management_tools:
            tools.extend(
                [
                    UploadSkillToLibrary(
                        workspace=self._workspace,
                        user_id=self._user_id,
                        skill_library_service=self._skill_library_service,
                    ),
                    DeleteSkillFromLibrary(
                        user_id=self._user_id,
                        skill_library_service=self._skill_library_service,
                    ),
                ],
            )
        return tools
