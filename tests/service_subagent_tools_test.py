# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Behavior tests for the DSH-style subagent tool family."""

from __future__ import annotations

from contextlib import AsyncExitStack
from datetime import datetime, timedelta
import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis

from utils import AnyString

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app._manager import ChatRunRegistry
from agentscope.app._manager._subagent_reaper import SubAgentReaper
from agentscope.app._service._chat import ChatService
from agentscope.app._tools import InterruptAgent, ListAgents, SendMessage, Subagent, SubagentFork
from agentscope.app.message_bus import MessageBusKeys, RedisMessageBus
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    ChatModelConfig,
    RedisStorage,
    SessionConfig,
    SessionSource,
)
from agentscope.event import ReplyEndEvent, ReplyStartEvent, TextBlockDeltaEvent, TextBlockStartEvent
from agentscope.message import (
    AssistantMsg,
    TextBlock,
    ToolCallBlock,
    ToolCallState,
    ToolResultBlock,
    ToolResultState,
    UserMsg,
)
from agentscope.permission import PermissionContext
from agentscope.state import AgentState, ToolRuntimeContext
from agentscope.types import ReplyFinishedReason


def _make_storage(
    fr: fakeredis.aioredis.FakeRedis,
) -> RedisStorage:
    class _S(RedisStorage):
        async def __aenter__(self) -> "RedisStorage":  # type: ignore[override]
            self._client = fr
            return self

        async def aclose(self) -> None:
            self._client = None

    return _S()


def _make_bus(
    fr: fakeredis.aioredis.FakeRedis,
) -> RedisMessageBus:
    class _B(RedisMessageBus):
        async def __aenter__(self) -> "RedisMessageBus":  # type: ignore[override]
            self._client = fr
            return self

        async def aclose(self) -> None:
            self._client = None

    return _B()


def _make_agent_record(user_id: str, name: str) -> AgentRecord:
    return AgentRecord(
        user_id=user_id,
        source="user",
        data=AgentData(
            name=name,
            system_prompt=f"You are {name}.",
            context_config=ContextConfig(),
            react_config=ReActConfig(),
            allow_subagent_calls=True,
        ),
    )


def _make_runtime_state(
    *,
    session_id: str,
    workspace_id: str,
    tool_call_id: str,
) -> AgentState:
    state = AgentState(permission_context=PermissionContext())
    state.tool_context.runtime_context = ToolRuntimeContext(
        session_id=session_id,
        workspace_id=workspace_id,
        workdir="/tmp",
        current_tool_call_id=tool_call_id,
    )
    return state


class _FakeWorkspaceManager:
    async def get_workspace(self, *_args, **_kwargs) -> SimpleNamespace:
        return SimpleNamespace(workdir="/tmp/agentscope-subagent-runtime-tests")


class _FakeReplyAgent:
    planned_events: list[object] = []

    def __init__(self, **kwargs) -> None:
        self.name = kwargs["name"]
        self.state = kwargs["state"]

    async def observe(self, inputs) -> None:
        _ = inputs

    async def reply_stream(self, inputs=None):
        _ = inputs
        for event in self.planned_events:
            if isinstance(event, ReplyStartEvent):
                self.state.reply_id = event.reply_id
            yield event


