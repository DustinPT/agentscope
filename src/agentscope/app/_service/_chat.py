# -*- coding: utf-8 -*-
"""Chat service encapsulating agent execution + persistence logic.

This is the single source of truth for running an agent against a
session. Both the HTTP chat endpoint and the wakeup dispatcher call
:meth:`ChatService.run`, guaranteeing identical message persistence,
middleware wiring, and state handling.

Events produced by the agent are not exposed back through this method
— they are published to the message bus inside the run, and any client
that wants them subscribes through the
``GET /sessions/{sid}/stream`` SSE endpoint.
"""
import asyncio
from dataclasses import dataclass

from fastapi import HTTPException

from .._reply_state import (
    is_reply_awaiting_tool_interaction,
    set_reply_checkpoint_replay_entry_id,
)
from ..message_bus import MessageBus
from ..storage import SessionConfig, SessionSource, StorageBase
from .._manager import BackgroundTaskManager, SchedulerManager
from .._manager import ChatRunRegistry
from ..workspace_manager import WorkspaceManagerBase
from ..middleware import (
    InboxMiddleware,
    StateChangeMiddleware,
    SubAgentMiddleware,
    ToolOffloadMiddleware,
)
from .._types import (
    AgentMiddlewareFactory,
    AgentToolFactory,
    SubAgentTemplate,
)
from ._model import get_model
from ._session_title import generate_session_title
from ._toolkit import get_toolkit

from ..._logging import logger
from ...agent import Agent, ModelConfig
from ...event import (
    CustomEvent,
    DataBlockDeltaEvent,
    ExternalExecutionResultEvent,
    ReplyStartEvent,
    SessionInterruptEvent,
    ThinkingBlockDeltaEvent,
    TextBlockDeltaEvent,
    ToolCallDeltaEvent,
    UserConfirmResultEvent,
)
from ...message import AssistantMsg, Msg
from ...permission import AdditionalWorkingDirectory


@dataclass
class _ReplyCheckpointState:
    """Per-run counters used to decide when to persist a reply checkpoint."""

    event_count: int = 0
    char_count: int = 0
    latest_replay_entry_id: str | None = None

    def reset(self) -> None:
        """Clear counters after a checkpoint write."""
        self.event_count = 0
        self.char_count = 0


