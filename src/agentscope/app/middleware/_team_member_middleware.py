# -*- coding: utf-8 -*-
"""Team worker middleware to avoid silent task termination."""

from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Callable

from .._bus_ops import deliver_to_inbox
from ..message_bus import MessageBus
from ..storage import StorageBase
from ..._utils._common import _json_loads_with_repair
from ...event import ReplyEndEvent
from ...message import HintBlock, ToolCallBlock, ToolResultBlock, ToolResultState
from ...middleware import MiddlewareBase
from ...types import ReplyFinishedReason

_TEAM_MEMBER_SYSTEM_PROMPT_SUFFIX = """
How to finish work as a team member:
- You report task progress and final results to the team leader with the `TeamSay` tool.
- Before ending a task, call `TeamSay` to report your final result to the leader.
- If you need clarification or want permission to continue, call `TeamSay` to ask the leader.
- If you hit the maximum number of reasoning-acting iterations, use `TeamSay` to tell the leader what you finished so far and ask whether to continue.
- Do not silently end a task without notifying the leader through `TeamSay`.
""".strip()


class TeamMemberMiddleware(MiddlewareBase):  # pylint: disable=abstract-method
    """Ensure team leaders are notified when a worker stops silently."""

    def __init__(
        self,
        *,
        storage: StorageBase,
        message_bus: MessageBus,
        user_id: str,
        session_id: str,
        agent_id: str,
        leader_session_id: str,
        leader_agent_id: str,
        leader_name: str,
        member_name: str,
    ) -> None:
        self._storage = storage
        self._bus = message_bus
        self._user_id = user_id
        self._session_id = session_id
        self._agent_id = agent_id
        self._leader_session_id = leader_session_id
        self._leader_agent_id = leader_agent_id
        self._leader_name = leader_name
        self._member_name = member_name

    async def on_system_prompt(  # type: ignore[override]
        self,
        agent: Any,
        current_prompt: str,
    ) -> str:
        """Append the worker reporting contract once."""
        if _TEAM_MEMBER_SYSTEM_PROMPT_SUFFIX in current_prompt:
            return current_prompt
        return f"{current_prompt}\n\n{_TEAM_MEMBER_SYSTEM_PROMPT_SUFFIX}"

    def _last_tool_call_reports_to_leader(self, agent: Any) -> bool:
        """Return whether the active reply ended with a successful TeamSay."""
        for msg in reversed(agent.state.context):
            if (
                msg.id != agent.state.reply_id
                or msg.role != "assistant"
                or msg.name != agent.name
            ):
                continue

            blocks = msg.get_content_blocks()
            last_tool_call = next(
                (
                    block
                    for block in reversed(blocks)
                    if isinstance(block, ToolCallBlock)
                ),
                None,
            )
            if last_tool_call is None or last_tool_call.name != "TeamSay":
                return False
            try:
                kwargs = _json_loads_with_repair(last_tool_call.input)
            except Exception:  # noqa: BLE001
                return False
            if kwargs.get("to") not in (None, self._leader_name):
                return False

            result = next(
                (
                    block
                    for block in blocks
                    if isinstance(block, ToolResultBlock)
                    and block.id == last_tool_call.id
                ),
                None,
            )
            return (
                result is not None and result.state == ToolResultState.SUCCESS
            )

        return False

    async def _notify_leader(self, hint: str) -> None:
        """Deliver a wake-up hint to the leader inbox."""
        block = HintBlock(
            hint=f"<system-reminder>{hint}</system-reminder>",
            source=json.dumps(
                {
                    "label": "team_message",
                    "sublabel": self._member_name,
                    "session_id": self._session_id,
                    "state": "needs_attention",
                },
                ensure_ascii=False,
            ),
        )
        await deliver_to_inbox(
            self._bus,
            user_id=self._user_id,
            session_id=self._leader_session_id,
            agent_id=self._leader_agent_id,
            payload=block.model_dump(mode="json"),
        )

    def _build_leader_hint(
        self,
        finished_reason: ReplyFinishedReason,
    ) -> str | None:
        """Build a concise leader reminder for silent worker termination."""
        if finished_reason == ReplyFinishedReason.EXCEED_MAX_ITERS:
            return (
                f"Team member '{self._member_name}' reached the maximum "
                "reasoning-acting iterations and stopped before reporting via "
                f"TeamSay. Ask it to continue only if the task still needs "
                "more work, otherwise inspect its session and decide whether "
                "the partial result is enough."
            )
        if finished_reason == ReplyFinishedReason.COMPLETED:
            return (
                f"Team member '{self._member_name}' ended its turn without "
                f"reporting back via TeamSay. Treat the task as unresolved "
                "until the member reports its result or you inspect the "
                "worker session directly."
            )
        return None

    async def on_reply(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Notify the leader when a worker stops without TeamSay."""
        terminal_reason: ReplyFinishedReason | None = None
        async for item in next_handler(**input_kwargs):
            if isinstance(item, ReplyEndEvent):
                terminal_reason = item.finished_reason
            yield item

        if terminal_reason is None:
            return
        if self._last_tool_call_reports_to_leader(agent):
            return

        hint = self._build_leader_hint(terminal_reason)
        if hint is None:
            return

        try:
            await self._notify_leader(hint)
        except Exception:  # noqa: BLE001
            # Avoid replacing the worker's original terminal outcome.
            pass
