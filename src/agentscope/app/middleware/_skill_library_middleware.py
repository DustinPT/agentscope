# -*- coding: utf-8 -*-
"""Middleware that injects skill-library tools and usage guidance."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...middleware import MiddlewareBase
from ...tool import ToolBase

from .._tools import DownloadSkill, SearchSkills

if TYPE_CHECKING:
    from ...agent import Agent


_SKILL_LIBRARY_SYSTEM_PROMPT_SUFFIX = (
    "## Skill library\n\n"
    "You have access to a skill library for this task.\n\n"
    "When a skill may be useful for the current task, you may use "
    "`SearchSkills` to discover relevant skills, then use "
    "`DownloadSkill` to download the chosen skill for use."
)


class SkillLibraryMiddleware(MiddlewareBase):  # pylint: disable=abstract-method
    """Expose skill-library tools and prompt guidance for using them."""

    def __init__(
        self,
        *,
        workspace,
        user_id: str,
        skill_library_service,
    ) -> None:
        """Store the runtime dependencies needed by skill-library tools."""
        self._workspace = workspace
        self._user_id = user_id
        self._skill_library_service = skill_library_service

    async def on_system_prompt(  # type: ignore[override]
        self,
        agent: "Agent",
        current_prompt: str,
    ) -> str:
        """Append skill-library tool guidance to the system prompt."""
        del agent
        if _SKILL_LIBRARY_SYSTEM_PROMPT_SUFFIX in current_prompt:
            return current_prompt
        return f"{current_prompt}\n\n{_SKILL_LIBRARY_SYSTEM_PROMPT_SUFFIX}"

    async def list_tools(self) -> list[ToolBase]:
        """Return the skill-library tools attached for this agent run."""
        return [
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
