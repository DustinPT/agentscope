# -*- coding: utf-8 -*-
"""Middleware implementing the child-session sub-agent protocol."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from typing import Any, AsyncGenerator, Callable

from .._bus_ops import deliver_to_inbox
from .._reply_state import get_current_reply_msg, is_reply_awaiting_tool_interaction
from ..message_bus import MessageBus
from ..storage import SessionRecord, StorageBase, SubAgentTaskRecord
from ...event import ExceedMaxItersEvent
from ...message import DataBlock, HintBlock, Msg, TextBlock, ToolCallState
from ...middleware import MiddlewareBase


_FINAL_RESULT_MARKER = "[[FINAL_RESULT]]"
_TERMINAL_PARENT_INVOCATION_STATUSES = {
    "final_delivered",
    "protocol_failed",
    "error_delivered",
}
_SUBAGENT_SYSTEM_PROMPT_SUFFIX = """
How to handle delegated sub-agent tasks:
- When you receive a message wrapped in `<subagent-task ...>...</subagent-task>`,
  the content inside that message is the delegated task from the parent agent.
- Complete that delegated task directly.
- Make your final result self-contained. Do not refer to earlier messages with
  phrases like "as explained above" or "see previous analysis" when the
  missing context can be restated directly in the final result.
- The parent agent will receive only the content after [[FINAL_RESULT]], so
  everything needed to understand and use the result must be included there.
- Your final reply MUST start with [[FINAL_RESULT]].
- [[FINAL_RESULT]] is the terminal handoff marker for this task. After you
  output it, do not call tools, do not continue reasoning, and do not send any
  additional assistant message.
- If another instruction asks you to do post-task housekeeping such as version
  checks, telemetry, or optional follow-up actions, do that before
  [[FINAL_RESULT]] when possible.
- If such housekeeping would require acting after [[FINAL_RESULT]], skip it
  instead of sending a second final result.
- If the final result includes attachments or other data blocks, place them
  after the [[FINAL_RESULT]] marker.

