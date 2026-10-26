# -*- coding: utf-8 -*-
"""Middleware that injects skill-library tools and usage guidance."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...event import ReplyStartEvent
from ...message import AssistantMsg, HintBlock
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
    "At the start of each new user task, treat skill-library checking as an "
    "early decision point.\n\n"
    "If you do not already know that an installed skill fits, or if the "
    "work is unfamiliar, multi-step, domain-specific, repetitive, or "
    "tool-heavy, proactively call `SearchSkills` before you do substantial "
    "reasoning or tool usage instead of waiting until you are blocked. Use "
    "a short English ASCII query that describes the capability or task you "
    "need. If the search returns a promising match, use `DownloadSkill` to "
    "load that skill before continuing.\n\n"
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

    @staticmethod
    def _build_turn_reminder_block() -> HintBlock:
        """Build the persisted reminder block for the current turn."""
        return HintBlock(
            hint=(
                "<system-reminder>"
                "Before handling the current user task, first decide "
                "whether you should search the skill library. If you do not "
                "already know that an installed skill fits, or if the task "
                "is unfamiliar, multi-step, domain-specific, repetitive, or "
                "tool-heavy, call `SearchSkills` early before substantial "
                "reasoning or other tool usage. Use a short English ASCII "
                "query that describes the capability you need. If the "
                "search returns a promising match, call `DownloadSkill` "
                "before continuing. If you already know an installed skill "
                "fits, or the task is straightforward and does not need a "
                "library skill, do not call `SearchSkills`."
                "</system-reminder>"
            ),
            source="system",
        )

    @staticmethod
    def _is_skill_library_reminder(block: HintBlock) -> bool:
        """Return whether one hint block is the injected skill reminder."""
        return (
            block.source == "system"
            and "<system-reminder>" in block.hint
            and "`SearchSkills`" in block.hint
            and "`DownloadSkill`" in block.hint
        )

    @staticmethod
    def _has_subagent_task_hint(agent) -> bool:
        """Return whether the current reply already contains a subagent task."""
        for msg in reversed(agent.state.context):
            if (
                msg.role == "assistant"
                and msg.name == agent.name
                and msg.id == agent.state.reply_id
            ):
                return any(
                    isinstance(block, HintBlock)
                    and "<subagent-task " in block.hint
                    for block in msg.content
                )
        return False

    @classmethod
    def _current_reply_has_reminder(cls, agent) -> bool:
        """Return whether the current reply already has the skill reminder."""
        for msg in reversed(agent.state.context):
            if (
                msg.role == "assistant"
                and msg.name == agent.name
                and msg.id == agent.state.reply_id
            ):
                return any(
                    isinstance(block, HintBlock)
                    and cls._is_skill_library_reminder(block)
                    for block in msg.content
                )
        return False

    @staticmethod
    def _append_reminder_to_current_reply(
        agent,
        reminder_block: HintBlock,
    ) -> None:
        """Append one reminder block to the current reply message."""
        if len(agent.state.context) > 0:
            last_msg = agent.state.context[-1]
            if (
                last_msg.role == "assistant"
                and last_msg.name == agent.name
                and last_msg.id == agent.state.reply_id
            ):
                last_msg.content.append(reminder_block)
                return

        agent.state.context.append(
            AssistantMsg(
                id=agent.state.reply_id,
                name=agent.name,
                content=[reminder_block],
            ),
        )

    @staticmethod
    def _needs_current_turn_reminder(inputs) -> bool:
        """Return whether the current turn should receive a skill reminder."""
        if not isinstance(inputs, (list, tuple)):
            inputs = [inputs]

        return any(
            getattr(msg, "role", None) == "user"
            for msg in inputs
        )

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

    async def on_reply(
        self,
        agent,
        input_kwargs: dict,
        next_handler,
    ):
        """Inject a persisted skill-library reminder into the current reply."""
        should_inject_reminder = self._needs_current_turn_reminder(
            input_kwargs["inputs"],
        )
        reminder_injected = False

        async for item in next_handler(**input_kwargs):
            if (
                should_inject_reminder
                and not reminder_injected
                and isinstance(item, ReplyStartEvent)
            ):
                reminder_injected = True
                self._append_reminder_to_current_reply(
                    agent,
                    self._build_turn_reminder_block(),
                )
            yield item

    async def on_reasoning(
        self,
        agent,
        input_kwargs: dict,
        next_handler,
    ):
        """Inject the same reminder when a subagent task arrives via inbox."""
        if (
            self._has_subagent_task_hint(agent)
            and not self._current_reply_has_reminder(agent)
        ):
            self._append_reminder_to_current_reply(
                agent,
                self._build_turn_reminder_block(),
            )

        async for item in next_handler(**input_kwargs):
            yield item

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
