# -*- coding: utf-8 -*-
"""Tests for in-progress reply checkpoint persistence in ``ChatService``."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app._manager import ChatRunRegistry
from agentscope.app._service._chat import ChatService
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    ChatModelConfig,
    SessionConfig,
    SessionRecord,
)
from agentscope.event import (
    ReplyStartEvent,
    ToolCallDeltaEvent,
    ToolCallStartEvent,
    ToolResultStartEvent,
    ToolResultTextDeltaEvent,
)
from agentscope.app._reply_state import (
    REPLY_CHECKPOINT_REPLAY_ENTRY_ID_METADATA_KEY,
)


class _FakeWorkspaceManager:
    """Return a deterministic workspace for every test session."""

    async def get_workspace(
        self,
        _user_id: str,
        _agent_id: str,
        _session_id: str,
        _workspace_id: str,
    ) -> SimpleNamespace:
        return SimpleNamespace(workdir="/tmp/agentscope-checkpoint-tests")


class _FakeMessageBus:
    """Minimal in-memory message bus used by ``ChatService`` tests."""

    def __init__(self) -> None:
        self.published_events: list[dict] = []
        self.enqueued_wakeups: list[dict] = []
        self._next_entry_id = 0

    @asynccontextmanager
    async def session_run(self, _session_id: str):
        yield

    async def session_publish_event(
        self,
        _session_id: str,
        payload: dict,
    ) -> str:
        self.published_events.append(payload)
        self._next_entry_id += 1
        return f"{self._next_entry_id}-0"

    async def inbox_length(self, _session_id: str) -> int:
        return 0

    async def enqueue_wakeup(
        self,
        *,
        user_id: str,
        session_id: str,
        agent_id: str,
    ) -> None:
        self.enqueued_wakeups.append(
            {
                "user_id": user_id,
                "session_id": session_id,
                "agent_id": agent_id,
            },
        )


class _FakeStorage:
    """Capture persisted messages and states for checkpoint assertions."""

    def __init__(self) -> None:
        self.agent_record = AgentRecord(
            user_id="u1",
            data=AgentData(
                name="Checkpoint Agent",
                system_prompt="You are helpful.",
                context_config=ContextConfig(),
                react_config=ReActConfig(),
            ),
        )
        self.session_record = SessionRecord(
            user_id="u1",
            agent_id="a1",
            config=SessionConfig(
                workspace_id="w1",
                chat_model_config=ChatModelConfig(
                    type="mock",
                    credential_id="cred",
                    model="mock-model",
                    parameters={},
                ),
            ),
        )
        self.messages: dict[tuple[str, str], object] = {}
        self.saved_replies: list[object] = []
        self.saved_states: list[object] = []

    async def get_agent(self, _user_id: str, _agent_id: str) -> AgentRecord:
        return self.agent_record

    async def get_session(
        self,
        _user_id: str,
        _agent_id: str,
        _session_id: str,
    ) -> SessionRecord:
        return self.session_record

    async def upsert_message(
        self,
        _user_id: str,
        session_id: str,
        msg: object,
    ) -> None:
        copied = msg.model_copy(deep=True)
        self.messages[(session_id, copied.id)] = copied
        if getattr(copied, "role", None) == "assistant":
            self.saved_replies.append(copied)

    async def get_message(
        self,
        _user_id: str,
        session_id: str,
        message_id: str,
    ) -> object | None:
        msg = self.messages.get((session_id, message_id))
        return None if msg is None else msg.model_copy(deep=True)

    async def update_session_state(
        self,
        *,
        user_id: str,
        agent_id: str,
        session_id: str,
        state: object,
    ) -> None:
        _ = (user_id, agent_id, session_id)
        copied = state.model_copy(deep=True)
        self.session_record.state = copied
        self.saved_states.append(copied)


class _FakeAgent:
    """Agent stand-in that yields preconfigured events."""

    planned_events: list[object] = []

    def __init__(self, **kwargs) -> None:
        self.name = kwargs["name"]
        self.state = kwargs["state"]

    async def reply_stream(self, inputs=None):
        _ = inputs
        for event in self.planned_events:
            if isinstance(event, ReplyStartEvent):
                self.state.reply_id = event.reply_id
            yield event


class ChatServiceCheckpointTest(IsolatedAsyncioTestCase):
    """Verify checkpoint persistence triggers on the intended signals."""

    async def _make_service(self) -> tuple[ChatService, _FakeStorage]:
        storage = _FakeStorage()
        service = ChatService(
            storage=storage,
            workspace_manager=_FakeWorkspaceManager(),
            scheduler_manager=SimpleNamespace(),
            background_task_manager=SimpleNamespace(),
            message_bus=_FakeMessageBus(),
            chat_run_registry=ChatRunRegistry(),
            custom_agent_cls=_FakeAgent,
        )
        return service, storage

    async def test_tool_call_delta_hits_char_threshold(self) -> None:
        """Partial tool-call args count toward the character checkpoint."""
        service, storage = await self._make_service()
        service._REPLY_CHECKPOINT_EVENT_THRESHOLD = 99
        service._REPLY_CHECKPOINT_CHAR_THRESHOLD = 4
        _FakeAgent.planned_events = [
            ReplyStartEvent(reply_id="reply-tool-call", name="Checkpoint Agent"),
            ToolCallStartEvent(
                reply_id="reply-tool-call",
                tool_call_id="tool-call-1",
                tool_call_name="search_docs",
            ),
            ToolCallDeltaEvent(
                reply_id="reply-tool-call",
                tool_call_id="tool-call-1",
                delta='{"q"',
            ),
        ]

        with (
            patch(
                "agentscope.app._service._chat.get_toolkit",
                new=AsyncMock(return_value=object()),
            ),
            patch(
                "agentscope.app._service._chat.get_model",
                new=AsyncMock(return_value=object()),
            ),
        ):
            await service.run("u1", "s1", "a1", None)

        self.assertEqual(len(storage.saved_replies), 2)
        self.assertEqual(len(storage.saved_states), 2)
        self.assertEqual(
            storage.saved_replies[0].content[0].input,
            '{"q"',
        )
        self.assertEqual(
            storage.saved_replies[0].metadata[
                REPLY_CHECKPOINT_REPLAY_ENTRY_ID_METADATA_KEY
            ],
            "2-0",
        )

    async def test_tool_result_delta_does_not_count_toward_chars(self) -> None:
        """Streaming tool results do not contribute to the char threshold."""
        service, storage = await self._make_service()
        service._REPLY_CHECKPOINT_EVENT_THRESHOLD = 99
        service._REPLY_CHECKPOINT_CHAR_THRESHOLD = 5
        _FakeAgent.planned_events = [
            ReplyStartEvent(reply_id="reply-tool-result", name="Checkpoint Agent"),
            ToolResultStartEvent(
                reply_id="reply-tool-result",
                tool_call_id="tool-call-1",
                tool_call_name="search_docs",
            ),
            ToolResultTextDeltaEvent(
                reply_id="reply-tool-result",
                tool_call_id="tool-call-1",
                delta="abcdef",
            ),
        ]

        with (
            patch(
                "agentscope.app._service._chat.get_toolkit",
                new=AsyncMock(return_value=object()),
            ),
            patch(
                "agentscope.app._service._chat.get_model",
                new=AsyncMock(return_value=object()),
            ),
        ):
            await service.run("u1", "s1", "a1", None)

        self.assertEqual(len(storage.saved_replies), 1)
        self.assertEqual(len(storage.saved_states), 1)
        self.assertEqual(
            storage.saved_replies[0].metadata[
                REPLY_CHECKPOINT_REPLAY_ENTRY_ID_METADATA_KEY
            ],
            "2-0",
        )

    async def test_event_threshold_allows_checkpoint_for_tool_result_delta(
        self,
    ) -> None:
        """Every appended event can trigger a checkpoint once counts trip."""
        service, storage = await self._make_service()
        service._REPLY_CHECKPOINT_EVENT_THRESHOLD = 2
        service._REPLY_CHECKPOINT_CHAR_THRESHOLD = 99
        _FakeAgent.planned_events = [
            ReplyStartEvent(reply_id="reply-event-threshold", name="Checkpoint Agent"),
            ToolResultStartEvent(
                reply_id="reply-event-threshold",
                tool_call_id="tool-call-1",
                tool_call_name="search_docs",
            ),
            ToolResultTextDeltaEvent(
                reply_id="reply-event-threshold",
                tool_call_id="tool-call-1",
                delta="partial result",
            ),
        ]

        with (
            patch(
                "agentscope.app._service._chat.get_toolkit",
                new=AsyncMock(return_value=object()),
            ),
            patch(
                "agentscope.app._service._chat.get_model",
                new=AsyncMock(return_value=object()),
            ),
        ):
            await service.run("u1", "s1", "a1", None)

        self.assertEqual(len(storage.saved_replies), 2)
        self.assertEqual(len(storage.saved_states), 2)
        self.assertEqual(
            storage.saved_replies[0].content[0].output,
            "partial result",
        )
        self.assertEqual(
            storage.saved_replies[0].metadata[
                REPLY_CHECKPOINT_REPLAY_ENTRY_ID_METADATA_KEY
            ],
            "2-0",
        )