Use this format in your final reply:
[[FINAL_RESULT]]
(complete, self-contained result)
""".strip()


def is_terminal_parent_invocation_status(status: str | None) -> bool:
    """Return whether the delegated task status is already terminal."""
    return status in _TERMINAL_PARENT_INVOCATION_STATUSES


def is_msg_awaiting_tool_interaction(msg: Msg | None) -> bool:
    """Return whether the given reply message is waiting for outside input."""
    if msg is None:
        return False
    return any(
        tool_call.state in (ToolCallState.ASKING, ToolCallState.SUBMITTED)
        for tool_call in msg.get_content_blocks("tool_call")
    )


def _reply_contains_final_result_marker(
    reply_content: str | list[TextBlock | DataBlock],
) -> bool:
    """Return whether the reply explicitly contains the final marker."""
    if isinstance(reply_content, str):
        return _FINAL_RESULT_MARKER in reply_content
    return any(
        isinstance(block, TextBlock) and _FINAL_RESULT_MARKER in block.text
        for block in reply_content
    )


def _has_result_content(blocks: list[TextBlock | DataBlock]) -> bool:
    """Return whether extracted result blocks contain usable content."""
    for block in blocks:
        if isinstance(block, DataBlock):
            return True
        if block.text.strip():
            return True
    return False


def _extract_marked_result_blocks(
    reply_content: str | list[TextBlock | DataBlock],
) -> list[TextBlock | DataBlock]:
    """Extract result blocks after the explicit final result marker."""
    if isinstance(reply_content, str):
        marker_index = reply_content.find(_FINAL_RESULT_MARKER)
        if marker_index < 0:
            return []
        result_text = reply_content[
            marker_index + len(_FINAL_RESULT_MARKER) :
        ].lstrip()
        if not result_text.strip():
            return []
        return [TextBlock(text=result_text)]

    extracted_blocks: list[TextBlock | DataBlock] = []
    collecting = False
    for block in reply_content:
        if not collecting:
            if not isinstance(block, TextBlock):
                continue
            marker_index = block.text.find(_FINAL_RESULT_MARKER)
            if marker_index < 0:
                continue
            collecting = True
            remaining_text = block.text[
                marker_index + len(_FINAL_RESULT_MARKER) :
            ].lstrip()
            if remaining_text:
                copied_block = deepcopy(block)
                copied_block.text = remaining_text
                extracted_blocks.append(copied_block)
            continue

        if not isinstance(block, (TextBlock, DataBlock)):
            break
        extracted_blocks.append(deepcopy(block))

    if not collecting or not _has_result_content(extracted_blocks):
        return []
    return extracted_blocks


def _build_empty_result_text(child_name: str, session_id: str) -> str:
    """Build guidance text for completed child sessions without result content."""
    return (
        f"Sub-agent '{child_name}' (session_id={session_id}) has completed, "
        "but did not return any result.\n\n"
        "Please determine whether this is expected for the current task. If it "
        "is not expected, resume this existing child session and provide "
        "further instructions."
    )


def _build_prefix(
    *,
    agent_id: str,
    agent_name: str,
    session_id: str,
    session_name: str,
) -> str:
    """Build the envelope prefix for a parent-visible subagent message."""
    return (
        f'<subagent-message agent_id="{agent_id}" '
        f'agent_name="{agent_name}" '
        f'session_id="{session_id}" '
        f'session_name="{session_name}">\n'
    )


def _build_success_hint_content(
    *,
    agent_id: str,
    agent_name: str,
    session_id: str,
    session_name: str,
    content_blocks: list[TextBlock | DataBlock],
) -> str | list[TextBlock | DataBlock]:
    """Wrap a successful final result for parent-session delivery."""
    prefix = _build_prefix(
        agent_id=agent_id,
        agent_name=agent_name,
        session_id=session_id,
        session_name=session_name,
    )
    notification_prefix = (
        f"{prefix}"
        f"Sub-agent '{agent_name}' (session_id={session_id}) has completed.\n\n"
        "Result:\n\n"
    )
    notification_suffix = "\n</subagent-message>"
    blocks = [deepcopy(block) for block in content_blocks]
    if blocks:
        if isinstance(blocks[0], TextBlock):
            blocks[0].text = notification_prefix + blocks[0].text
        else:
            blocks.insert(0, TextBlock(text=notification_prefix))

        if isinstance(blocks[-1], TextBlock):
            blocks[-1].text += notification_suffix
        else:
            blocks.append(TextBlock(text=notification_suffix))
        return blocks

    return (
        f"{prefix}"
        f"{_build_empty_result_text(agent_name, session_id)}"
        f"\n</subagent-message>"
    )


def _build_error_hint_content(
    *,
    agent_id: str,
    agent_name: str,
    session_id: str,
    session_name: str,
    error_text: str,
) -> str:
    """Wrap an error terminal result for parent-session delivery."""
    prefix = _build_prefix(
        agent_id=agent_id,
        agent_name=agent_name,
        session_id=session_id,
        session_name=session_name,
    )
    return (
        f"{prefix}"
        f"Sub-agent '{agent_name}' (session_id={session_id}) failed.\n\n"
        f"Error:\n\n{error_text}\n"
        "</subagent-message>"
    )


async def load_session_current_reply(
    storage: StorageBase,
    user_id: str,
    session: SessionRecord,
) -> Msg | None:
    """Load the persisted current reply for one session, if available."""
    if not session.state.reply_id:
        return None
    return await storage.get_message(
        user_id=user_id,
        session_id=session.id,
        message_id=session.state.reply_id,
    )


class SubAgentMiddleware(MiddlewareBase):  # pylint: disable=abstract-method
    """Apply the sub-agent protocol on child-session input and output."""

    def __init__(
        self,
        storage: StorageBase,
        message_bus: MessageBus,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> None:
        """Bind dependencies and request-scoped identifiers."""
        self._storage = storage
        self._bus = message_bus
        self._user_id = user_id
        self._agent_id = agent_id
        self._session_id = session_id

    async def on_system_prompt(  # type: ignore[override]
        self,
        agent: Any,
        current_prompt: str,
    ) -> str:
        """Inject child-session protocol into the system prompt."""
        if _SUBAGENT_SYSTEM_PROMPT_SUFFIX in current_prompt:
            return current_prompt
        return f"{current_prompt}\n\n{_SUBAGENT_SYSTEM_PROMPT_SUFFIX}"

    async def _get_active_task(self) -> SubAgentTaskRecord | None:
        """Return the active delegated task bound to the current child session."""
        return await self._storage.get_active_subagent_task_by_child_session(
            self._user_id,
            self._session_id,
        )

    async def _persist_task(
        self,
        task: SubAgentTaskRecord,
    ) -> None:
        """Persist one delegated task record."""
        task.last_progress_at = datetime.now()
        await self._storage.upsert_subagent_task(self._user_id, task)

    async def _persist_terminal_task(
        self,
        task: SubAgentTaskRecord,
        *,
        terminal_status: str,
        terminal_reason: str,
    ) -> None:
        """Persist the task's terminal state and clear the active index."""
        task.status = terminal_status
        task.terminal_reason = terminal_reason
        task.last_progress_at = datetime.now()
        await self._storage.upsert_subagent_task(self._user_id, task)
        await self._storage.unmark_active_subagent_task(
            self._user_id,
            task.child_session_id,
            task.id,
        )

    async def _decrement_parent_task_descendant_count(
        self,
        task: SubAgentTaskRecord,
    ) -> None:
        """Close out one descendant slot on the direct parent task."""
        if not task.parent_task_id:
            return
        parent_task = await self._storage.get_subagent_task(
            self._user_id,
            task.parent_task_id,
        )
        if parent_task is None:
            return
        if parent_task.open_descendant_count <= 0:
            return
        parent_task.open_descendant_count -= 1
        parent_task.last_progress_at = datetime.now()
        await self._storage.upsert_subagent_task(self._user_id, parent_task)

    async def _deliver_terminal_result(
        self,
        *,
        task: SubAgentTaskRecord,
        terminal_status: str,
        terminal_reason: str,
        hint_content: str | list[TextBlock | DataBlock],
        hint_label: str,
        hint_state: str,
    ) -> None:
        """Send one terminal result to the direct parent session exactly once."""
        if is_terminal_parent_invocation_status(task.status):
            return

        parent_session = await self._storage.get_session_meta(
            self._user_id,
            task.parent_session_id,
        )
        if parent_session is None:
            await self._persist_terminal_task(
                task,
                terminal_status=terminal_status,
                terminal_reason=f"{terminal_reason}: parent_session_missing",
            )
            return

        child_session = await self._storage.get_session_meta(
            self._user_id,
            self._session_id,
        )
        if child_session is not None and child_session.agent_id != self._agent_id:
            child_session = None
        child_agent = await self._storage.get_agent(self._user_id, self._agent_id)
        if child_session is None or child_agent is None:
            await self._persist_terminal_task(
                task,
                terminal_status=terminal_status,
                terminal_reason=f"{terminal_reason}: child_session_or_agent_missing",
            )
            return

        await self._decrement_parent_task_descendant_count(task)

        hint = HintBlock(
            hint=hint_content,
            source=json.dumps(
                {
                    "label": hint_label,
                    "sublabel": child_agent.data.name,
                    "session_id": self._session_id,
                    "session_name": child_session.config.name,
                    "state": hint_state,
                    "subagent_task_id": task.id,
                },
                ensure_ascii=False,
            ),
        )
        await deliver_to_inbox(
            self._bus,
            user_id=self._user_id,
            session_id=task.parent_session_id,
            agent_id=parent_session.agent_id,
            payload=hint.model_dump(mode="json"),
        )
        await self._persist_terminal_task(
            task,
            terminal_status=terminal_status,
            terminal_reason=terminal_reason,
        )

    async def on_reply(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Mirror a finished child-session reply back to the parent session."""
        exceeded_max_iters = False
        async for item in next_handler(**input_kwargs):
            if isinstance(item, ExceedMaxItersEvent):
                exceeded_max_iters = True
            yield item

        child_session = await self._storage.get_session_meta(
            self._user_id,
            self._session_id,
        )
        if child_session is not None and child_session.agent_id != self._agent_id:
            child_session = None
        if child_session is None or child_session.parent_session_id is None:
            return

        task = await self._get_active_task()
        if task is None or is_terminal_parent_invocation_status(task.status):
            return

        final_msg = get_current_reply_msg(agent)
        if final_msg is None and not exceeded_max_iters:
            return

        child_agent = await self._storage.get_agent(self._user_id, self._agent_id)
        if child_agent is None:
            return

        if exceeded_max_iters:
            await self._deliver_terminal_result(
                task=task,
                terminal_status="error_delivered",
                terminal_reason="exceeded_max_iters",
                hint_content=_build_error_hint_content(
                    agent_id=self._agent_id,
                    agent_name=child_agent.data.name,
                    session_id=self._session_id,
                    session_name=child_session.config.name,
                    error_text=(
                        "Executed maximum iterations of reasoning-acting loop "
                        "without finishing the task. Treat this as an error "
                        "result. The main agent can resume this existing child "
                        "session to continue execution."
                    ),
                ),
                hint_label="subagent_error",
                hint_state="error",
            )
            return

        reply_content = final_msg.content
        if _reply_contains_final_result_marker(reply_content):
            content_blocks = _extract_marked_result_blocks(reply_content)
            if content_blocks and not _has_result_content(content_blocks):
                content_blocks = []
            if not content_blocks:
                await self._deliver_terminal_result(
                    task=task,
                    terminal_status="protocol_failed",
                    terminal_reason="empty_final_result",
                    hint_content=_build_error_hint_content(
                        agent_id=self._agent_id,
                        agent_name=child_agent.data.name,
                        session_id=self._session_id,
                        session_name=child_session.config.name,
                        error_text=(
                            "The child session returned an empty final reply."
                        ),
                    ),
                    hint_label="subagent_error",
                    hint_state="error",
                )
                return

            await self._deliver_terminal_result(
                task=task,
                terminal_status="final_delivered",
                terminal_reason="final_result",
                hint_content=_build_success_hint_content(
                    agent_id=self._agent_id,
                    agent_name=child_agent.data.name,
                    session_id=self._session_id,
                    session_name=child_session.config.name,
                    content_blocks=content_blocks,
                ),
                hint_label="subagent_message",
                hint_state="success",
            )
            return

        if is_reply_awaiting_tool_interaction(agent):
            task.status = "running"
            await self._persist_task(task)
            return

        if task.open_descendant_count > 0:
            task.status = "waiting_descendants"
            await self._persist_task(task)
            return

        await self._deliver_terminal_result(
            task=task,
            terminal_status="protocol_failed",
            terminal_reason="missing_final_result",
            hint_content=_build_error_hint_content(
                agent_id=self._agent_id,
                agent_name=child_agent.data.name,
                session_id=self._session_id,
                session_name=child_session.config.name,
                error_text=(
                    "The child session did not produce a valid final reply."
                ),
            ),
            hint_label="subagent_error",
            hint_state="error",
        )
