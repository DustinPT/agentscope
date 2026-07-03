"""Regression tests for gap-free session SSE streaming."""
import json
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

from agentscope.app._router._session import (
    _iter_session_sse_frames,
    stream_session_events,
)
from agentscope.app.message_bus import MessageBus
from agentscope.message import AssistantMsg


def _decode_sse_event(frame: str) -> dict:
    """Parse a ``data: ...`` SSE frame into a dict payload."""
    prefix = "data: "
    if not frame.startswith(prefix):
        raise AssertionError(f"Unexpected SSE frame: {frame!r}")
    payload = json.loads(frame[len(prefix) :].strip())
    payload.pop("_entry_id", None)
    return payload


class _BaseFakeStreamBus:
    """Minimal bus stub for exercising ``_iter_session_sse_frames``."""

    _SESSION_EVENTS_KEY = MessageBus._SESSION_EVENTS_KEY

    def __init__(self) -> None:
        self._replay_entries = [("1-0", {"phase": "replay"})]
        self.last_since: str | None = None

    async def session_read_events(
        self,
        session_id: str,  # pylint: disable=unused-argument
        since: str | None = None,  # pylint: disable=unused-argument
        max_count: int = 1000,  # pylint: disable=unused-argument
    ) -> list[tuple[str, dict]]:
        """Return the current replay-log snapshot."""
        self.last_since = since
        if since is None:
            return list(self._replay_entries)
        return [
            (entry_id, payload)
            for entry_id, payload in self._replay_entries
            if entry_id > since
        ]


class _LateReplayBus(_BaseFakeStreamBus):
    """Publishes one event into replay during subscription handoff."""

    async def subscribe(
        self,
        key: str,  # pylint: disable=unused-argument
        *,
        on_ready=None,
    ) -> AsyncGenerator[dict, None]:
        if on_ready is not None:
            on_ready()
        self._replay_entries.append(("2-0", {"phase": "late"}))
        yield {"phase": "live", "_entry_id": "3-0"}


class _DedupingBus(_BaseFakeStreamBus):
    """Emits a duplicate live copy of a replayed event plus a new one."""

    async def subscribe(
        self,
        key: str,  # pylint: disable=unused-argument
        *,
        on_ready=None,
    ) -> AsyncGenerator[dict, None]:
        if on_ready is not None:
            on_ready()
        self._replay_entries.append(("2-0", {"phase": "late"}))
        yield {"phase": "late", "_entry_id": "2-0"}
        yield {"phase": "live", "_entry_id": "3-0"}


class TestSessionStreamFrames(IsolatedAsyncioTestCase):
    """SSE frame generation stays gap-free across replay/live handoff."""

    async def test_replay_includes_events_published_during_handoff(self) -> None:
        """Events published after subscribe-ready are replayed on refresh."""
        stream = _iter_session_sse_frames(_LateReplayBus(), "session-1")

        first = await anext(stream)
        second = await anext(stream)
        third = await anext(stream)
        await stream.aclose()

        self.assertEqual(
            [
                _decode_sse_event(first),
                _decode_sse_event(second),
                _decode_sse_event(third),
            ],
            [
                {"phase": "replay"},
                {"phase": "late"},
                {"phase": "live"},
            ],
        )

    async def test_live_duplicates_of_replayed_entries_are_suppressed(self) -> None:
        """Replay/live overlap does not deliver the same event twice."""
        stream = _iter_session_sse_frames(_DedupingBus(), "session-2")

        first = await anext(stream)
        second = await anext(stream)
        third = await anext(stream)
        with self.assertRaises(StopAsyncIteration):
            await anext(stream)

        self.assertEqual(
            [
                _decode_sse_event(first),
                _decode_sse_event(second),
                _decode_sse_event(third),
            ],
            [
                {"phase": "replay"},
                {"phase": "late"},
                {"phase": "live"},
            ],
        )

    async def test_replay_starts_after_checkpoint_boundary(self) -> None:
        """Refresh replay skips events already folded into history."""
        bus = _LateReplayBus()
        stream = _iter_session_sse_frames(
            bus,
            "session-3",
            since="1-0",
        )

        first = await anext(stream)
        second = await anext(stream)
        await stream.aclose()

        self.assertEqual(bus.last_since, "1-0")
        self.assertEqual(
            [
                _decode_sse_event(first),
                _decode_sse_event(second),
            ],
            [
                {"phase": "late"},
                {"phase": "live"},
            ],
        )

    async def test_stream_endpoint_prefers_client_history_boundary(self) -> None:
        """Client history boundary overrides storage's newer checkpoint."""

        class _FakeStorage:
            async def get_session(
                self,
                user_id: str,
                agent_id: str,
                session_id: str,
            ):
                _ = (user_id, agent_id, session_id)
                return SimpleNamespace(
                    state=SimpleNamespace(reply_id="reply-1"),
                )

            async def get_message(
                self,
                user_id: str,
                session_id: str,
                message_id: str,
            ):
                _ = (user_id, session_id, message_id)
                return AssistantMsg(
                    id="reply-1",
                    name="agent",
                    content=[],
                    metadata={"checkpoint_replay_entry_id": "2-0"},
                )

        bus = _LateReplayBus()
        response = await stream_session_events(
            session_id="session-4",
            agent_id="agent-1",
            replay_after="1-0",
            user_id="user-1",
            storage=_FakeStorage(),
            message_bus=bus,
        )
        stream = response.body_iterator

        first = await anext(stream)
        second = await anext(stream)
        await stream.aclose()

        self.assertEqual(bus.last_since, "1-0")
        self.assertEqual(
            [
                _decode_sse_event(first),
                _decode_sse_event(second),
            ],
            [
                {"phase": "late"},
                {"phase": "live"},
            ],
        )
