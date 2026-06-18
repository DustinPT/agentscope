# -*- coding: utf-8 -*-
"""Reply middleware that reports child-session results back to the parent."""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, AsyncGenerator, Callable

from .._reply_state import get_current_reply_msg, is_reply_awaiting_tool_interaction
from ..message_bus import MessageBus
from ..storage import StorageBase
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
        async for item in next_handler(**input_kwargs):
            yield item

        child_session = await self._storage.get_session(
            self._user_id,
            self._agent_id,
            self._session_id,
        )
        if child_session is None or child_session.parent_session_id is None:
            return

        final_msg = get_current_reply_msg(agent)

        if final_msg is None:
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
        prefix = (
            f'<subagent-message agent_id="{self._agent_id}" '
            f'agent_name="{child_agent.data.name}" '
            f'session_id="{self._session_id}" '
            f'session_name="{child_session.config.name}">\n'
        )
        suffix = "\n</subagent-message>"

        if content_blocks:
            if isinstance(content_blocks[0], TextBlock):
                content_blocks[0].text = prefix + content_blocks[0].text
            else:
                content_blocks.insert(0, TextBlock(text=prefix))

            if isinstance(content_blocks[-1], TextBlock):
                content_blocks[-1].text += suffix
            else:
                content_blocks.append(TextBlock(text=suffix))

            hint_content: str | list[TextBlock | DataBlock] = content_blocks
        else:
            content = reply_content if isinstance(reply_content, str) else ""
            hint_content = f"{prefix}{content}{suffix}"

        hint = HintBlock(
            hint=hint_content,
            source=json.dumps(
                {
                    "label": "subagent_message",
                    "sublabel": child_agent.data.name,
                    "session_id": self._session_id,
                    "session_name": child_session.config.name,
                },
                ensure_ascii=False,
            ),
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
