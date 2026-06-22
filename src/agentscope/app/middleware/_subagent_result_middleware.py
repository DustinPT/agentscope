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
            content_blocks: list[TextBlock | DataBlock] = []
            if isinstance(reply_content, list):
                trailing_blocks: list[TextBlock | DataBlock] = []
                for block in reversed(reply_content):
                    if isinstance(block, (TextBlock, DataBlock)):
                        trailing_blocks.append(deepcopy(block))
                    else:
                        break
                content_blocks = list(reversed(trailing_blocks))

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
                hint_content = (
                    f"{notification_prefix}{content}{notification_suffix}"
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
