# -*- coding: utf-8 -*-
"""Toolkit assembly for an (agent, session) pair."""
from typing import Any, TYPE_CHECKING

from .._manager import (
    BackgroundTaskManager,
    SchedulerManager,
)
from ..message_bus import MessageBus
from .._tools import (
    AgentCreate,
    ConfirmToolCalls,
    CreateTestSession,
    ImportAgentPackage,
    InterruptSession,
    SendSessionMessage,
    SubAgentRun,
    SubmitExternalResults,
    TeamCreate,
    TeamDelete,
    TeamSay,
    WaitNewMessages,
)
from .._types import AgentToolFactory, SubAgentTemplate
from ..storage import AgentRecord, SessionRecord, StorageBase
from .._manager import ChatRunRegistry
from ...tool import (
    TaskCreate,
    TaskGet,
    TaskList,
    ToolBase,
    TaskUpdate,
    Toolkit,
)
from ...workspace import WorkspaceBase

if TYPE_CHECKING:
    from ._agent_asset_store import AgentAssetStore
    from ._chat import ChatService


async def get_toolkit(
    *,
    storage: StorageBase,
    workspace: WorkspaceBase,
    scheduler_manager: SchedulerManager,
    background_task_manager: BackgroundTaskManager,
    message_bus: MessageBus,
    chat_service: "ChatService",
    chat_run_registry: ChatRunRegistry,
    user_id: str,
    agent_record: AgentRecord,
    session_record: SessionRecord,
    agent_asset_store: "AgentAssetStore | None" = None,
    extra_factory: AgentToolFactory | None = None,
    sub_agent_templates: dict[str, SubAgentTemplate] | None = None,
    channel_tools: list[ToolBase] | None = None,
) -> Toolkit:
    """Assemble the complete :class:`Toolkit` for one chat turn.

    Tool sources (in attachment order):

    1. Workspace builtins (Bash / Read / Write / Grep / …)
    2. Planning tools (:class:`TaskCreate` / :class:`TaskList` /
       :class:`TaskGet` / :class:`TaskUpdate`)
    3. Schedule control (:class:`ScheduleCreate` / :class:`ScheduleView`
       / :class:`ScheduleDelete` / :class:`ScheduleList`, from
       :meth:`SchedulerManager.list_tools`). Only attached when enabled in
       ``react_config.enabled_builtin_tool_groups`` and the session has a
       model configured (Schedule tools need a model to fire new chats
       with).
    4. Team tools — selected inline by ``agent_record.source`` and the
       ``team`` builtin-tool-group switch:
       worker (``"team"``) gets only ``TeamSay``; everyone else gets
       the full leader-side toolset
       (``TeamCreate / AgentCreate / TeamSay / TeamDelete``)
    5. Sub-agent execution (`SubAgentRun`) when enabled by agent config
       (independent of the ``team`` builtin-tool-group switch)
    6. Caller-supplied extras (``extra_factory``)

    Plus the workspace's skills and MCPs, which become the toolkit's
    ``skills_or_loaders`` and ``mcps`` parameters.

    Args:
        storage (`StorageBase`):
            Application storage backend; needed by team tools to read
            fresh team / session state at call time, and by schedule
            tools.
        workspace (`WorkspaceBase`):
            Pre-resolved per-session workspace (caller resolves it
            via :meth:`WorkspaceManagerBase.get_workspace`). Used here
            for tool / skill / MCP discovery.
        scheduler_manager (`SchedulerManager`):
            Application scheduler. Provides the four schedule tools and
            persists schedules through it.
        background_task_manager (`BackgroundTaskManager`):
            Application background-task registry. Currently unused here;
            kept in the signature for caller compatibility.
        message_bus (`MessageBus`):
            Application message bus; passed to team tools so they can
            push HintBlocks + wakeups when delivering inter-session
            messages.
        user_id (`str`):
            Caller user id.
        agent_record (`AgentRecord`):
            Pre-loaded agent record (loaded once by the caller). Its
            ``source`` field determines which team tools are attached.
        session_record (`SessionRecord`):
            Pre-loaded session record (loaded once by the caller).
            Used for the schedule-tool model configuration.
        extra_factory (`AgentToolFactory | None`, optional):
            Async factory invoked once per assembly to produce
            user/session-specific extra tools.
        sub_agent_templates (`dict[str, SubAgentTemplate] | None`, \
optional):
            Sub-agent template registry, keyed by template type.
            Passed to the ``AgentCreate`` tool so it can route to
            the appropriate template when a ``subagent_type`` is
            specified by the leader agent.

    Returns:
        `Toolkit`: Fully populated toolkit (tools + skills + MCPs).
    """
    _ = background_task_manager


    enabled_builtin_tool_groups = set(
        agent_record.data.react_config.enabled_builtin_tool_groups,
    )

    builtin_tool_groups = {
        "read": {"Glob", "Grep", "Read"},
        "edit": {"Edit", "Write"},
        "terminal": {"Bash"},
        "network": {"WebSearch", "WebFetch"},
    }

    # The general tools running in the workspace.
    tools = []
    for tool in await workspace.list_tools():
        group_name = next(
            (
                name
                for name, tool_names in builtin_tool_groups.items()
                if tool.name in tool_names
            ),
            None,
        )
        if group_name is None or group_name in enabled_builtin_tool_groups:
            tools.append(tool)

    # Planning tools — always on.
    tools += [TaskCreate(), TaskList(), TaskGet(), TaskUpdate()]

    # Schedule control. Requires a model config on this session because
    # ``ScheduleCreate`` records it into new ``ScheduleRecord`` instances.
    if (
        "schedule" in enabled_builtin_tool_groups
        and session_record.config.chat_model_config is not None
    ):
        tools.extend(
            await scheduler_manager.list_tools(
                user_id=user_id,
                agent_id=agent_record.id,
                chat_model_config=session_record.config.chat_model_config,
            ),
        )

    managed_tool_kwargs: dict[str, Any] = {
        "storage": storage,
        "message_bus": message_bus,
        "chat_service": chat_service,
        "chat_run_registry": chat_run_registry,
        "workspace": workspace,
        "user_id": user_id,
        "session_id": session_record.id,
        "agent_id": agent_record.id,
        "workspace_id": session_record.config.workspace_id,
        "agent_asset_store": agent_asset_store,
    }
    if "agent_management" in enabled_builtin_tool_groups:
        tools.append(ImportAgentPackage(**managed_tool_kwargs))
    if "session_management" in enabled_builtin_tool_groups:
        tools.extend(
            [
                CreateTestSession(**managed_tool_kwargs),
                SendSessionMessage(**managed_tool_kwargs),
                WaitNewMessages(**managed_tool_kwargs),
                ConfirmToolCalls(**managed_tool_kwargs),
                SubmitExternalResults(**managed_tool_kwargs),
                InterruptSession(**managed_tool_kwargs),
            ],
        )

    # Team tools — variant based on ``agent_record.source``. A worker
    # only gets TeamSay (to report back); a user-owned agent always
    # gets the full leader-side toolset. Each tool checks its own
    # preconditions (am I in a team? am I the leader?) at call time
    # against fresh storage, which is why the full set can be attached
    # unconditionally without needing a stale snapshot of team_id.
    team_tool_kwargs: dict[str, Any] = {
        "storage": storage,
        "message_bus": message_bus,
        "user_id": user_id,
        "session_id": session_record.id,
        "agent_id": agent_record.id,
    }
    allowed_subagents = []
    if (
        agent_record.data.allow_subagent_calls
        and agent_record.data.allowed_subagent_ids
    ):
        for subagent_id in agent_record.data.allowed_subagent_ids:
            subagent_record = await storage.get_agent(user_id, subagent_id)
            if subagent_record is None or subagent_record.user_id != user_id:
                continue
            allowed_subagents.append(
                {
                    "agent_id": subagent_record.id,
                    "name": subagent_record.data.name,
                    "description": subagent_record.data.description,
                },
            )
        tools.append(
            SubAgentRun(
                storage=storage,
                message_bus=message_bus,
                user_id=user_id,
                session_id=session_record.id,
                agent_id=agent_record.id,
                allowed_subagents=allowed_subagents,
            ),
        )

    if "team" in enabled_builtin_tool_groups and agent_record.source == "team":
        tools.append(TeamSay(**team_tool_kwargs, role="worker"))
    elif "team" in enabled_builtin_tool_groups:
        tools += [
            TeamCreate(**team_tool_kwargs),
            AgentCreate(
                **team_tool_kwargs,
                sub_agent_templates=sub_agent_templates or {},
            ),
            TeamSay(**team_tool_kwargs, role="leader"),
            TeamDelete(**team_tool_kwargs),
        ]

    # Caller-supplied extras.
    if extra_factory is not None:
        tools += await extra_factory(
            user_id,
            agent_record.id,
            session_record.id,
        )

    if channel_tools:
        tools += channel_tools

    return Toolkit(
        tools=tools,
        skills_or_loaders=await workspace.list_skills(),
        mcps=[
            client
            for client in await workspace.list_mcps()
            if client.connection_status == "connected"
        ],
    )
