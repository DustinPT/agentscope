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
import inspect
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import HTTPException, Request

from .._bus_ops import (
    abandon_inbox_consumer,
    enqueue_run_trigger,
    has_pending_inbox_or_release,
    publish_session_event,
    register_inbox_consumer,
)
from .._reply_state import (
    get_current_reply_msg,
    is_reply_awaiting_tool_interaction,
    set_reply_checkpoint_replay_entry_id,
)
from ..channel import ChatKind
from ..message_bus import MessageBus, MessageBusKeys
from ..storage import (
    AgentMCPAsset,
    AgentRecord,
    AgentSkillAsset,
    SessionConfig,
    SessionSource,
    StorageBase,
)
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
from ._tts_model import get_tts_model

from ..._logging import logger
from ...agent import Agent, ModelConfig
from ...event import (
    CustomEvent,
    DataBlockDeltaEvent,
    ExternalExecutionResultEvent,
    ReplyEndEvent,
    ReplyStartEvent,
    ThinkingBlockDeltaEvent,
    TextBlockDeltaEvent,
    ToolCallDeltaEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from ...formatter import FormatterBase
from ...middleware import MiddlewareBase, TTSMiddleware
from ...message import AssistantMsg, Msg, SystemMsg
from ...permission import AdditionalWorkingDirectory
from ...state import ToolRuntimeContext
from ._agent_asset_store import AgentAssetStore
from ._attachment_store import AttachmentStore


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


class _AttachmentModelCallMiddleware(MiddlewareBase):
    """Expand attachment references just before the model call."""

    def __init__(
        self,
        attachment_store: AttachmentStore,
        workspace,
    ) -> None:
        self._attachment_store = attachment_store
        self._workspace = workspace

    async def on_model_call(
        self,
        agent: Agent,
        input_kwargs: dict,
        next_handler,
    ):
        current_model = input_kwargs["current_model"]
        formatter = getattr(current_model, "formatter", None)
        if formatter is not None and not isinstance(formatter, FormatterBase):
            formatter = None
        materialized_messages = (
            await self._attachment_store.materialize_messages_for_model(
                input_kwargs["messages"],
                workspace=self._workspace,
                formatter=formatter,
            )
        )
        return await next_handler(
            current_model=current_model,
            messages=materialized_messages,
            tools=input_kwargs["tools"],
            tool_choice=input_kwargs["tool_choice"],
        )


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

    _ROLLBACK_SNAPSHOT_METADATA_KEY = "rollback_snapshot"
    """Message metadata key storing the pre-user-message rollback snapshot."""

    _PRIVATE_MESSAGE_METADATA_KEYS = frozenset(
        {_ROLLBACK_SNAPSHOT_METADATA_KEY},
    )
    """Message metadata keys that must never be exposed publicly."""

    def __init__(
        self,
        storage: StorageBase,
        workspace_manager: WorkspaceManagerBase,
        scheduler_manager: SchedulerManager,
        background_task_manager: BackgroundTaskManager,
        message_bus: MessageBus,
        chat_run_registry: ChatRunRegistry,
        *,
        agent_asset_store: AgentAssetStore | None = None,
        attachment_store: AttachmentStore | None = None,
        extra_agent_middlewares: AgentMiddlewareFactory | None = None,
        extra_agent_tools: AgentToolFactory | None = None,
        custom_subagent_templates: dict[str, SubAgentTemplate] | None = None,
        custom_agent_cls: type[Agent] | None = None,
        channel_clients=None,
    ) -> None:
        """Initialize chat service.

        Args:
            storage (`StorageBase`):
                Application storage backend.
            workspace_manager (`WorkspaceManagerBase`):
                Provides per-session workspace (tools, MCPs, skills) used
                during agent assembly.
            agent_asset_store (`AgentAssetStore | None`, optional):
                Resolves persisted managed asset paths into local absolute
                directories before the workspace consumes them. Runtime code
                passes this explicitly; tests may omit it when managed assets
                are irrelevant.
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
            channel_clients:
                Factory for unconnected channel instances. Stored for
                channel-originated session integration.
        """
        self._storage = storage
        self._workspace_manager = workspace_manager
        self._agent_asset_store = agent_asset_store
        self._attachment_store = attachment_store or AttachmentStore()
        self._scheduler_manager = scheduler_manager
        self._background_task_manager = background_task_manager
        self._message_bus = message_bus
        self._chat_run_registry = chat_run_registry
        self._extra_agent_middlewares = extra_agent_middlewares
        self._middlewares_take_workspace = False
        if extra_agent_middlewares is not None:
            try:
                inspect.signature(extra_agent_middlewares).bind(
                    "",
                    "",
                    "",
                    None,
                )
                self._middlewares_take_workspace = True
            except (TypeError, ValueError):
                pass
        self._extra_agent_tools = extra_agent_tools
        self._sub_agent_templates = custom_subagent_templates
        self._agent_cls = custom_agent_cls or Agent
        self._channel_clients = channel_clients

    def _resolve_managed_assets(
        self,
        agent_record: AgentRecord,
    ) -> tuple[list[AgentMCPAsset], list[AgentSkillAsset]]:
        """Resolve managed asset metadata to absolute local directories."""
        if self._agent_asset_store is None:
            return list(agent_record.data.mcp_assets), list(agent_record.data.skills)
        resolved_mcp_assets = [
            asset.model_copy(
                update={"dir": self._agent_asset_store.resolve_dir(asset.dir)},
            )
            for asset in agent_record.data.mcp_assets
        ]
        resolved_skills = [
            skill.model_copy(
                update={"dir": self._agent_asset_store.resolve_dir(skill.dir)},
            )
            for skill in agent_record.data.skills
        ]
        return resolved_mcp_assets, resolved_skills

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

    @classmethod
    def _build_rollback_snapshot(cls, state) -> dict[str, Any]:
        """Build a JSON-safe rollback snapshot from the current agent state."""
        state_dump = state.model_dump(mode="json")
        return {
            "summary": deepcopy(state_dump.get("summary", "")),
            "context": deepcopy(state_dump.get("context", [])),
            "tasks_context": deepcopy(state_dump.get("tasks_context", {})),
            "tool_context": deepcopy(state_dump.get("tool_context", {})),
            "permission_context": deepcopy(
                state_dump.get("permission_context", {}),
            ),
        }

    @classmethod
    def attach_rollback_snapshot(cls, msg: Msg, state) -> Msg:
        """Attach a pre-send rollback snapshot to a user message."""
        if msg.role != "user":
            return msg
        metadata = dict(msg.metadata or {})
        metadata[cls._ROLLBACK_SNAPSHOT_METADATA_KEY] = (
            cls._build_rollback_snapshot(state)
        )
        return msg.model_copy(update={"metadata": metadata}, deep=True)

    @classmethod
    def sanitize_public_message(cls, message: Msg) -> Msg:
        """Strip private message metadata before returning it publicly."""
        metadata = dict(message.metadata or {})
        changed = False
        for key in cls._PRIVATE_MESSAGE_METADATA_KEYS:
            if key in metadata:
                metadata.pop(key, None)
                changed = True
        if not changed:
            return message
        return message.model_copy(update={"metadata": metadata}, deep=True)

    @classmethod
    def sanitize_public_messages(cls, messages: list[Msg]) -> list[Msg]:
        """Strip private metadata from a list of messages."""
        return [cls.sanitize_public_message(message) for message in messages]

    async def _dehydrate_message_for_storage(
        self,
        message: Msg,
        *,
        workspace,
        session_id: str,
    ) -> Msg:
        """Persist message attachments to workspace and store references."""
        return await self._attachment_store.persist_message_attachments(
            message,
            workspace=workspace,
            session_id=session_id,
        )

    async def _dehydrate_messages_for_storage(
        self,
        messages: list[Msg],
        *,
        workspace,
        session_id: str,
    ) -> list[Msg]:
        """Persist attachments for multiple messages."""
        return [
            await self._dehydrate_message_for_storage(
                message,
                workspace=workspace,
                session_id=session_id,
            )
            for message in messages
        ]

    async def _build_state_for_storage(
        self,
        *,
        state,
        workspace,
        session_id: str,
    ):
        """Create a storage-safe state snapshot with dehydrated context."""
        state_copy = state.model_copy(deep=True)
        if state_copy.context:
            state_copy.context = await self._dehydrate_messages_for_storage(
                list(state_copy.context),
                workspace=workspace,
                session_id=session_id,
            )
        return state_copy

    def _hydrate_message_for_public(
        self,
        message: Msg,
        *,
        request: Request | None = None,
        user_id: str | None = None,
    ) -> Msg:
        """Expose attachment references as public download URLs."""
        hydrated = self._attachment_store.hydrate_message_for_public(
            message,
            request=request,
            user_id=user_id,
        )
        return self.sanitize_public_message(hydrated)

    def hydrate_public_messages(
        self,
        messages: list[Msg],
        *,
        request: Request | None = None,
        user_id: str | None = None,
    ) -> list[Msg]:
        """Expose a message list with private metadata stripped."""
        return [
            self._hydrate_message_for_public(
                message,
                request=request,
                user_id=user_id,
            )
            for message in messages
        ]

    @classmethod
    def sanitize_public_message_payload(
        cls,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        """Strip private metadata keys from a serialized message payload."""
        sanitized = dict(message)
        metadata = sanitized.get("metadata")
        if isinstance(metadata, dict):
            sanitized["metadata"] = {
                key: value
                for key, value in metadata.items()
                if key not in cls._PRIVATE_MESSAGE_METADATA_KEYS
            }
        return sanitized

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

    @staticmethod
    def _stringify_error_detail(detail: Any) -> str:
        """Convert an exception detail payload to a readable string."""
        if detail is None:
            return ""
        if isinstance(detail, str):
            return detail.strip()
        return str(detail).strip()

    @classmethod
    def _format_exception_detail(cls, exc: Exception) -> str:
        """Flatten an exception chain into a compact human-readable detail."""
        parts: list[str] = []
        seen: set[int] = set()
        current: BaseException | None = exc

        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, HTTPException):
                message = cls._stringify_error_detail(current.detail)
            else:
                message = str(current).strip()
            if not message:
                message = current.__class__.__name__
            parts.append(f"{current.__class__.__name__}: {message}")
            current = current.__cause__ or current.__context__

        return "\nCaused by: ".join(parts)

    @staticmethod
    def _extract_status_code(exc: Exception) -> int | None:
        """Return an HTTP-style status code when one is available."""
        if isinstance(exc, HTTPException):
            return exc.status_code

        status_code = getattr(exc, "status_code", None)
        return status_code if isinstance(status_code, int) else None

    def _build_reply_failure_metadata(
        self,
        exc: Exception,
        failed_at: str,
    ) -> dict[str, Any]:
        """Build structured reply-error metadata from a runtime exception."""
        status_code = self._extract_status_code(exc)
        detail = self._format_exception_detail(exc)
        lowered = detail.lower()

        kind = "unknown"
        summary = "Reply generation failed."
        retryable = False

        if status_code == 429 or (
            "provider_rate_limit_exceeded" in lowered
            or "rate limit" in lowered
        ):
            kind = "rate_limit"
            summary = "Model service is rate limited."
            retryable = True
        elif status_code == 401 or (
            "invalid_api_key" in lowered
            or "incorrect api key" in lowered
            or "authenticationerror" in lowered
            or "authentication error" in lowered
            or "unauthorized" in lowered
        ):
            kind = "auth"
            summary = "Model authentication failed."
        elif isinstance(exc, (TimeoutError, asyncio.TimeoutError)) or (
            "timed out" in lowered or "timeout" in lowered
        ):
            kind = "timeout"
            summary = "Model request timed out."
            retryable = True
        elif "connection error" in lowered or "api connection" in lowered:
            kind = "connection"
            summary = "Model service connection failed."
            retryable = True
        elif status_code is not None:
            kind = "http"
            summary = "Model request failed."

        return {
            "terminal_state": "failed",
            "run_failed_at": failed_at,
            "run_error": {
                "kind": kind,
                "summary": summary,
                "detail": detail,
                "retryable": retryable,
                "status_code": status_code,
            },
        }

    def _build_failed_reply_end_event(
        self,
        session_id: str,
        reply_id: str,
        exc: Exception,
    ) -> ReplyEndEvent:
        """Build a terminal ``ReplyEndEvent`` that marks the reply as failed."""
        failed_at = datetime.now().isoformat()
        return ReplyEndEvent(
            session_id=session_id,
            reply_id=reply_id,
            created_at=failed_at,
            metadata=self._build_reply_failure_metadata(exc, failed_at),
        )

    @staticmethod
    def _build_reply_interrupted_metadata(
        interrupted_at: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Build terminal metadata for a user-interrupted reply."""
        metadata: dict[str, Any] = {
            "terminal_state": "interrupted",
            "run_interrupted_at": interrupted_at,
        }
        if reason:
            metadata["interrupt_reason"] = reason
        return metadata

    def _build_interrupted_reply_end_event(
        self,
        session_id: str,
        reply_id: str,
        reason: str | None = None,
    ) -> ReplyEndEvent:
        """Build a terminal ``ReplyEndEvent`` that marks the reply interrupted."""
        interrupted_at = datetime.now().isoformat()
        return ReplyEndEvent(
            session_id=session_id,
            reply_id=reply_id,
            created_at=interrupted_at,
            metadata=self._build_reply_interrupted_metadata(
                interrupted_at,
                reason=reason,
            ),
        )

    @staticmethod
    def _remove_empty_terminal_reply_from_context(
        agent: Agent,
        reply_id: str,
    ) -> None:
        """Drop an empty terminal reply from the session context tail."""
        current_reply = get_current_reply_msg(agent)
        if current_reply is None or current_reply.id != reply_id:
            return
        if current_reply.content:
            return
        if is_reply_awaiting_tool_interaction(agent):
            return
        agent.state.context.pop()

    async def _finalize_failed_reply(
        self,
        *,
        user_id: str,
        session_id: str,
        agent_id: str,
        agent: Agent,
        workspace,
        reply_msg: Msg | None,
        reply_started: bool,
        checkpoint_state: _ReplyCheckpointState,
        exc: Exception,
    ) -> Msg | None:
        """Best-effort finalize and persist the current reply as failed."""
        reply_id = reply_msg.id if reply_msg is not None else agent.state.reply_id
        current_reply = get_current_reply_msg(agent)
        if current_reply is not None and current_reply.id != reply_id:
            current_reply = None

        target_reply = reply_msg
        if target_reply is None and current_reply is not None:
            target_reply = current_reply.model_copy(deep=True)

        if target_reply is None and not reply_started:
            return None

        if target_reply is None:
            target_reply = AssistantMsg(
                id=reply_id,
                name=agent.name,
                content=[],
            )

        if target_reply.finished_at is not None:
            return target_reply

        failed_event = self._build_failed_reply_end_event(
            session_id=session_id,
            reply_id=reply_id,
            exc=exc,
        )

        try:
            entry_id = await publish_session_event(
                self._message_bus,
                session_id,
                failed_event.model_dump(mode="json"),
            )
            checkpoint_state.latest_replay_entry_id = entry_id
        except Exception:
            logger.warning(
                "Failed to publish failed ReplyEndEvent for session %s reply %s.",
                session_id,
                reply_id,
                exc_info=True,
            )

        target_reply.append_event(failed_event)
        if current_reply is not None and current_reply is not target_reply:
            current_reply.append_event(failed_event)

        set_reply_checkpoint_replay_entry_id(
            target_reply,
            checkpoint_state.latest_replay_entry_id,
        )
        await self._storage.upsert_message(
            user_id,
            session_id,
            await self._dehydrate_message_for_storage(
                target_reply,
                workspace=workspace,
                session_id=session_id,
            ),
        )

        self._remove_empty_terminal_reply_from_context(agent, reply_id)
        await self._storage.update_session_state(
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            state=await self._build_state_for_storage(
                state=agent.state,
                workspace=workspace,
                session_id=session_id,
            ),
        )
        return target_reply

    async def _finalize_interrupted_reply(
        self,
        *,
        user_id: str,
        session_id: str,
        agent_id: str,
        agent: Agent,
        workspace,
        reply_msg: Msg | None,
        reply_started: bool,
        checkpoint_state: _ReplyCheckpointState,
        reason: str | None = None,
    ) -> Msg | None:
        """Best-effort finalize and persist the current reply as interrupted."""
        reply_id = reply_msg.id if reply_msg is not None else agent.state.reply_id
        current_reply = get_current_reply_msg(agent)
        if current_reply is not None and current_reply.id != reply_id:
            current_reply = None

        target_reply = reply_msg
        if target_reply is None and current_reply is not None:
            target_reply = current_reply.model_copy(deep=True)

        if target_reply is None and not reply_started:
            return None

        if target_reply is None:
            target_reply = AssistantMsg(
                id=reply_id,
                name=agent.name,
                content=[],
            )

        if target_reply.finished_at is not None:
            return target_reply

        interrupted_event = self._build_interrupted_reply_end_event(
            session_id=session_id,
            reply_id=reply_id,
            reason=reason,
        )

        try:
            entry_id = await publish_session_event(
                self._message_bus,
                session_id,
                interrupted_event.model_dump(mode="json"),
            )
            checkpoint_state.latest_replay_entry_id = entry_id
        except Exception:
            logger.warning(
                "Failed to publish interrupted ReplyEndEvent for session %s reply %s.",
                session_id,
                reply_id,
                exc_info=True,
            )

        target_reply.append_event(interrupted_event)
        if current_reply is not None and current_reply is not target_reply:
            current_reply.append_event(interrupted_event)

        set_reply_checkpoint_replay_entry_id(
            target_reply,
            checkpoint_state.latest_replay_entry_id,
        )
        await self._storage.upsert_message(
            user_id,
            session_id,
            await self._dehydrate_message_for_storage(
                target_reply,
                workspace=workspace,
                session_id=session_id,
            ),
        )

        self._remove_empty_terminal_reply_from_context(agent, reply_id)
        await self._storage.update_session_state(
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            state=await self._build_state_for_storage(
                state=agent.state,
                workspace=workspace,
                session_id=session_id,
            ),
        )
        return target_reply

    async def _checkpoint_reply(
        self,
        *,
        user_id: str,
        session_id: str,
        agent_id: str,
        workspace,
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
            await self._dehydrate_message_for_storage(
                reply_msg,
                workspace=workspace,
                session_id=session_id,
            ),
        )
        await self._storage.update_session_state(
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            state=await self._build_state_for_storage(
                state=agent.state,
                workspace=workspace,
                session_id=session_id,
            ),
        )

    async def _maybe_checkpoint_reply(
        self,
        *,
        user_id: str,
        session_id: str,
        agent_id: str,
        workspace,
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
            workspace=workspace,
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
        | UserInterruptEvent
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

        latest_session = await self._storage.get_session_meta(
            user_id,
            session_id,
        )
        if latest_session is not None and latest_session.agent_id != agent_id:
            latest_session = None
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

        latest_session = await self._storage.get_session_meta(
            user_id,
            session_id,
        )
        if latest_session is not None and latest_session.agent_id != agent_id:
            latest_session = None
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
            session_id=session_id,
        )

        event = CustomEvent(name="session_updated", value={"name": title})
        await publish_session_event(
            self._message_bus,
            session_id,
            event.model_dump(mode="json"),
        )

    async def interrupt(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
    ) -> None:
        """Interrupt an in-progress or parked reply for one session."""
        session = await self._storage.get_session(
            user_id,
            agent_id,
            session_id,
        )
        if session is None:
            raise LookupError(f"Session '{session_id}' not found.")

        if await self._message_bus.is_locked(
            MessageBusKeys.session_lock(session_id),
        ):
            await self._message_bus.publish(
                MessageBusKeys.session_interrupt_channel(),
                {"session_id": session_id},
            )
            return

        await enqueue_run_trigger(
            self._message_bus,
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
            kind=MessageBusKeys.WAKEUP_KIND_RESUME,
            inputs=UserInterruptEvent(
                reply_id=session.state.reply_id,
            ),
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

    async def _list_all_messages(
        self,
        *,
        user_id: str,
        session_id: str,
        batch_size: int = 200,
    ) -> list[Msg]:
        """Fetch all persisted messages for a session."""
        messages: list[Msg] = []
        offset = 0
        while True:
            batch = await self._storage.list_messages(
                user_id,
                session_id,
                offset=offset,
                limit=batch_size,
            )
            if not batch:
                break
            messages.extend(batch)
            if len(batch) < batch_size:
                break
            offset += len(batch)
        return messages

    @staticmethod
    def _truncate_export_text(
        value: str,
        max_length: int,
    ) -> tuple[str, bool]:
        """Truncate text for export when it exceeds ``max_length``."""
        if len(value) <= max_length:
            return value, False
        return value[:max_length], True

    def _sanitize_export_block(
        self,
        block: dict[str, Any],
        *,
        truncate_tool_call_input: bool,
        tool_call_input_max_length: int,
        truncate_tool_result: bool,
        tool_result_max_length: int,
    ) -> dict[str, Any]:
        """Apply export-time truncation to a single content block."""
        result = dict(block)
        block_type = result.get("type")

        if (
            block_type == "tool_call"
            and truncate_tool_call_input
            and isinstance(result.get("input"), str)
        ):
            original = result["input"]
            truncated, changed = self._truncate_export_text(
                original,
                tool_call_input_max_length,
            )
            if changed:
                result["input"] = truncated
                result["input_truncated"] = True
                result["input_original_length"] = len(original)

        if block_type == "tool_result" and truncate_tool_result:
            output = result.get("output")
            if isinstance(output, str):
                truncated, changed = self._truncate_export_text(
                    output,
                    tool_result_max_length,
                )
                if changed:
                    result["output"] = truncated
                    result["output_truncated"] = True
                    result["output_original_length"] = len(output)
            elif isinstance(output, list):
                sanitized_output: list[Any] = []
                for item in output:
                    if not isinstance(item, dict):
                        sanitized_output.append(item)
                        continue
                    sanitized_item = dict(item)
                    if (
                        sanitized_item.get("type") == "text"
                        and isinstance(sanitized_item.get("text"), str)
                    ):
                        original = sanitized_item["text"]
                        truncated, changed = self._truncate_export_text(
                            original,
                            tool_result_max_length,
                        )
                        if changed:
                            sanitized_item["text"] = truncated
                            sanitized_item["text_truncated"] = True
                            sanitized_item["text_original_length"] = len(
                                original,
                            )
                    sanitized_output.append(sanitized_item)
                result["output"] = sanitized_output

        return result

    def _sanitize_export_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        include_system_messages: bool,
        truncate_tool_call_input: bool,
        tool_call_input_max_length: int,
        truncate_tool_result: bool,
        tool_result_max_length: int,
    ) -> list[dict[str, Any]]:
        """Filter and sanitize messages for export."""
        sanitized_messages: list[dict[str, Any]] = []
        for message in messages:
            if (
                not include_system_messages
                and message.get("role") == "system"
            ):
                continue
            sanitized_payload = self.sanitize_public_message_payload(message)
            sanitized_messages.append(
                {
                    **sanitized_payload,
                    "content": [
                        self._sanitize_export_block(
                            block,
                            truncate_tool_call_input=truncate_tool_call_input,
                            tool_call_input_max_length=tool_call_input_max_length,
                            truncate_tool_result=truncate_tool_result,
                            tool_result_max_length=tool_result_max_length,
                        )
                        if isinstance(block, dict)
                        else block
                        for block in message.get("content", [])
                    ],
                },
            )
        return sanitized_messages

    @staticmethod
    def _build_export_session_info(
        *,
        agent_record,
        session_record,
    ) -> dict[str, Any]:
        """Build the export payload's session metadata."""
        return {
            "agent_id": agent_record.id,
            "agent_name": agent_record.data.name,
            "session_id": session_record.id,
            "session_name": session_record.config.name,
            "source": session_record.source.value,
            "workspace_id": session_record.config.workspace_id,
            "created_at": session_record.created_at.isoformat(),
            "updated_at": session_record.updated_at.isoformat(),
        }

    @staticmethod
    def _refresh_tool_runtime_context(
        *,
        state,
        session_id: str,
        workspace_id: str,
        workdir: str,
    ) -> None:
        """Refresh per-run tool runtime context on the live agent state."""
        state.session_id = session_id
        state.tool_context.runtime_context = ToolRuntimeContext(
            session_id=session_id,
            workspace_id=workspace_id,
            workdir=workdir,
        )

    @staticmethod
    def _build_fallback_export_system_message(
        *,
        system_prompt: str,
        exported_at: str,
        reason: str,
    ) -> dict[str, Any]:
        """Build a fallback system message when full reconstruction fails."""
        return SystemMsg(
            name="system",
            content=system_prompt,
            created_at=exported_at,
            finished_at=exported_at,
            metadata={
                "export_source": "reconstructed",
                "reconstruction_mode": "fallback",
                "fallback_reason": reason,
            },
        ).model_dump(mode="json")

    async def _build_export_runtime_context(
        self,
        *,
        user_id: str,
        session_id: str,
        agent_record,
        session_record,
    ) -> dict[str, Any]:
        """Assemble reusable runtime components for export helpers."""
        resolved_mcp_assets, resolved_skills = self._resolve_managed_assets(
            agent_record,
        )
        workspace = await self._workspace_manager.get_workspace(
            user_id,
            agent_record.id,
            session_id,
            session_record.config.workspace_id,
            agent_mcps=agent_record.data.mcps,
            agent_mcp_assets=resolved_mcp_assets,
            agent_skill_assets=resolved_skills,
        )

        session_state = deepcopy(session_record.state)
        if (
            workspace.workdir
            not in session_state.permission_context.working_directories
        ):
            session_state.permission_context.working_directories[
                workspace.workdir
            ] = AdditionalWorkingDirectory(
                path=workspace.workdir,
                source="session",
            )
        self._refresh_tool_runtime_context(
            state=session_state,
            session_id=session_id,
            workspace_id=session_record.config.workspace_id,
            workdir=workspace.workdir,
        )

        runtime_session = session_record.model_copy(deep=True)
        runtime_session.state = session_state

        toolkit = await get_toolkit(
            storage=self._storage,
            workspace=workspace,
            scheduler_manager=self._scheduler_manager,
            background_task_manager=self._background_task_manager,
            message_bus=self._message_bus,
            chat_service=self,
            chat_run_registry=self._chat_run_registry,
            user_id=user_id,
            agent_record=agent_record,
            session_record=runtime_session,
            agent_asset_store=self._agent_asset_store,
            extra_factory=self._extra_agent_tools,
            sub_agent_templates=self._sub_agent_templates,
        )

        middlewares = []
        if runtime_session.parent_session_id is not None:
            middlewares.append(
                SubAgentMiddleware(
                    storage=self._storage,
                    message_bus=self._message_bus,
                    user_id=user_id,
                    agent_id=agent_record.id,
                    session_id=session_id,
                ),
            )
        if self._extra_agent_middlewares is not None:
            factory_args: tuple = (
                user_id,
                agent_record.id,
                session_id,
            )
            if self._middlewares_take_workspace:
                factory_args += (workspace,)
            middlewares.extend(
                await self._extra_agent_middlewares(*factory_args),
            )

        return {
            "workspace": workspace,
            "session_state": session_state,
            "runtime_session": runtime_session,
            "toolkit": toolkit,
            "middlewares": middlewares,
        }

    async def _build_export_system_message(
        self,
        *,
        user_id: str,
        session_id: str,
        agent_record,
        session_record,
        exported_at: str,
    ) -> dict[str, Any]:
        """Reconstruct the current system message for export."""
        base_system_prompt = agent_record.data.system_prompt
        try:
            runtime_context = await self._build_export_runtime_context(
                user_id=user_id,
                session_id=session_id,
                agent_record=agent_record,
                session_record=session_record,
            )
            runtime_session = runtime_context["runtime_session"]

            model_cfg = (
                runtime_session.config.chat_model_config
                or runtime_session.config.fallback_chat_model_config
            )
            if model_cfg is None:
                return self._build_fallback_export_system_message(
                    system_prompt=base_system_prompt,
                    exported_at=exported_at,
                    reason="missing_model_config",
                )

            model = await get_model(user_id, model_cfg, self._storage)
            agent = self._agent_cls(
                name=agent_record.data.name,
                system_prompt=base_system_prompt,
                model=model,
                toolkit=runtime_context["toolkit"],
                state=runtime_context["session_state"],
                middlewares=runtime_context["middlewares"],
                offloader=runtime_context["workspace"],
                context_config=agent_record.data.context_config,
                react_config=agent_record.data.react_config,
            )
            reconstructed_prompt = await agent._get_system_prompt()
            return SystemMsg(
                name="system",
                content=reconstructed_prompt,
                created_at=exported_at,
                finished_at=exported_at,
                metadata={
                    "export_source": "reconstructed",
                    "reconstruction_mode": "full",
                },
            ).model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to fully reconstruct system message for session %s: %s",
                session_id,
                exc,
            )
            return self._build_fallback_export_system_message(
                system_prompt=base_system_prompt,
                exported_at=exported_at,
                reason="reconstruction_failed",
            )

    async def _build_export_tool_schemas(
        self,
        *,
        user_id: str,
        session_id: str,
        agent_record,
        session_record,
    ) -> list[dict[str, Any]]:
        """Resolve the currently active tool schemas for export."""
        try:
            runtime_context = await self._build_export_runtime_context(
                user_id=user_id,
                session_id=session_id,
                agent_record=agent_record,
                session_record=session_record,
            )
            return await runtime_context["toolkit"].get_tool_schemas(
                runtime_context["runtime_session"].state.tool_context.activated_groups,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to build tool schemas for session %s export: %s",
                session_id,
                exc,
            )
            return []

    async def build_session_export_payload(
        self,
        *,
        request: Request | None = None,
        user_id: str,
        agent_id: str,
        session_id: str,
        include_system_messages: bool = False,
        include_tool_schemas: bool = False,
        truncate_tool_call_input: bool = False,
        tool_call_input_max_length: int = 200,
        truncate_tool_result: bool = False,
        tool_result_max_length: int = 200,
    ) -> dict[str, Any]:
        """Build the final JSON payload for session export."""
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

        exported_at = datetime.now().isoformat()
        messages = await self._list_all_messages(
            user_id=user_id,
            session_id=session_id,
        )
        export_messages = [
            self._hydrate_message_for_public(
                message,
                request=request,
                user_id=user_id,
            ).model_dump(mode="json")
            for message in messages
        ]

        if include_system_messages:
            export_messages = [
                await self._build_export_system_message(
                    user_id=user_id,
                    session_id=session_id,
                    agent_record=agent_record,
                    session_record=session_record,
                    exported_at=exported_at,
                ),
                *export_messages,
            ]

        tool_schemas: list[dict[str, Any]] = []
        if include_tool_schemas:
            tool_schemas = await self._build_export_tool_schemas(
                user_id=user_id,
                session_id=session_id,
                agent_record=agent_record,
                session_record=session_record,
            )

        return {
            "version": 1,
            "exported_at": exported_at,
            "session": self._build_export_session_info(
                agent_record=agent_record,
                session_record=session_record,
            ),
            "export_options": {
                "include_system_messages": include_system_messages,
                "include_tool_schemas": include_tool_schemas,
                "truncate_tool_call_input": truncate_tool_call_input,
                "tool_call_input_max_length": tool_call_input_max_length,
                "truncate_tool_result": truncate_tool_result,
                "tool_result_max_length": tool_result_max_length,
            },
            "tool_schemas": tool_schemas,
            "messages": self._sanitize_export_messages(
                export_messages,
                include_system_messages=include_system_messages,
                truncate_tool_call_input=truncate_tool_call_input,
                tool_call_input_max_length=tool_call_input_max_length,
                truncate_tool_result=truncate_tool_result,
                tool_result_max_length=tool_result_max_length,
            ),
        }

    async def run(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        input_msg: Msg
        | list[Msg]
        | UserConfirmResultEvent
        | ExternalExecutionResultEvent
        | UserInterruptEvent
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
                  - ``UserInterruptEvent``: interrupt an awaiting tool
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
        | UserInterruptEvent
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
        resolved_mcp_assets, resolved_skills = self._resolve_managed_assets(
            agent_record,
        )
        workspace = await self._workspace_manager.get_workspace(
            user_id,
            agent_id,
            session_id,
            session_record.config.workspace_id,
            agent_mcps=agent_record.data.mcps,
            agent_mcp_assets=resolved_mcp_assets,
            agent_skill_assets=resolved_skills,
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
        self._refresh_tool_runtime_context(
            state=session_record.state,
            session_id=session_id,
            workspace_id=session_record.config.workspace_id,
            workdir=workspace.workdir,
        )

        channel = (
            await self._channel_clients.get(
                session_record.source_channel_id,
            )
            if session_record.source_channel_id
            and self._channel_clients is not None
            else None
        )
        channel_tools = (
            await channel.list_tools(workspace)
            if channel is not None
            else []
        )

        # ----------------------------------------------------------------
        # 2. Middlewares — framework-supplied first, then caller extras.
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
            _AttachmentModelCallMiddleware(
                attachment_store=self._attachment_store,
                workspace=workspace,
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
        tts_cfg = session_record.config.tts_model_config
        if tts_cfg is not None:
            tts_model = await get_tts_model(
                user_id,
                tts_cfg,
                self._storage,
            )
            middlewares.append(TTSMiddleware(tts_model))
        if self._extra_agent_middlewares is not None:
            factory_args: tuple = (user_id, agent_id, session_id)
            if self._middlewares_take_workspace:
                factory_args += (workspace,)
            middlewares.extend(
                await self._extra_agent_middlewares(*factory_args),
            )
        
        # ----------------------------------------------------------------
        # 3. Toolkit (workspace tools + planning + TaskStop + schedule +
        # team + extras + skills + mcps).
        # ----------------------------------------------------------------
        toolkit = await get_toolkit(
            storage=self._storage,
            workspace=workspace,
            scheduler_manager=self._scheduler_manager,
            background_task_manager=self._background_task_manager,
            message_bus=self._message_bus,
            middlewares=middlewares,
            chat_service=self,
            chat_run_registry=self._chat_run_registry,
            user_id=user_id,
            agent_record=agent_record,
            session_record=session_record,
            agent_asset_store=self._agent_asset_store,
            extra_factory=self._extra_agent_tools,
            sub_agent_templates=self._sub_agent_templates,
            channel_tools=channel_tools,
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
        system_prompt = agent_record.data.system_prompt
        attachment = f"You're within a session (id={session_id})."
        if channel is not None:
            tools = ", ".join(tool.name for tool in channel_tools)
            chat_id = session_record.source_chat_id or ""
            kind = (
                ChatKind(session_record.conversation_kind)
                if session_record.conversation_kind is not None
                else await channel.chat_kind(chat_id)
            )
            name = (
                session_record.source_chat_name
                or await channel.chat_name(chat_id)
            )
            where = f' named "{name}"' if name else ""
            attachment += (
                f" This session is bound to a chat{where} (id "
                f"{chat_id!r}) on the {channel.display_name} platform: "
                "the messages, images and files people send there are "
                "relayed to you here, and your replies are delivered "
                "back to that same chat."
            )
            if kind is ChatKind.GROUP:
                attachment += (
                    " It is a group chat, so messages may come from "
                    "several different people; each incoming user turn "
                    "is labelled with its sender."
                )
            elif kind is ChatKind.PRIVATE:
                attachment += (
                    " It is a one-to-one private chat with a single user."
                )
                target_user_id = session_record.source_chat_user_id
                target_user_name = session_record.source_chat_user_name
                if target_user_id:
                    attachment += (
                        f" The target user's id is {target_user_id!r}."
                    )
                    if target_user_name:
                        attachment += (
                            f' The target user\'s name is "{target_user_name}".'
                        )
            if tools:
                attachment += (
                    f" You also have these {channel.display_name} tools "
                    f"available: {tools}. Pass this chat's id as their "
                    "target to act on this chat."
                )
        system_prompt = (
            f"{system_prompt}\n\n"
            f"<system-notification>{attachment}</system-notification>"
        )

        agent_state = session_record.state
        agent_state.session_id = session_id
        agent_state.conversation_kind = session_record.conversation_kind
        agent = self._agent_cls(
            name=agent_record.data.name,
            system_prompt=system_prompt,
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
        released_inbox_consumer = False
        reply_msg: Msg | None = None
        runtime_input_msg = input_msg
        async with self._message_bus.session_run(session_id):
            if (
                session_record.source == SessionSource.CHANNEL
                and session_record.source_channel_id
                and session_record.source_chat_id
                and self._channel_clients is not None
            ):
                await self._channel_clients.deliver(
                    session_id=session_id,
                    channel_id=session_record.source_channel_id,
                    chat_id=session_record.source_chat_id,
                    agent_id=agent_id,
                )
            await register_inbox_consumer(self._message_bus, session_id)
            checkpoint_state = _ReplyCheckpointState()
            reply_started = False

            try:
                if input_msg is None or isinstance(input_msg, (Msg, list)):
                    # Case A: new reply (user message(s), or retrigger with
                    # empty input)
                    if isinstance(input_msg, (Msg, list)):
                        input_msgs = (
                            [input_msg]
                            if isinstance(input_msg, Msg)
                            else input_msg
                        )
                        persisted_input_msgs: list[Msg] = []
                        for msg in input_msgs:
                            stored_msg = await self._dehydrate_message_for_storage(
                                self.attach_rollback_snapshot(
                                    msg,
                                    agent.state,
                                ),
                                workspace=workspace,
                                session_id=session_id,
                            )
                            persisted_input_msgs.append(stored_msg)
                            await self._storage.upsert_message(
                                user_id,
                                session_id,
                                stored_msg,
                            )
                        runtime_input_msg = (
                            persisted_input_msgs[0]
                            if isinstance(input_msg, Msg)
                            else persisted_input_msgs
                        )

                    async for event in agent.reply_stream(
                        inputs=runtime_input_msg,
                    ):
                        entry_id = await publish_session_event(
                            self._message_bus,
                            session_id,
                            event.model_dump(mode="json"),
                        )
                        checkpoint_state.latest_replay_entry_id = entry_id
                        if isinstance(event, ReplyStartEvent):
                            reply_started = True
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
                                workspace=workspace,
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
                            workspace=workspace,
                            reply_msg=reply_msg,
                            agent=agent,
                            checkpoint_state=checkpoint_state,
                        )

                    async for event in agent.reply_stream(inputs=input_msg):
                        entry_id = await publish_session_event(
                            self._message_bus,
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
                                workspace=workspace,
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
                            workspace=workspace,
                            reply_msg=reply_msg,
                            agent=agent,
                            checkpoint_state=checkpoint_state,
                        )

                    async for event in agent.reply_stream(inputs=input_msg):
                        entry_id = await publish_session_event(
                            self._message_bus,
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
                                workspace=workspace,
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
                        await self._dehydrate_message_for_storage(
                            reply_msg,
                            workspace=workspace,
                            session_id=session_id,
                        ),
                    )

                # Persist the updated agent state. MUST happen inside the
                # session lock: if we released the lock first, another
                # process could acquire it and load a stale state from
                # storage before this write lands.
                await self._storage.update_session_state(
                    user_id=user_id,
                    agent_id=agent_id,
                    session_id=session_id,
                    state=await self._build_state_for_storage(
                        state=agent.state,
                        workspace=workspace,
                        session_id=session_id,
                    ),
                )

                parked_on_awaiting_tool = is_reply_awaiting_tool_interaction(
                    agent,
                )

                if parked_on_awaiting_tool:
                    async with self._message_bus.acquire_lock(
                        MessageBusKeys.inbox_lock(session_id),
                        ttl_secs=MessageBusKeys.INBOX_LOCK_TTL_SECS,
                    ):
                        await self._message_bus.registry_del(
                            MessageBusKeys.inbox_consumer(session_id),
                            MessageBusKeys.INBOX_CONSUMER_FIELD,
                        )
                    released_inbox_consumer = True
                else:
                    needs_followup_wakeup = (
                        await has_pending_inbox_or_release(
                            self._message_bus,
                            session_id,
                        )
                    )
                    released_inbox_consumer = not needs_followup_wakeup
                    if needs_followup_wakeup:
                        logger.info(
                            "ChatService: session %s finished with pending inbox "
                            "entries; scheduling a follow-up wakeup.",
                            session_id,
                        )
            except asyncio.CancelledError:
                try:
                    reply_msg = await asyncio.shield(
                        self._finalize_interrupted_reply(
                            user_id=user_id,
                            session_id=session_id,
                            agent_id=agent_id,
                            agent=agent,
                            workspace=workspace,
                            reply_msg=reply_msg,
                            reply_started=reply_started,
                            checkpoint_state=checkpoint_state,
                            reason="Session cancelled before reply completed.",
                        ),
                    )
                except Exception:
                    logger.warning(
                        "Failed to finalize interrupted reply for session %s.",
                        session_id,
                        exc_info=True,
                    )
                raise
            except Exception as exc:
                reply_msg = await self._finalize_failed_reply(
                    user_id=user_id,
                    session_id=session_id,
                    agent_id=agent_id,
                    agent=agent,
                    workspace=workspace,
                    reply_msg=reply_msg,
                    reply_started=reply_started,
                    checkpoint_state=checkpoint_state,
                    exc=exc,
                )
                raise
            finally:
                if not released_inbox_consumer:
                    await abandon_inbox_consumer(
                        self._message_bus,
                        user_id=user_id,
                        session_id=session_id,
                        agent_id=agent_id,
                    )

        # ``session_run.__aexit__`` trims the replay log before
        # releasing the lock — see :meth:`MessageBus.session_run`.
        if needs_followup_wakeup:
            await enqueue_run_trigger(
                self._message_bus,
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
