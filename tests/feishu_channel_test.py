# -*- coding: utf-8 -*-
"""Regression tests for Feishu channel streaming recovery behavior."""
import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from agentscope.app.channel._base import ChannelEvent
from agentscope.app.channel._feishu._channel import FeishuChannel
from agentscope.event import (
    ReplyEndEvent,
    ReplyStartEvent,
    TextBlockDeltaEvent,
    TextBlockEndEvent,
    TextBlockStartEvent,
)


def _reply_events(text: str) -> list[object]:
    """Build a minimal text reply stream."""
    return [
        ReplyStartEvent(
            session_id="session-1",
            reply_id="reply-1",
            name="Feishu Agent",
        ),
        TextBlockStartEvent(reply_id="reply-1", block_id="block-1"),
        TextBlockDeltaEvent(
            reply_id="reply-1",
            block_id="block-1",
            delta=text,
        ),
        TextBlockEndEvent(reply_id="reply-1", block_id="block-1"),
        ReplyEndEvent(session_id="session-1", reply_id="reply-1"),
    ]


async def _iter_events(text: str):
    """Yield the reply events as bus payloads."""
    for event in _reply_events(text):
        yield event.model_dump(mode="json")


class FeishuChannelTest(IsolatedAsyncioTestCase):
    """Verify Feishu recovers timed-out cards before degrading to text."""

    def setUp(self) -> None:
        self.channel = FeishuChannel(
            channel_id="channel-1",
            credentials=FeishuChannel.Credentials(
                app_id="app-id",
                app_secret="app-secret",
            ),
            config=FeishuChannel.Config(),
        )
        self.target = ChannelEvent(
            channel_id="channel-1",
            channel_user_id="user-1",
            chat_id="chat-1",
            channel_message_id="msg-1",
        )

    async def test_send_response_recovers_when_stream_push_times_out(self) -> None:
        """A timed-out stream should reopen the same card and continue."""

        async def _open_card(_event: ChannelEvent) -> str:
            self.channel._stream_seq["card-1"] = 0
            return "card-1"

        self.channel._card_open = AsyncMock(side_effect=_open_card)
        self.channel._card_push = AsyncMock(
            side_effect=[
                {"code": 200510, "msg": "card streaming timeout"},
                {"code": 0, "msg": "success"},
                {"code": 0, "msg": "success"},
            ],
        )
        self.channel._set_streaming_mode = AsyncMock(
            return_value={"code": 0, "msg": "success"},
        )
        self.channel._close_stream = AsyncMock()
        self.channel._send = AsyncMock(return_value={"code": 0})

        await self.channel.send_response(self.target, _iter_events("hello"))

        self.assertEqual(self.channel._card_push.await_count, 3)
        self.channel._set_streaming_mode.assert_awaited_once_with("card-1", True)
        self.channel._close_stream.assert_awaited_once_with("card-1")
        self.channel._send.assert_not_awaited()
        self.assertNotIn("card-1", self.channel._stream_seq)

    async def test_send_response_falls_back_when_stream_reopen_fails(self) -> None:
        """A timed-out stream should degrade to text if reopening fails."""

        async def _open_card(_event: ChannelEvent) -> str:
            self.channel._stream_seq["card-1"] = 0
            return "card-1"

        self.channel._card_open = AsyncMock(side_effect=_open_card)
        self.channel._card_push = AsyncMock(
            return_value={"code": 200510, "msg": "card streaming timeout"},
        )
        self.channel._set_streaming_mode = AsyncMock(
            return_value={"code": 300120, "msg": "server internal error"},
        )
        self.channel._close_stream = AsyncMock()
        self.channel._send = AsyncMock(return_value={"code": 0})

        await self.channel.send_response(self.target, _iter_events("hello"))

        self.channel._set_streaming_mode.assert_awaited_once_with("card-1", True)
        self.channel._close_stream.assert_not_awaited()
        self.channel._send.assert_awaited_once_with(
            "msg-1",
            "chat-1",
            "text",
            json.dumps({"text": "hello"}),
        )
        self.assertNotIn("card-1", self.channel._stream_seq)

    async def test_send_response_falls_back_when_final_card_flush_fails(self) -> None:
        """A final timeout should reopen the card, flush text, then close it."""

        async def _open_card(_event: ChannelEvent) -> str:
            self.channel._stream_seq["card-1"] = 0
            return "card-1"

        self.channel._card_open = AsyncMock(side_effect=_open_card)
        self.channel._card_push = AsyncMock(
            side_effect=[
                {"code": 0, "msg": "success"},
                {"code": 200510, "msg": "card streaming timeout"},
                {"code": 0, "msg": "success"},
            ],
        )
        self.channel._set_streaming_mode = AsyncMock(
            return_value={"code": 0, "msg": "success"},
        )
        self.channel._close_stream = AsyncMock()
        self.channel._send = AsyncMock(return_value={"code": 0})

        await self.channel.send_response(self.target, _iter_events("world"))

        self.assertEqual(self.channel._card_push.await_count, 3)
        self.channel._set_streaming_mode.assert_awaited_once_with("card-1", True)
        self.channel._close_stream.assert_awaited_once_with("card-1")
        self.channel._send.assert_not_awaited()
        self.assertNotIn("card-1", self.channel._stream_seq)
    
    async def test_send_response_falls_back_for_non_recoverable_card_error(self) -> None:
        """Non-timeout CardKit failures should still degrade to a text reply."""

        async def _open_card(_event: ChannelEvent) -> str:
            self.channel._stream_seq["card-1"] = 0
            return "card-1"

        self.channel._card_open = AsyncMock(side_effect=_open_card)
        self.channel._card_push = AsyncMock(
            return_value={"code": 300311, "msg": "permission denied"},
        )
        self.channel._set_streaming_mode = AsyncMock()
        self.channel._close_stream = AsyncMock()
        self.channel._send = AsyncMock(return_value={"code": 0})

        await self.channel.send_response(self.target, _iter_events("world"))

        self.channel._set_streaming_mode.assert_not_awaited()
        self.channel._close_stream.assert_not_awaited()
        self.channel._send.assert_awaited_once_with(
            "msg-1",
            "chat-1",
            "text",
            json.dumps({"text": "world"}),
        )
        self.assertNotIn("card-1", self.channel._stream_seq)
