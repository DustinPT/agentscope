# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for :func:`get_toolkit` — the single entry point that assembles
the per-chat-turn :class:`Toolkit` from every tool source the framework
manages.

Verifies the assembly rules:

- workspace builtins are always included;
- the four ``Task*`` planning tools are always included;
- the four ``Schedule*`` tools only when ``session.config.chat_model_config``
  is set and the ``schedule`` builtin tool group is enabled;
- team tools are gated by the ``team`` builtin tool group and then by
  ``agent_record.source``: ``"team"`` → one ``TeamSay`` (worker variant);
  anything else → the full leader-side toolset of four;
- ``TaskStop`` and ``reset_tools`` are not exposed;
- ``SubAgentRun`` still follows the existing sub-agent permissions config;
- caller-supplied ``extra_factory`` results land at the end.
"""
from typing import Any
from unittest import IsolatedAsyncioTestCase

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app._manager import (
    BackgroundTaskManager,
    SchedulerManager,
)
from agentscope.app._service import get_toolkit
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    ChatModelConfig,
    SessionConfig,
    SessionRecord,
)
from agentscope.tool import ToolBase


class _FakeWorkspace:
    """Stand-in for a resolved :class:`WorkspaceBase` — only the three
    methods :func:`get_toolkit` calls are implemented."""

    def __init__(
        self,
        tools: list[ToolBase] | None = None,
        skills: list | None = None,
        mcps: list | None = None,
    ) -> None:
        self._tools = tools or []
        self._skills = skills or []
        self._mcps = mcps or []

    async def list_tools(self) -> list[ToolBase]:
        """Return the configured workspace tools."""
        return list(self._tools)

    async def list_skills(self) -> list:
        """Return the configured workspace skills."""
        return list(self._skills)

    async def list_mcps(self) -> list:
        """Return the configured workspace MCP descriptors."""
        return list(self._mcps)


class _NullBus:
    """``MessageBus`` placeholder. Team tools only carry the reference;
    nothing in :func:`get_toolkit` actually awaits it."""


class _NoOpStorage:
    """Storage placeholder used during toolkit assembly."""

    def __init__(self, agents: dict[str, AgentRecord] | None = None) -> None:
        self._agents = agents or {}

    async def get_agent(
        self,
        _user_id: str,
        agent_id: str,
    ) -> AgentRecord | None:
        """Return a configured agent record if present."""
        return self._agents.get(agent_id)


def _make_agent(
    *,
    source: str = "user",
    name: str = "A",
    enabled_builtin_tool_groups: list[str] | None = None,
    allow_subagent_calls: bool = False,
    allowed_subagent_ids: list[str] | None = None,
) -> AgentRecord:
    """Build a minimal :class:`AgentRecord`."""
    return AgentRecord(
        user_id="u",
        source=source,
        data=AgentData(
            name=name,
            system_prompt=f"You are {name}.",
            context_config=ContextConfig(),
            react_config=ReActConfig(
                enabled_builtin_tool_groups=enabled_builtin_tool_groups
                or ["read", "edit", "schedule", "terminal", "team"],
            ),
            allow_subagent_calls=allow_subagent_calls,
            allowed_subagent_ids=allowed_subagent_ids or [],
        ),
    )


def _make_session(
    *,
    user_id: str,
    agent_id: str,
    with_model: bool,
) -> SessionRecord:
    """Build a minimal :class:`SessionRecord`, optionally with a chat
    model config."""
    cfg = SessionConfig(
        workspace_id="ws",
        chat_model_config=(
            ChatModelConfig(
                type="dashscope_credential",
                credential_id="c",
                model="m",
                parameters={},
            )
            if with_model
            else None
        ),
    )
    return SessionRecord(user_id=user_id, agent_id=agent_id, config=cfg)


def _tool_names(toolkit: Any) -> list[str]:
    """Extract every registered tool name from a :class:`Toolkit`,
    walking its tool groups."""
    return [t.name for group in toolkit.tool_groups for t in group.tools]


class _StubTool(ToolBase):
    """Minimal :class:`ToolBase` subclass that satisfies the abstract
    methods so :func:`get_toolkit` can register the instance.

    Sub-classes override ``name`` and ``description``; nothing in the
    tests actually calls the tool, so the implementations are no-ops.
    """

    name: str = "stub"
    description: str = "stub tool"
    input_schema: dict = {}
    is_concurrency_safe: bool = True
    is_read_only: bool = False
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    async def check_permissions(self, *args: Any, **kwargs: Any) -> None:
        """No-op permission check — the tests do not exercise it."""

    async def __call__(self, *args: Any, **kwargs: Any) -> None:
        """No-op invocation — the tests never execute the tool."""


def _make_named_tool(name: str, description: str = "stub tool") -> ToolBase:
    """Create a stub tool instance with a custom name."""

    class _NamedTool(_StubTool):
        pass

    _NamedTool.name = name
    _NamedTool.description = description
    return _NamedTool()


class TestGetToolkitBaseAssembly(IsolatedAsyncioTestCase):
    """User-owned agent (``source="user"``) gets the expected tools."""

    async def test_user_agent_gets_enabled_sources_without_taskstop(self) -> None:
        """A user-owned agent receives enabled workspace, planning,
        scheduling, and leader-side team tools, but not TaskStop."""
        agent = _make_agent(source="user")
        session = _make_session(
            user_id="u",
            agent_id=agent.id,
            with_model=True,
        )
        workspace = _FakeWorkspace(
            tools=[
                _make_named_tool("Bash"),
                _make_named_tool("Glob"),
                _make_named_tool("Grep"),
                _make_named_tool("Read"),
                _make_named_tool("Edit"),
                _make_named_tool("Write"),
                _make_named_tool("ws-extra"),
            ],
        )

        toolkit = await get_toolkit(
            storage=_NoOpStorage(),  # type: ignore[arg-type]
            workspace=workspace,  # type: ignore[arg-type]
            scheduler_manager=SchedulerManager(
                storage=_NoOpStorage(),  # type: ignore[arg-type]
                message_bus=_NullBus(),  # type: ignore[arg-type]
            ),
            background_task_manager=BackgroundTaskManager(),
            message_bus=_NullBus(),  # type: ignore[arg-type]
            user_id="u",
            agent_record=agent,
            session_record=session,
            extra_factory=None,
        )

        names = set(_tool_names(toolkit))
        self.assertTrue(
            {"Bash", "Glob", "Grep", "Read", "Edit", "Write", "ws-extra"}
            <= names,
        )
        # Planning tools present.
        self.assertTrue(
            {"TaskCreate", "TaskList", "TaskGet", "TaskUpdate"} <= names,
        )
        # Background task control removed.
        self.assertNotIn("TaskStop", names)
        # Schedule control present (model_config is set).
        self.assertTrue(
            {
                "ScheduleCreate",
                "ScheduleView",
                "ScheduleDelete",
                "ScheduleList",
            }
            <= names,
        )
        # Leader-side team tools (4 of them).
        self.assertTrue(
            {"TeamCreate", "AgentCreate", "TeamSay", "TeamDelete"} <= names,
        )
        schema_names = {
            schema["function"]["name"]
            for schema in await toolkit.get_tool_schemas()
        }
        self.assertNotIn("reset_tools", schema_names)

    async def test_workspace_tools_follow_group_toggles(self) -> None:
        """Workspace builtin tools are filtered by the configured groups."""
        agent = _make_agent(
            enabled_builtin_tool_groups=["read"],
        )
        session = _make_session(
            user_id="u",
            agent_id=agent.id,
            with_model=True,
        )
        workspace = _FakeWorkspace(
            tools=[
                _make_named_tool("Bash"),
                _make_named_tool("Glob"),
                _make_named_tool("Grep"),
                _make_named_tool("Read"),
                _make_named_tool("Edit"),
                _make_named_tool("Write"),
                _make_named_tool("ws-extra"),
            ],
        )
        toolkit = await get_toolkit(
            storage=_NoOpStorage(),  # type: ignore[arg-type]
            workspace=workspace,  # type: ignore[arg-type]
            scheduler_manager=SchedulerManager(
                storage=_NoOpStorage(),  # type: ignore[arg-type]
                message_bus=_NullBus(),  # type: ignore[arg-type]
            ),
            background_task_manager=BackgroundTaskManager(),
            message_bus=_NullBus(),  # type: ignore[arg-type]
            user_id="u",
            agent_record=agent,
            session_record=session,
            extra_factory=None,
        )

        names = set(_tool_names(toolkit))
        self.assertTrue({"Glob", "Grep", "Read", "ws-extra"} <= names)
        for missing in (
            "Bash",
            "Edit",
            "Write",
            "ScheduleCreate",
            "TeamCreate",
            "AgentCreate",
            "TeamSay",
            "TeamDelete",
        ):
            self.assertNotIn(missing, names)


class TestGetToolkitWorkerVariant(IsolatedAsyncioTestCase):
    """Worker agent (``source="team"``) only gets ``TeamSay``."""

    async def test_worker_only_gets_team_say(self) -> None:
        """A worker agent (``source="team"``) receives only ``TeamSay``
        from the team toolset."""
        agent = _make_agent(source="team", name="worker")
        session = _make_session(
            user_id="u",
            agent_id=agent.id,
            with_model=True,
        )
        toolkit = await get_toolkit(
            storage=_NoOpStorage(),  # type: ignore[arg-type]
            workspace=_FakeWorkspace(),  # type: ignore[arg-type]
            scheduler_manager=SchedulerManager(
                storage=_NoOpStorage(),  # type: ignore[arg-type]
                message_bus=_NullBus(),  # type: ignore[arg-type]
            ),
            background_task_manager=BackgroundTaskManager(),
            message_bus=_NullBus(),  # type: ignore[arg-type]
            user_id="u",
            agent_record=agent,
            session_record=session,
            extra_factory=None,
        )
        names = set(_tool_names(toolkit))
        # Only TeamSay from the team toolset.
        self.assertIn("TeamSay", names)
        for missing in ("TeamCreate", "AgentCreate", "TeamDelete"):
            self.assertNotIn(missing, names)

    async def test_worker_gets_no_team_tools_when_team_group_disabled(self) -> None:
        """Disabling the team group removes TeamSay for workers as well."""
        agent = _make_agent(
            source="team",
            name="worker",
            enabled_builtin_tool_groups=["read", "edit", "schedule", "terminal"],
        )
        session = _make_session(
            user_id="u",
            agent_id=agent.id,
            with_model=True,
        )
        toolkit = await get_toolkit(
            storage=_NoOpStorage(),  # type: ignore[arg-type]
            workspace=_FakeWorkspace(),  # type: ignore[arg-type]
            scheduler_manager=SchedulerManager(
                storage=_NoOpStorage(),  # type: ignore[arg-type]
                message_bus=_NullBus(),  # type: ignore[arg-type]
            ),
            background_task_manager=BackgroundTaskManager(),
            message_bus=_NullBus(),  # type: ignore[arg-type]
            user_id="u",
            agent_record=agent,
            session_record=session,
            extra_factory=None,
        )
        names = set(_tool_names(toolkit))
        self.assertNotIn("TeamSay", names)


class TestGetToolkitSchedulingGuard(IsolatedAsyncioTestCase):
    """``Schedule*`` tools are only attached when the session has a
    model configured."""

    async def test_no_schedule_tools_without_model_config(self) -> None:
        """Without a ``chat_model_config`` on the session, the four
        ``Schedule*`` tools are omitted from the toolkit."""
        agent = _make_agent(source="user")
        session = _make_session(
            user_id="u",
            agent_id=agent.id,
            with_model=False,
        )
        toolkit = await get_toolkit(
            storage=_NoOpStorage(),  # type: ignore[arg-type]
            workspace=_FakeWorkspace(),  # type: ignore[arg-type]
            scheduler_manager=SchedulerManager(
                storage=_NoOpStorage(),  # type: ignore[arg-type]
                message_bus=_NullBus(),  # type: ignore[arg-type]
            ),
            background_task_manager=BackgroundTaskManager(),
            message_bus=_NullBus(),  # type: ignore[arg-type]
            user_id="u",
            agent_record=agent,
            session_record=session,
            extra_factory=None,
        )
        names = set(_tool_names(toolkit))
        for missing in (
            "ScheduleCreate",
            "ScheduleView",
            "ScheduleDelete",
            "ScheduleList",
        ):
            self.assertNotIn(missing, names)

    async def test_schedule_group_disabled_hides_schedule_tools(self) -> None:
        """Disabling the schedule group omits schedule tools even with a model."""
        agent = _make_agent(
            enabled_builtin_tool_groups=["read", "edit", "terminal", "team"],
        )
        session = _make_session(
            user_id="u",
            agent_id=agent.id,
            with_model=True,
        )
        toolkit = await get_toolkit(
            storage=_NoOpStorage(),  # type: ignore[arg-type]
            workspace=_FakeWorkspace(),  # type: ignore[arg-type]
            scheduler_manager=SchedulerManager(
                storage=_NoOpStorage(),  # type: ignore[arg-type]
                message_bus=_NullBus(),  # type: ignore[arg-type]
            ),
            background_task_manager=BackgroundTaskManager(),
            message_bus=_NullBus(),  # type: ignore[arg-type]
            user_id="u",
            agent_record=agent,
            session_record=session,
            extra_factory=None,
        )
        names = set(_tool_names(toolkit))
        for missing in (
            "ScheduleCreate",
            "ScheduleView",
            "ScheduleDelete",
            "ScheduleList",
        ):
            self.assertNotIn(missing, names)


class TestGetToolkitSubAgentRun(IsolatedAsyncioTestCase):
    """Sub-agent execution follows the existing allow-list strategy."""

    async def test_subagentrun_enabled_strategy_unchanged(self) -> None:
        """SubAgentRun still depends on allow_subagent_calls, not team group."""
        allowed_agent = _make_agent(name="child")
        agent = _make_agent(
            enabled_builtin_tool_groups=["read"],
            allow_subagent_calls=True,
            allowed_subagent_ids=[allowed_agent.id],
        )
        session = _make_session(
            user_id="u",
            agent_id=agent.id,
            with_model=True,
        )
        toolkit = await get_toolkit(
            storage=_NoOpStorage({allowed_agent.id: allowed_agent}),  # type: ignore[arg-type]
            workspace=_FakeWorkspace(),  # type: ignore[arg-type]
            scheduler_manager=SchedulerManager(
                storage=_NoOpStorage(),  # type: ignore[arg-type]
                message_bus=_NullBus(),  # type: ignore[arg-type]
            ),
            background_task_manager=BackgroundTaskManager(),
            message_bus=_NullBus(),  # type: ignore[arg-type]
            user_id="u",
            agent_record=agent,
            session_record=session,
            extra_factory=None,
        )
        self.assertIn("SubAgentRun", set(_tool_names(toolkit)))


class TestGetToolkitExtraFactory(IsolatedAsyncioTestCase):
    """Tools returned by ``extra_factory`` end up in the final toolkit."""

    async def test_extra_factory_tools_are_attached(self) -> None:
        """Tools returned by ``extra_factory`` end up in the final
        toolkit alongside the framework-builtin ones."""

        class _ExtraTool(_StubTool):
            """Stub tool emitted by the ``extra_factory`` callback."""

            name: str = "my-extra"
            description: str = "stub extra tool"

        extra_tool = _ExtraTool()

        async def factory(
            _user_id: str,
            _agent_id: str,
            _session_id: str,
        ) -> list[ToolBase]:
            """Stub extra-factory that always returns ``extra_tool``."""
            return [extra_tool]

        agent = _make_agent()
        session = _make_session(
            user_id="u",
            agent_id=agent.id,
            with_model=True,
        )
        toolkit = await get_toolkit(
            storage=_NoOpStorage(),  # type: ignore[arg-type]
            workspace=_FakeWorkspace(),  # type: ignore[arg-type]
            scheduler_manager=SchedulerManager(
                storage=_NoOpStorage(),  # type: ignore[arg-type]
                message_bus=_NullBus(),  # type: ignore[arg-type]
            ),
            background_task_manager=BackgroundTaskManager(),
            message_bus=_NullBus(),  # type: ignore[arg-type]
            user_id="u",
            agent_record=agent,
            session_record=session,
            extra_factory=factory,
        )
        self.assertIn("my-extra", _tool_names(toolkit))