class TestSubagentTools(IsolatedAsyncioTestCase):
    user_id = "u"

    async def asyncSetUp(self) -> None:
        self.fr = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self._stack = AsyncExitStack()
        self.storage = await self._stack.enter_async_context(
            _make_storage(self.fr),
        )
        self.bus = await self._stack.enter_async_context(_make_bus(self.fr))
        self.agent = _make_agent_record(self.user_id, "lead-agent")
        self.specialist_agent = _make_agent_record(self.user_id, "specialist-agent")
        self.agent.data.allowed_subagent_ids = [self.specialist_agent.id]
        await self.storage.upsert_agent(self.user_id, self.agent)
        await self.storage.upsert_agent(self.user_id, self.specialist_agent)
        self.root_session = await self.storage.upsert_session(
            user_id=self.user_id,
            agent_id=self.agent.id,
            config=SessionConfig(
                workspace_id="ws",
                chat_model_config=ChatModelConfig(
                    type="dashscope_credential",
                    credential_id="cred",
                    model="test-model",
                    parameters={},
                ),
            ),
        )

    async def asyncTearDown(self) -> None:
        await self._stack.aclose()
        await self.fr.aclose()

    class _FakeChatService:
        def __init__(self) -> None:
            self.calls: list[dict[str, str]] = []

        async def interrupt(self, *, user_id: str, session_id: str, agent_id: str) -> None:
            self.calls.append(
                {
                    "user_id": user_id,
                    "session_id": session_id,
                    "agent_id": agent_id,
                },
            )

    def _make_chat_service(self) -> ChatService:
        return ChatService(
            storage=self.storage,
            workspace_manager=_FakeWorkspaceManager(),
            scheduler_manager=SimpleNamespace(),
            background_task_manager=SimpleNamespace(),
            message_bus=self.bus,
            chat_run_registry=ChatRunRegistry(),
            custom_agent_cls=_FakeReplyAgent,
        )

    async def _spawn_child_session(self, *, name: str = "reviewer"):
        tool = Subagent(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.root_session.id,
            agent_id=self.agent.id,
        )
        await tool(
            description="Review code",
            prompt="Inspect the latest change carefully.",
            agent_id=self.specialist_agent.id,
            name=name,
            _agent_state=_make_runtime_state(
                session_id=self.root_session.id,
                workspace_id="ws",
                tool_call_id="tool-1",
            ),
        )
        child = (await self.storage.list_child_sessions(self.user_id, self.root_session.id))[0]
        task = await self.storage.get_active_subagent_task_by_child_session(
            self.user_id,
            child.id,
        )
        await self.bus.inbox_drain(child.id, max_count=10)
        await self.bus.queue_drain(MessageBusKeys.wakeup_queue(), max_count=10)
        return child, task

    async def _run_child_session_and_collect_settlement(
        self,
        *,
        finished_reason: ReplyFinishedReason,
        closing_text: str,
        reply_metadata: dict | None = None,
    ) -> tuple[dict, object]:
        child, task = await self._spawn_child_session()
        reply_id = f"reply-{finished_reason.value}"
        block_id = "block-1"
        _FakeReplyAgent.planned_events = [
            ReplyStartEvent(
                session_id=child.id,
                reply_id=reply_id,
                name="reviewer",
            ),
            TextBlockStartEvent(reply_id=reply_id, block_id=block_id),
            TextBlockDeltaEvent(
                reply_id=reply_id,
                block_id=block_id,
                delta=closing_text,
            ),
            ReplyEndEvent(
                session_id=child.id,
                reply_id=reply_id,
                finished_reason=finished_reason,
                metadata=reply_metadata or {},
            ),
        ]
        service = self._make_chat_service()
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
            await service.run(self.user_id, child.id, child.agent_id, None)

        leader_inbox = await self.bus.inbox_drain(self.root_session.id, max_count=10)
        self.assertEqual(len(leader_inbox), 1)
        updated_task = await self.storage.get_subagent_task(self.user_id, task.id)
        return leader_inbox[0][1], updated_task

    async def test_subagent_creates_child_session_and_task(self) -> None:
        tool = Subagent(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.root_session.id,
            agent_id=self.agent.id,
        )

        chunk = await tool(
            description="Review code",
            prompt="Inspect the latest change carefully.",
            agent_id=self.specialist_agent.id,
            name="reviewer",
            _agent_state=_make_runtime_state(
                session_id=self.root_session.id,
                workspace_id="ws",
                tool_call_id="tool-1",
            ),
        )

        payload = json.loads(chunk.content[0].text)
        self.assertEqual(
            payload,
            {
                "status": "started",
                "agent": "reviewer",
                "name": "reviewer",
                "agent_id": self.specialist_agent.id,
                "session_id": payload["session_id"],
                "task_id": AnyString(),
                "mode": "spawn",
                "description": "Review code",
            },
        )
        self.assertEqual(chunk.metadata, payload)

        children = await self.storage.list_child_sessions(
            self.user_id,
            self.root_session.id,
        )
        self.assertEqual(len(children), 1)
        child = children[0]
        self.assertEqual(child.source, SessionSource.SUBAGENT)
        self.assertEqual(child.agent_id, self.specialist_agent.id)

        hydrated_child = await self.storage.get_session(
            self.user_id,
            self.specialist_agent.id,
            child.id,
        )
        self.assertDictEqual(
            hydrated_child.state.middle_context["subagent_runtime"],
            {
                "root_session_id": self.root_session.id,
                "parent_session_id": self.root_session.id,
                "parent_name": "lead",
                "name": "reviewer",
                "path": "reviewer",
                "label": "Review code",
                "mode": "spawn",
            },
        )

        task = await self.storage.get_active_subagent_task_by_child_session(
            self.user_id,
            child.id,
        )
        self.assertEqual(task.name, "reviewer")
        self.assertEqual(task.path, "reviewer")
        self.assertEqual(task.mode, "spawn")
        self.assertEqual(task.delivery, "parent")
        self.assertTrue(task.continuable)

        inbox = await self.bus.inbox_drain(child.id, max_count=10)
        self.assertEqual(len(inbox), 1)
        hint_payload = inbox[0][1]
        self.assertDictEqual(
            hint_payload,
            {
                "type": "hint",
                "id": AnyString(),
                "hint": AnyString(),
                "source": AnyString(),
                "metadata": {},
            },
        )
        self.assertIn("<subagent-task", hint_payload["hint"])
        self.assertIn("Inspect the latest change carefully.", hint_payload["hint"])
        self.assertIn('"mode": "spawn"', hint_payload["source"])

    async def test_subagent_supports_explicit_agent_preset(self) -> None:
        tool = Subagent(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.root_session.id,
            agent_id=self.agent.id,
        )

        chunk = await tool(
            description="Ask specialist",
            prompt="Review the API migration plan.",
            name="specialist",
            agent_id=self.specialist_agent.id,
            _agent_state=_make_runtime_state(
                session_id=self.root_session.id,
                workspace_id="ws",
                tool_call_id="tool-specialist",
            ),
        )

        payload = json.loads(chunk.content[0].text)
        self.assertEqual(
            payload,
            {
                "status": "started",
                "agent": "specialist",
                "name": "specialist",
                "agent_id": self.specialist_agent.id,
                "session_id": payload["session_id"],
                "task_id": AnyString(),
                "mode": "spawn",
                "description": "Ask specialist",
            },
        )
        self.assertEqual(chunk.metadata, payload)

        children = await self.storage.list_child_sessions(
            self.user_id,
            self.root_session.id,
        )
        self.assertEqual(len(children), 1)
        child = children[0]
        self.assertEqual(child.agent_id, self.specialist_agent.id)

        hydrated_child = await self.storage.get_session(
            self.user_id,
            self.specialist_agent.id,
            child.id,
        )
        self.assertIsNotNone(hydrated_child)
        task = await self.storage.get_active_subagent_task_by_child_session(
            self.user_id,
            child.id,
        )
        self.assertEqual(task.child_agent_id, self.specialist_agent.id)

    async def test_subagent_enforces_max_active_subagents_per_tree(self) -> None:
        tool = Subagent(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.root_session.id,
            agent_id=self.agent.id,
            max_active_subagents=1,
        )

        first = await tool(
            description="Review code",
            prompt="Inspect the latest change carefully.",
            agent_id=self.specialist_agent.id,
            name="reviewer",
            _agent_state=_make_runtime_state(
                session_id=self.root_session.id,
                workspace_id="ws",
                tool_call_id="tool-cap-1",
            ),
        )
        self.assertEqual(first.state, ToolResultState.SUCCESS)

        second = await tool(
            description="Check docs",
            prompt="Review the migration guide too.",
            agent_id=self.specialist_agent.id,
            name="writer",
            _agent_state=_make_runtime_state(
                session_id=self.root_session.id,
                workspace_id="ws",
                tool_call_id="tool-cap-2",
            ),
        )
        self.assertEqual(second.state, ToolResultState.ERROR)
        self.assertEqual(
            second.metadata,
            {
                "error": (
                    "subagent limit reached (active child limit: 1); "
                    "wait for an existing child to finish or complete this work "
                    "with the current agents"
                ),
                "code": "ACTIVATION_LIMIT_REACHED",
                "limit": 1,
                "active_children": 1,
            },
        )
        children = await self.storage.list_child_sessions(self.user_id, self.root_session.id)
        self.assertEqual(len(children), 1)

    async def test_subagent_rejects_agent_preset_outside_allowlist(self) -> None:
        outsider = _make_agent_record(self.user_id, "outsider-agent")
        await self.storage.upsert_agent(self.user_id, outsider)

        tool = Subagent(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.root_session.id,
            agent_id=self.agent.id,
        )

        self.agent.data.allowed_subagent_ids = [self.specialist_agent.id]
        await self.storage.upsert_agent(self.user_id, self.agent)
        chunk = await tool(
            description="Ask outsider",
            prompt="Review the API migration plan.",
            agent_id=outsider.id,
            _agent_state=_make_runtime_state(
                session_id=self.root_session.id,
                workspace_id="ws",
                tool_call_id="tool-outsider",
            ),
        )

        self.assertEqual(chunk.state, ToolResultState.ERROR)
        self.assertEqual(
            chunk.metadata,
            {
                "error": f"subagent: agent '{outsider.id}' cannot be used as a subagent here.",
            },
        )
        self.assertEqual(
            await self.storage.list_child_sessions(self.user_id, self.root_session.id),
            [],
        )

    async def test_send_message_delivers_to_named_child(self) -> None:
        spawn = Subagent(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.root_session.id,
            agent_id=self.agent.id,
        )
        await spawn(
            description="Review code",
            prompt="Inspect the latest change carefully.",
            agent_id=self.specialist_agent.id,
            name="reviewer",
            _agent_state=_make_runtime_state(
                session_id=self.root_session.id,
                workspace_id="ws",
                tool_call_id="tool-1",
            ),
        )
        child = (await self.storage.list_child_sessions(self.user_id, self.root_session.id))[0]
        await self.bus.inbox_drain(child.id, max_count=10)

        task = await self.storage.get_active_subagent_task_by_child_session(
            self.user_id,
            child.id,
        )
        tool = SendMessage(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.root_session.id,
            agent_id=self.agent.id,
        )
        chunk = await tool(
            agent="reviewer",
            message="Focus on edge cases first.",
            _agent_state=_make_runtime_state(
                session_id=self.root_session.id,
                workspace_id="ws",
                tool_call_id="tool-2",
            ),
        )

        self.assertEqual(
            json.loads(chunk.content[0].text),
            {
                "status": "delivered",
                "agent": "reviewer",
                "session_id": child.id,
                "task_id": task.id,
                "mode": "message",
            },
        )
        self.assertEqual(
            chunk.metadata,
            {
                "status": "delivered",
                "agent": "reviewer",
                "session_id": child.id,
                "task_id": task.id,
                "mode": "message",
            },
        )
        inbox = await self.bus.inbox_drain(child.id, max_count=10)
        self.assertEqual(len(inbox), 1)
        hint_payload = inbox[0][1]
        self.assertIn("Agent lead sent a message", hint_payload["hint"])
        self.assertIn("Focus on edge cases first.", hint_payload["hint"])
        self.assertIn(task.id, hint_payload["source"])

    async def test_send_message_delivers_from_child_to_root_by_name(self) -> None:
        child, task = await self._spawn_child_session()

        tool = SendMessage(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=child.id,
            agent_id=self.specialist_agent.id,
        )
        chunk = await tool(
            agent="lead",
            message="I need one clarification before I continue.",
            _agent_state=_make_runtime_state(
                session_id=child.id,
                workspace_id="ws",
                tool_call_id="tool-child-to-parent",
            ),
        )

        self.assertEqual(
            json.loads(chunk.content[0].text),
            {
                "status": "delivered",
                "agent": "lead",
                "session_id": self.root_session.id,
                "task_id": None,
                "mode": "message",
            },
        )
        self.assertEqual(
            chunk.metadata,
            {
                "status": "delivered",
                "agent": "lead",
                "session_id": self.root_session.id,
                "task_id": None,
                "mode": "message",
            },
        )
        leader_inbox = await self.bus.inbox_drain(self.root_session.id, max_count=10)
        self.assertEqual(len(leader_inbox), 1)
        hint_payload = leader_inbox[0][1]
        self.assertIn("Agent reviewer sent a message", hint_payload["hint"])
        self.assertIn("I need one clarification before I continue.", hint_payload["hint"])
        self.assertNotIn(task.id, hint_payload["source"])

    async def test_subagent_fork_seeds_completed_steps_only(self) -> None:
        tool = SubagentFork(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.root_session.id,
            agent_id=self.agent.id,
        )
        state = _make_runtime_state(
            session_id=self.root_session.id,
            workspace_id="ws",
            tool_call_id="tool-1",
        )
        state.summary = "Compressed summary from older completed work."
        state.context.extend(
            [
                UserMsg(name="user", content="Please investigate the regression."),
                AssistantMsg(
                    id=state.reply_id,
                    name="assistant",
                    content=[
                        TextBlock(text="I found two suspicious commits."),
                        ToolCallBlock(
                            id="call-1",
                            name="shell",
                            input='{"command":"git log"}',
                            state=ToolCallState.FINISHED,
                        ),
                        ToolResultBlock(
                            id="call-1",
                            name="shell",
                            output="commit abc123",
                            state=ToolResultState.SUCCESS,
                        ),
                        TextBlock(
                            text="I am now checking whether the second commit changed the API contract.",
                        ),
                        ToolCallBlock(
                            id="call-2",
                            name="subagent_fork",
                            input='{"description":"Continue analysis"}',
                            state=ToolCallState.PENDING,
                        ),
                    ],
                ),
            ],
        )

        chunk = await tool(
            description="Continue analysis",
            prompt="Focus on the API contract impact.",
            agent_id=self.specialist_agent.id,
            name="forker",
            _agent_state=state,
        )

        child = (await self.storage.list_child_sessions(self.user_id, self.root_session.id))[0]
        child_session = await self.storage.get_session(
            self.user_id,
            self.specialist_agent.id,
            child.id,
        )
        self.assertEqual(chunk.metadata["mode"], "fork")
        self.assertEqual(child_session.state.summary, state.summary)
        self.assertEqual(len(child_session.state.context), 2)
        self.assertEqual(
            child_session.state.context[0].get_text_content(),
            "Please investigate the regression.",
        )
        self.assertEqual(
            child_session.state.context[1].get_text_content(),
            "I found two suspicious commits.",
        )
        self.assertEqual(
            len(child_session.state.context[1].content),
            3,
        )
        self.assertEqual(
            child_session.state.context[1].get_content_blocks("tool_call")[0].name,
            "shell",
        )
        self.assertEqual(
            child_session.state.context[1].get_content_blocks("tool_result")[0].output,
            "commit abc123",
        )
        self.assertEqual(
            child_session.state.context[1].get_content_blocks("tool_call")[0].id,
            "call-1",
        )
        self.assertEqual(
            child_session.state.context[1].get_content_blocks("tool_result")[0].id,
            "call-1",
        )
        persisted_messages = await self.storage.list_messages(
            self.user_id,
            child.id,
            limit=10,
        )
        self.assertEqual(len(persisted_messages), 2)

        inbox = await self.bus.inbox_drain(child.id, max_count=10)
        self.assertEqual(len(inbox), 1)
        hint_payload = inbox[0][1]
        self.assertNotIn("Context snapshot from the parent conversation:", hint_payload["hint"])
        self.assertIn("Focus on the API contract impact.", hint_payload["hint"])
        self.assertNotIn(
            "I am now checking whether the second commit changed the API contract.",
            hint_payload["hint"],
        )
        self.assertIn('"mode": "fork"', hint_payload["source"])

    async def test_list_agents_returns_root_and_child_paths(self) -> None:
        spawn = Subagent(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.root_session.id,
            agent_id=self.agent.id,
        )
        await spawn(
            description="Review code",
            prompt="Inspect the latest change carefully.",
            agent_id=self.specialist_agent.id,
            name="reviewer",
            _agent_state=_make_runtime_state(
                session_id=self.root_session.id,
                workspace_id="ws",
                tool_call_id="tool-1",
            ),
        )

        tool = ListAgents(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.root_session.id,
            agent_id=self.agent.id,
        )
        chunk = await tool()
        rows = json.loads(chunk.content[0].text)
        self.assertEqual(
            rows,
            [
                {
                    "name": "lead",
                    "status": "inactive",
                    "description": "root agent",
                },
                {
                    "name": "reviewer",
                    "status": "inactive",
                    "description": "Review code",
                },
            ],
        )
        self.assertEqual(chunk.metadata, {"agents": rows})

    async def test_list_agents_includes_self_and_descendants_for_child_session(self) -> None:
        self.specialist_agent.data.allowed_subagent_ids = [self.specialist_agent.id]
        await self.storage.upsert_agent(self.user_id, self.specialist_agent)

        child, _task = await self._spawn_child_session()

        spawn = Subagent(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=child.id,
            agent_id=self.specialist_agent.id,
        )
        await spawn(
            description="Check tests",
            prompt="Inspect the flaky test behavior carefully.",
            agent_id=self.specialist_agent.id,
            name="tester",
            _agent_state=_make_runtime_state(
                session_id=child.id,
                workspace_id="ws",
                tool_call_id="tool-nested",
            ),
        )

        tool = ListAgents(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=child.id,
            agent_id=self.specialist_agent.id,
        )
        chunk = await tool()
        rows = json.loads(chunk.content[0].text)
        self.assertEqual(
            rows,
            [
                {
                    "name": "lead",
                    "status": "inactive",
                    "description": "root agent",
                },
                {
                    "name": "reviewer",
                    "status": "inactive",
                    "description": "Review code",
                },
                {
                    "name": "reviewer/tester",
                    "status": "inactive",
                    "description": "Check tests",
                },
            ],
        )
        self.assertEqual(chunk.metadata, {"agents": rows})

    async def test_reaper_emits_runtime_owned_error_settlement(self) -> None:
        spawn = Subagent(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.root_session.id,
            agent_id=self.agent.id,
        )
        await spawn(
            description="Review code",
            prompt="Inspect the latest change carefully.",
            agent_id=self.specialist_agent.id,
            name="reviewer",
            _agent_state=_make_runtime_state(
                session_id=self.root_session.id,
                workspace_id="ws",
                tool_call_id="tool-1",
            ),
        )
        child = (await self.storage.list_child_sessions(self.user_id, self.root_session.id))[0]
        await self.bus.inbox_drain(child.id, max_count=10)
        await self.bus.queue_drain(MessageBusKeys.wakeup_queue(), max_count=10)

        task = await self.storage.get_active_subagent_task_by_child_session(
            self.user_id,
            child.id,
        )
        task.launch_requested_at = datetime.now() - timedelta(hours=1)
        task.last_progress_at = datetime.now() - timedelta(hours=1)
        await self.storage.upsert_subagent_task(self.user_id, task)

        reaper = SubAgentReaper(storage=self.storage, message_bus=self.bus)
        await reaper._check_active_task(user_id=self.user_id, task_id=task.id)

        leader_inbox = await self.bus.inbox_drain(self.root_session.id, max_count=10)
        self.assertEqual(len(leader_inbox), 1)
        hint_payload = leader_inbox[0][1]
        self.assertEqual(
            json.loads(hint_payload["source"]),
            {
                "label": "subagent_settled",
                "sublabel": "reviewer",
                "session_id": child.id,
                "state": "error",
                "stop_reason": "error",
                "subagent_task_id": task.id,
            },
        )
        self.assertEqual(
            hint_payload["hint"][0],
            {
                "type": "text",
                "text": "Background subagent reviewer failed before it finished.",
                "id": AnyString(),
            },
        )

        updated = await self.storage.get_subagent_task(self.user_id, task.id)
        self.assertEqual(updated.status, "error_delivered")
        self.assertEqual(updated.terminal_reason, "reaped_stalled_child")
        self.assertIsNone(
            await self.storage.get_active_subagent_task_by_child_session(
                self.user_id,
                child.id,
            ),
        )

    async def test_chat_service_settles_completed_child_run(self) -> None:
        hint_payload, updated_task = await self._run_child_session_and_collect_settlement(
            finished_reason=ReplyFinishedReason.COMPLETED,
            closing_text="I finished the delegated review.",
        )

        self.assertEqual(
            json.loads(hint_payload["source"]),
            {
                "label": "subagent_settled",
                "sublabel": "reviewer",
                "session_id": AnyString(),
                "state": "success",
                "stop_reason": "completed",
                "subagent_task_id": updated_task.id,
            },
        )
        self.assertEqual(
            [block["text"] for block in hint_payload["hint"]],
            [
                "Background subagent reviewer finished and will do no further work unless you send it more.",
                "Its closing message:",
                "I finished the delegated review.",
            ],
        )
        self.assertEqual(updated_task.status, "final_delivered")
        self.assertEqual(updated_task.terminal_reason, "completed")
        self.assertIsNone(
            await self.storage.get_active_subagent_task_by_child_session(
                self.user_id,
                json.loads(hint_payload["source"])["session_id"],
            ),
        )

    async def test_chat_service_settles_failed_child_run_with_diagnostic(self) -> None:
        hint_payload, updated_task = await self._run_child_session_and_collect_settlement(
            finished_reason=ReplyFinishedReason.ERROR,
            closing_text="I could not complete the task.",
            reply_metadata={
                "terminal_state": "failed",
                "run_error": {
                    "kind": "timeout",
                    "summary": "The model request timed out.",
                    "detail": "Upstream timed out after 30 seconds.",
                    "retryable": True,
                },
            },
        )

        self.assertEqual(
            json.loads(hint_payload["source"]),
            {
                "label": "subagent_settled",
                "sublabel": "reviewer",
                "session_id": AnyString(),
                "state": "error",
                "stop_reason": "error",
                "subagent_task_id": updated_task.id,
            },
        )
        self.assertEqual(
            [block["text"] for block in hint_payload["hint"]],
            [
                "Background subagent reviewer failed before it finished.",
                "Its closing message:",
                "I could not complete the task.",
                "The model request timed out.\n\nKind: timeout\nDetail: Upstream timed out after 30 seconds.\nRetryable: yes\n\nThis error may be recoverable. The main agent can resume this existing child session to continue execution.",
            ],
        )
        self.assertEqual(updated_task.status, "error_delivered")
        self.assertEqual(updated_task.terminal_reason, "error")

    async def test_chat_service_settles_interrupted_child_run(self) -> None:
        hint_payload, updated_task = await self._run_child_session_and_collect_settlement(
            finished_reason=ReplyFinishedReason.INTERRUPTED,
            closing_text="I was interrupted after the first pass.",
        )

        self.assertEqual(
            json.loads(hint_payload["source"]),
            {
                "label": "subagent_settled",
                "sublabel": "reviewer",
                "session_id": AnyString(),
                "state": "error",
                "stop_reason": "interrupted",
                "subagent_task_id": updated_task.id,
            },
        )
        self.assertEqual(
            [block["text"] for block in hint_payload["hint"]],
            [
                "Background subagent reviewer was stopped before it finished.",
                "Its closing message:",
                "I was interrupted after the first pass.",
            ],
        )
        self.assertEqual(updated_task.status, "error_delivered")
        self.assertEqual(updated_task.terminal_reason, "interrupted")

    async def test_interrupt_agent_targets_named_child(self) -> None:
        spawn = Subagent(
            storage=self.storage,
            message_bus=self.bus,
            user_id=self.user_id,
            session_id=self.root_session.id,
            agent_id=self.agent.id,
        )
        await spawn(
            description="Review code",
            prompt="Inspect the latest change carefully.",
            agent_id=self.specialist_agent.id,
            name="reviewer",
            _agent_state=_make_runtime_state(
                session_id=self.root_session.id,
                workspace_id="ws",
                tool_call_id="tool-1",
            ),
        )
        child = (await self.storage.list_child_sessions(self.user_id, self.root_session.id))[0]

        fake_chat_service = self._FakeChatService()
        tool = InterruptAgent(
            storage=self.storage,
            message_bus=self.bus,
            chat_service=fake_chat_service,
            chat_run_registry=object(),
            workspace=object(),
            user_id=self.user_id,
            session_id=self.root_session.id,
            agent_id=self.agent.id,
            workspace_id="ws",
        )

        chunk = await tool(agent="reviewer")
        self.assertEqual(chunk.content[0].text, "interrupt requested for agent reviewer")
        self.assertDictEqual(chunk.metadata, {"accepted": True})
        self.assertEqual(
            fake_chat_service.calls,
            [
                {
                    "user_id": self.user_id,
                    "session_id": child.id,
                    "agent_id": self.specialist_agent.id,
                },
            ],
        )
