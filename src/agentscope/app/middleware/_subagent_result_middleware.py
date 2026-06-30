# -*- coding: utf-8 -*-
"""Reply middleware that reports child-session results back to the parent."""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, AsyncGenerator, Callable

from .._reply_state import get_current_reply_msg, is_reply_awaiting_tool_interaction
from ..message_bus import MessageBus
from ..storage import StorageBase
from ...event import ExceedMaxItersEvent
from ...message import DataBlock, HintBlock, TextBlock
from ...middleware import MiddlewareBase


_FINAL_RESULT_MARKER = "[[FINAL_RESULT]]"


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
            return []
        extracted_blocks.append(deepcopy(block))

    if not collecting or not _has_result_content(extracted_blocks):
        return []
    return extracted_blocks


def _extract_trailing_result_blocks(
    reply_content: str | list[TextBlock | DataBlock],
) -> list[TextBlock | DataBlock]:
    """Extract trailing text and data blocks as a compatibility fallback."""
    if not isinstance(reply_content, list):
        return []

    trailing_blocks: list[TextBlock | DataBlock] = []
    for block in reversed(reply_content):
        if isinstance(block, (TextBlock, DataBlock)):
            trailing_blocks.append(deepcopy(block))
        else:
            break
    return list(reversed(trailing_blocks))


def _build_empty_result_text(child_name: str, session_id: str) -> str:
    """Build guidance text for completed child sessions without result content."""
    return (
        f"Sub-agent '{child_name}' (session_id={session_id}) has completed, "
        "but did not return any result.\n\n"
        "Please determine whether this is expected for the current task. If it "
        "is not expected, resume this existing child session and provide "
        "further instructions."
    )


class SubAgentResultMiddleware(MiddlewareBase):  # pylint: disable=abstract-method
    """Push the final child-session reply into the parent session's inbox."""

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

        child_session = await self._storage.get_session(
            self._user_id,
            self._agent_id,
            self._session_id,
        )
        if child_session is None or child_session.parent_session_id is None:
            return

        final_msg = get_current_reply_msg(agent)

        if final_msg is None and not exceeded_max_iters:
            return

        if is_reply_awaiting_tool_interaction(agent):
            return

        parent_session = await self._storage.get_session(
            self._user_id,
            "",
            child_session.parent_session_id,
        )
        if parent_session is None:
            return

        child_agent = await self._storage.get_agent(self._user_id, self._agent_id)
        if child_agent is None:
            return

        prefix = (
            f'<subagent-message agent_id="{self._agent_id}" '
            f'agent_name="{child_agent.data.name}" '
            f'session_id="{self._session_id}" '
            f'session_name="{child_session.config.name}">\n'
        )
        suffix = "\n</subagent-message>"
        hint_source = {
            "label": "subagent_error" if exceeded_max_iters else "subagent_message",
            "sublabel": child_agent.data.name,
            "session_id": self._session_id,
            "session_name": child_session.config.name,
            "state": "error" if exceeded_max_iters else "success",
        }

        if exceeded_max_iters:
            hint_content = (
                f"{prefix}"
                f"Sub-agent '{child_agent.data.name}' "
                f"(session_id={self._session_id}) failed.\n\n"
                "Error:\n\n"
                "Executed maximum iterations of reasoning-acting loop without "
                "finishing the task. Treat this as an error result. The main "
                "agent can resume this existing child session to continue "
                "execution."
                f"{suffix}"
            )
        else:
            reply_content = final_msg.content
            content_blocks = _extract_marked_result_blocks(reply_content)
            if not content_blocks:
                content_blocks = _extract_trailing_result_blocks(reply_content)
            if content_blocks and not _has_result_content(content_blocks):
                content_blocks = []

            notification_prefix = (
                f"{prefix}"
                f"Sub-agent '{child_agent.data.name}' "
                f"(session_id={self._session_id}) has completed.\n\n"
                "Result:\n\n"
            )
            notification_suffix = f"{suffix}"

            if content_blocks:
                if isinstance(content_blocks[0], TextBlock):
                    content_blocks[0].text = (
                        notification_prefix + content_blocks[0].text
                    )
                else:
                    content_blocks.insert(
                        0,
                        TextBlock(text=notification_prefix),
                    )

                if isinstance(content_blocks[-1], TextBlock):
                    content_blocks[-1].text += notification_suffix
                else:
                    content_blocks.append(TextBlock(text=notification_suffix))

                hint_content: str | list[TextBlock | DataBlock] = content_blocks
            else:
                content = reply_content if isinstance(reply_content, str) else ""
                if content.strip():
                    hint_content = (
                        f"{notification_prefix}{content}{notification_suffix}"
                    )
                else:
                    hint_content = (
                        f"{prefix}"
                        f"{_build_empty_result_text(child_agent.data.name, self._session_id)}"
                        f"{suffix}"
                    )

        hint = HintBlock(
            hint=hint_content,
            source=json.dumps(hint_source, ensure_ascii=False),
        )
        await self._bus.inbox_push(
            child_session.parent_session_id,
            hint.model_dump(mode="json"),
        )
        await self._bus.enqueue_wakeup(
            user_id=self._user_id,
            session_id=child_session.parent_session_id,
            agent_id=parent_session.agent_id,
        )