class ChatService:
    """Run an agent against a session, persisting input/reply messages
    and updated agent state.

    Shared by the HTTP chat endpoint and the wakeup dispatcher so both
    paths go through identical validation, assembly, and persistence.

    Session serialisation and event fan-out are both handled by the
    :class:`MessageBus`: :meth:`bus.session_run` acquires a distributed
    lock (guaranteeing at most one chat run per session across all
    processes), and :meth:`bus.session_publish_event` writes each event
    to both a replay log (for late-joining subscribers) and a live
    Pub/Sub channel.
    """

    _REPLY_CHECKPOINT_EVENT_THRESHOLD = MessageBus._SESSION_REPLAY_MAX_LEN // 2
    """Maximum events to buffer locally before checkpointing the reply."""

    _REPLY_CHECKPOINT_CHAR_THRESHOLD = 2_000
    """Maximum model-output characters to buffer locally before checkpointing."""

    def __init__(
        self,
        storage: StorageBase,
        workspace_manager: WorkspaceManagerBase,
        scheduler_manager: SchedulerManager,
        background_task_manager: BackgroundTaskManager,
        message_bus: MessageBus,
        chat_run_registry: ChatRunRegistry,
        extra_agent_middlewares: AgentMiddlewareFactory | None = None,
        extra_agent_tools: AgentToolFactory | None = None,
        custom_subagent_templates: dict[str, SubAgentTemplate] | None = None,
        custom_agent_cls: type[Agent] | None = None,
    ) -> None:
        """Initialize chat service.

        Args:
            storage (`StorageBase`):
                Application storage backend.
            workspace_manager (`WorkspaceManagerBase`):
                Provides per-session workspace (tools, MCPs, skills) used
                during agent assembly.
            scheduler_manager (`SchedulerManager`):
                Application scheduler — passed through to
                :func:`get_toolkit` so the agent toolkit gets the four
                ``Schedule*`` tools.
            background_task_manager (`BackgroundTaskManager`):
                Tracks offloaded long-running tool tasks. Also provides
                the :class:`TaskStop` tool through
                :func:`get_toolkit`.
            message_bus (`MessageBus`):
                Application-wide message bus. Provides session-level
                distributed locking (via :meth:`session_run`), event
                replay + live fan-out (via :meth:`session_publish_event`),
                and inbox delivery (via :class:`InboxMiddleware`).
            chat_run_registry (`ChatRunRegistry`):
                Per-process registry used to spawn child session chat runs.
            extra_agent_middlewares (`AgentMiddlewareFactory | None`, \
             optional):
                Async factory invoked at every chat turn to produce
                user/session-specific middlewares to attach to the agent.
            extra_agent_tools (`AgentToolFactory | None`, optional):
                Async factory invoked at every chat turn to produce
                user/session-specific tools to register in the toolkit.
            custom_subagent_templates (`dict[str, SubAgentTemplate] | None`,\
             optional):
                Sub-agent template registry, keyed by template type.
                Passed through to :func:`get_toolkit` so that
                ``AgentCreate`` can route to the appropriate template
                when a ``subagent_type`` is specified.
            custom_agent_cls (`type[Agent] | None`, optional):
                Custom :class:`Agent` subclass for assembling agents.
                Falls back to :class:`Agent` when ``None``.
        """
        self._storage = storage
        self._workspace_manager = workspace_manager
        self._scheduler_manager = scheduler_manager
        self._background_task_manager = background_task_manager
        self._message_bus = message_bus
        self._chat_run_registry = chat_run_registry
        self._extra_agent_middlewares = extra_agent_middlewares
        self._extra_agent_tools = extra_agent_tools
        self._sub_agent_templates = custom_subagent_templates
        self._agent_cls = custom_agent_cls or Agent

    @staticmethod
    def _checkpoint_char_delta(event: object) -> int:
        """Return the model-output character contribution of *event*."""
        if isinstance(event, TextBlockDeltaEvent):
            return len(event.delta)
        if isinstance(event, DataBlockDeltaEvent):
            return len(event.data)
        if isinstance(event, ThinkingBlockDeltaEvent):
            return len(event.delta)
        if isinstance(event, ToolCallDeltaEvent):
            return len(event.delta)
        return 0

    def _record_checkpoint_event(
        self,
        checkpoint_state: _ReplyCheckpointState,
        event: object,
    ) -> None:
        """Accumulate counters for a reply event that was appended locally."""
        checkpoint_state.event_count += 1
        checkpoint_state.char_count += self._checkpoint_char_delta(event)

    def _should_checkpoint_reply(
        self,
        checkpoint_state: _ReplyCheckpointState,
    ) -> bool:
        """Return whether the current counters require a checkpoint write."""
        return (
            checkpoint_state.event_count
            >= self._REPLY_CHECKPOINT_EVENT_THRESHOLD
            or checkpoint_state.char_count
            >= self._REPLY_CHECKPOINT_CHAR_THRESHOLD
        )

    async def _checkpoint_reply(
        self,
        *,
        user_id: str,
        session_id: str,
        agent_id: str,
        reply_msg: Msg,
        agent: Agent,
        checkpoint_state: _ReplyCheckpointState,
    ) -> None:
        """Persist the in-progress reply and current agent state."""
        set_reply_checkpoint_replay_entry_id(
            reply_msg,
            checkpoint_state.latest_replay_entry_id,
        )
        await self._storage.upsert_message(
            user_id,
            session_id,
            reply_msg,
        )
        await self._storage.update_session_state(
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            state=agent.state,
        )

    async def _maybe_checkpoint_reply(
        self,
        *,
        user_id: str,
        session_id: str,
        agent_id: str,
        reply_msg: Msg | None,
        agent: Agent,
        checkpoint_state: _ReplyCheckpointState,
    ) -> None:
        """Persist an in-progress reply once the configured thresholds trip."""
        if reply_msg is None or not self._should_checkpoint_reply(
            checkpoint_state,
        ):
            return
        await self._checkpoint_reply(
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
            reply_msg=reply_msg,
            agent=agent,
            checkpoint_state=checkpoint_state,
        )
        checkpoint_state.reset()

    @staticmethod
    def _extract_first_user_message(
        input_msg: Msg
        | list[Msg]
        | UserConfirmResultEvent
        | ExternalExecutionResultEvent
        | SessionInterruptEvent
        | None,
    ) -> Msg | None:
        """Return the first user message from a new-turn input payload."""
        if isinstance(input_msg, Msg):
            return input_msg if input_msg.role == "user" else None
        if isinstance(input_msg, list):
            for msg in input_msg:
                if msg.role == "user":
                    return msg
        return None

    async def _maybe_update_session_title(
        self,
        *,
        user_id: str,
        agent_id: str,
        session_id: str,
        current_name: str,
        model_cfg,
        first_user_msg: Msg | None,
        first_reply_msg: Msg | None,
    ) -> None:
        """Best-effort async title generation for a newly created session."""
        if not current_name or first_user_msg is None or first_reply_msg is None:
            return

        latest_session = await self._storage.get_session(
            user_id,
            agent_id,
            session_id,
        )
        if latest_session is None:
            return
        if latest_session.source != SessionSource.USER:
            return
        if latest_session.parent_session_id is not None:
            return
        if latest_session.config.name != current_name:
            return

        try:
            title_model = await get_model(user_id, model_cfg, self._storage)
            title = await generate_session_title(
                title_model,
                first_user_msg=first_user_msg,
                first_reply_msg=first_reply_msg,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to generate session title for session %s: %s",
                session_id,
                exc,
            )
            return

        if not title or title == current_name:
            return

        latest_session = await self._storage.get_session(
            user_id,
            agent_id,
            session_id,
        )
        if latest_session is None or latest_session.config.name != current_name:
            return

        await self._storage.upsert_session(
            user_id=user_id,
            agent_id=agent_id,
            config=SessionConfig.model_validate(
                {
                    **latest_session.config.model_dump(mode="json"),
                    "name": title,
                },
            ),
            state=latest_session.state,
            session_id=session_id,
        )

        event = CustomEvent(name="session_updated", value={"name": title})
        await self._message_bus.session_publish_event(
            session_id,
            event.model_dump(mode="json"),
        )

    def _schedule_session_title_update(
        self,
        *,
        user_id: str,
        agent_id: str,
        session_id: str,
        current_name: str,
        model_cfg,
        first_user_msg: Msg | None,
        first_reply_msg: Msg | None,
    ) -> None:
        """Run best-effort title generation outside the active chat run."""
        task = asyncio.create_task(
            self._maybe_update_session_title(
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
                current_name=current_name,
                model_cfg=model_cfg,
                first_user_msg=first_user_msg,
                first_reply_msg=first_reply_msg,
            ),
            name=f"session-title:{session_id}",
        )

        def _log_exception(completed_task: asyncio.Task) -> None:
            try:
                completed_task.result()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Background session title generation failed for session %s: %s",
                    session_id,
                    exc,
                )

        task.add_done_callback(_log_exception)

    async def run(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        input_msg: Msg
        | list[Msg]
        | UserConfirmResultEvent
        | ExternalExecutionResultEvent
        | SessionInterruptEvent
        | None = None,
    ) -> None:
        """Drive a chat run to completion.

        Persists input messages (Case A) or the incoming continuation
        event applied to the existing reply (Case B / Case C), runs the
        agent while publishing every produced event to the message bus,
        and persists the rebuilt reply ``Msg`` + updated agent state
        when finished.

        Session serialisation is handled by the bus's distributed lock
        (:meth:`MessageBus.session_run`); events are simultaneously
        persisted to the replay log and fanned out on the live channel
        via :meth:`MessageBus.session_publish_event`. Exceptions are
        logged and swallowed so a single failed fire does not tear
        down its trigger (HTTP request task, wakeup dispatcher, …).

        Args:
            user_id (`str`):
                Authenticated caller's user ID.
            session_id (`str`):
                Target session ID.
            agent_id (`str`):
                Agent to run.
            input_msg:
                One of:

                - ``Msg`` / ``list[Msg]``: new user message(s) (Case A).
                - ``None``: continue from current state — used by the
                  wakeup dispatcher when there is no fresh user input
                  but pending inbox content needs draining (Case A
                  with no input).
                - ``UserConfirmResultEvent`` /
                  ``ExternalExecutionResultEvent``: resume an awaiting
                  tool call (Case B).
                - ``SessionInterruptEvent``: interrupt an awaiting tool
                  interaction and close the current reply without model
                  reasoning (Case C).
        """
        try:
            await self._run_impl(user_id, session_id, agent_id, input_msg)
        except Exception as e:
            logger.exception(
                "ChatService.run failed for user_id=%s session_id=%s "
                "agent_id=%s, error=%s",
                user_id,
                session_id,
                agent_id,
                str(e),
            )

    async def _run_impl(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        input_msg: Msg
        | list[Msg]
        | UserConfirmResultEvent
        | ExternalExecutionResultEvent
        | SessionInterruptEvent
        | None,
    ) -> None:
        """The actual chat-run body; wrapped by :meth:`run` for error
        swallowing. Separated so the try/except doesn't bury the
        per-step logic at one extra indentation level."""

        # ----------------------------------------------------------------
        # 1. Load records + resolve workspace ONCE here, reused below.
        # Reject missing records up front with a clear error so the
        # downstream assembly code can rely on non-None values.
        # ----------------------------------------------------------------
        agent_record = await self._storage.get_agent(user_id, agent_id)
        if agent_record is None:
            raise HTTPException(
                status_code=404,
                detail=f"Agent {agent_id!r} not found.",
            )
        session_record = await self._storage.get_session(
            user_id,
            agent_id,
            session_id,
        )
        if session_record is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Session {session_id!r} not found for "
                    f"agent {agent_id!r}."
                ),
            )
        existing_messages = await self._storage.list_messages(
            user_id,
            session_id,
            limit=1,
        )
        first_user_msg = self._extract_first_user_message(input_msg)
        should_attempt_session_title = (
            not existing_messages
            and first_user_msg is not None
            and session_record.source == SessionSource.USER
            and session_record.parent_session_id is None
        )
        initial_session_name = session_record.config.name
        workspace = await self._workspace_manager.get_workspace(
            user_id,
            agent_id,
            session_id,
            session_record.config.workspace_id,
            default_mcps=agent_record.data.mcps,
            skill_assets=agent_record.data.skills,
        )

        # Add workspace working directory to the permission context
        if (
            workspace.workdir
            not in session_record.state.permission_context.working_directories
        ):
            session_record.state.permission_context.working_directories[
                workspace.workdir
            ] = AdditionalWorkingDirectory(
                path=workspace.workdir,
                source="session",
            )

        # ----------------------------------------------------------------
        # 2. Toolkit (workspace tools + planning + TaskStop + schedule +
        # team + extras + skills + mcps).
        # ----------------------------------------------------------------
        toolkit = await get_toolkit(
            storage=self._storage,
            workspace=workspace,
            scheduler_manager=self._scheduler_manager,
            background_task_manager=self._background_task_manager,
            message_bus=self._message_bus,
            user_id=user_id,
            agent_record=agent_record,
            session_record=session_record,
            extra_factory=self._extra_agent_tools,
            sub_agent_templates=self._sub_agent_templates,
        )

        # ----------------------------------------------------------------
        # 3. Middlewares — framework-supplied first, then caller extras.
        # Background-tool completions deliver their results via
        # ``message_bus.inbox_push + enqueue_wakeup``, so the dispatcher
        # (any process) wakes an idle session — no in-process retrigger
        # plumbing is needed here.
        # ----------------------------------------------------------------
        middlewares: list = [
            InboxMiddleware(self._message_bus),
            StateChangeMiddleware(
                message_bus=self._message_bus,
                session_id=session_id,
            ),
        ]
        if agent_record.data.react_config.enable_tool_offload:
            middlewares.append(
                ToolOffloadMiddleware(
                    bg_manager=self._background_task_manager,
                    message_bus=self._message_bus,
                    user_id=user_id,
                    agent_id=agent_id,
                ),
            )
        if session_record.parent_session_id is not None:
            middlewares.append(
                SubAgentMiddleware(
                    storage=self._storage,
                    message_bus=self._message_bus,
                    user_id=user_id,
                    agent_id=agent_id,
                    session_id=session_id,
                ),
            )
        if self._extra_agent_middlewares is not None:
            middlewares.extend(
                await self._extra_agent_middlewares(
                    user_id,
                    agent_id,
                    session_id,
                ),
            )

        # ----------------------------------------------------------------
        # 4. Model + fallback (resolved from session's config).
        # ----------------------------------------------------------------
        model_cfg = session_record.config.chat_model_config
        if not model_cfg:
            raise HTTPException(
                status_code=404,
                detail=f"No model configuration found for agent {agent_id}",
            )
        model = await get_model(user_id, model_cfg, self._storage)

        fallback_cfg = session_record.config.fallback_chat_model_config
        fallback_model = (
            await get_model(user_id, fallback_cfg, self._storage)
            if fallback_cfg is not None
            else None
        )

        # ----------------------------------------------------------------
        # 5. Assemble the Agent.
        # ----------------------------------------------------------------
        agent_state = session_record.state
        agent_state.session_id = session_id
        agent = self._agent_cls(
            name=agent_record.data.name,
            system_prompt=agent_record.data.system_prompt,
            model=model,
            toolkit=toolkit,
            model_config=ModelConfig(fallback_model=fallback_model),
            context_config=agent_record.data.context_config,
            react_config=agent_record.data.react_config,
            state=agent_state,
            middlewares=middlewares,
            offloader=workspace,
        )

        # ----------------------------------------------------------------
        # 6. Guard: skip wake-up driven runs when the agent is parked on
        # an awaiting tool call.
        #
        # Wake-ups deliver pending inbox content (team messages, etc.) by
        # poking the dispatcher to run the session with ``input_msg=None``.
        # If the agent is currently parked on an ``ASKING`` or
        # ``SUBMITTED`` tool call (waiting for user confirmation or
        # external-execution results), kicking off another ``None`` run
        # would hit :meth:`Agent._check_incoming_event`, which rightly
        # rejects ``None`` when there is something to confirm — and fail
        # the run noisily. The inbox content is safe to leave queued:
        # whenever the user does confirm (or the external result lands),
        # the resuming run's next reasoning step lets
        # :class:`InboxMiddleware` drain the queue naturally.
        # ----------------------------------------------------------------
        if input_msg is None and agent.state.context:
            if is_reply_awaiting_tool_interaction(agent):
                logger.info(
                    "Skipping wake-up for session %s: agent is parked on "
                    "awaiting tool call(s); inbox messages will be drained "
                    "when the agent resumes.",
                    session_id,
                )
                return

        # ----------------------------------------------------------------
        # 7. Run the agent inside the bus's distributed session lock
        # ----------------------------------------------------------------
        needs_followup_wakeup = False
        reply_msg: Msg | None = None
        async with self._message_bus.session_run(session_id):
            checkpoint_state = _ReplyCheckpointState()

            if input_msg is None or isinstance(input_msg, (Msg, list)):
                # Case A: new reply (user message(s), or retrigger with
                # empty input)
                if isinstance(input_msg, (Msg, list)):
                    input_msgs = (
                        [input_msg]
                        if isinstance(input_msg, Msg)
                        else input_msg
                    )
                    for msg in input_msgs:
                        await self._storage.upsert_message(
                            user_id,
                            session_id,
                            msg,
                        )

                async for event in agent.reply_stream(inputs=input_msg):
                    entry_id = await self._message_bus.session_publish_event(
                        session_id,
                        event.model_dump(mode="json"),
                    )
                    checkpoint_state.latest_replay_entry_id = entry_id
                    if isinstance(event, ReplyStartEvent):
                        reply_msg = AssistantMsg(
                            id=event.reply_id,
                            name=event.name,
                            content=[],
                        )
                    elif reply_msg is not None:
                        reply_msg.append_event(event)
                        self._record_checkpoint_event(
                            checkpoint_state,
                            event,
                        )
                        await self._maybe_checkpoint_reply(
                            user_id=user_id,
                            session_id=session_id,
                            agent_id=agent_id,
                            reply_msg=reply_msg,
                            agent=agent,
                            checkpoint_state=checkpoint_state,
                        )

            elif isinstance(
                input_msg,
                (UserConfirmResultEvent, ExternalExecutionResultEvent),
            ):
                # Case B: continuation (UserConfirmResult / ExternalExecResult)
                reply_msg = await self._storage.get_message(
                    user_id,
                    session_id,
                    agent.state.reply_id,
                )

                if reply_msg is None:
                    logger.warning(
                        "Reply message %r not found in storage for session "
                        "%r; tool-call state changes from the incoming event "
                        "will not be persisted.",
                        agent.state.reply_id,
                        session_id,
                    )
                elif input_msg:
                    reply_msg.append_event(input_msg)
                    self._record_checkpoint_event(
                        checkpoint_state,
                        input_msg,
                    )
                    await self._maybe_checkpoint_reply(
                        user_id=user_id,
                        session_id=session_id,
                        agent_id=agent_id,
                        reply_msg=reply_msg,
                        agent=agent,
                        checkpoint_state=checkpoint_state,
                    )

                async for event in agent.reply_stream(inputs=input_msg):
                    entry_id = await self._message_bus.session_publish_event(
                        session_id,
                        event.model_dump(mode="json"),
                    )
                    checkpoint_state.latest_replay_entry_id = entry_id
                    if reply_msg is not None:
                        reply_msg.append_event(event)
                        self._record_checkpoint_event(
                            checkpoint_state,
                            event,
                        )
                        await self._maybe_checkpoint_reply(
                            user_id=user_id,
                            session_id=session_id,
                            agent_id=agent_id,
                            reply_msg=reply_msg,
                            agent=agent,
                            checkpoint_state=checkpoint_state,
                        )

            else:
                # Case C: session interrupt (interrupt awaiting tool calls
                # without entering model reasoning)
                reply_msg = await self._storage.get_message(
                    user_id,
                    session_id,
                    agent.state.reply_id,
                )

                if reply_msg is None:
                    logger.warning(
                        "Reply message %r not found in storage for session "
                        "%r; interrupt state changes from the incoming event "
                        "will not be persisted.",
                        agent.state.reply_id,
                        session_id,
                    )
                elif input_msg:
                    reply_msg.append_event(input_msg)
                    self._record_checkpoint_event(
                        checkpoint_state,
                        input_msg,
                    )
                    await self._maybe_checkpoint_reply(
                        user_id=user_id,
                        session_id=session_id,
                        agent_id=agent_id,
                        reply_msg=reply_msg,
                        agent=agent,
                        checkpoint_state=checkpoint_state,
                    )

                async for event in agent.reply_stream(inputs=input_msg):
                    entry_id = await self._message_bus.session_publish_event(
                        session_id,
                        event.model_dump(mode="json"),
                    )
                    checkpoint_state.latest_replay_entry_id = entry_id
                    if reply_msg is not None:
                        reply_msg.append_event(event)
                        self._record_checkpoint_event(
                            checkpoint_state,
                            event,
                        )
                        await self._maybe_checkpoint_reply(
                            user_id=user_id,
                            session_id=session_id,
                            agent_id=agent_id,
                            reply_msg=reply_msg,
                            agent=agent,
                            checkpoint_state=checkpoint_state,
                        )

            # Persist the reply Msg (upsert: overwrite if same id, append
            # if new).
            if reply_msg is not None:
                set_reply_checkpoint_replay_entry_id(
                    reply_msg,
                    checkpoint_state.latest_replay_entry_id,
                )
                await self._storage.upsert_message(
                    user_id,
                    session_id,
                    reply_msg,
                )

            # Persist the updated agent state. MUST happen inside the
            # session lock: if we released the lock first, another
            # process could acquire it and load a stale state from
            # storage before this write lands.
            await self._storage.update_session_state(
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
                state=agent.state,
            )

            parked_on_awaiting_tool = is_reply_awaiting_tool_interaction(
                agent,
            )

            pending_inbox_entries = await self._message_bus.inbox_length(
                session_id,
            )
            needs_followup_wakeup = (
                pending_inbox_entries > 0 and not parked_on_awaiting_tool
            )
            if needs_followup_wakeup:
                logger.info(
                    "ChatService: session %s finished with %d pending inbox "
                    "entries; scheduling a follow-up wakeup.",
                    session_id,
                    pending_inbox_entries,
                )

        # ``session_run.__aexit__`` trims the replay log before
        # releasing the lock — see :meth:`MessageBus.session_run`.
        if needs_followup_wakeup:
            await self._message_bus.enqueue_wakeup(
                user_id=user_id,
                session_id=session_id,
                agent_id=agent_id,
            )
        if should_attempt_session_title:
            self._schedule_session_title_update(
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
                current_name=initial_session_name,
                model_cfg=model_cfg,
                first_user_msg=first_user_msg,
                first_reply_msg=reply_msg,
            )
