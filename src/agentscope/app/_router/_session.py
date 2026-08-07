# -*- coding: utf-8 -*-
"""Session router — create, list, update, delete, stream, and get messages."""
import asyncio
import json
import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from .._reply_state import get_reply_checkpoint_replay_entry_id
from ..deps import (
    get_chat_service,
    get_current_user_id,
    get_message_bus,
    get_session_service,
    get_storage,
)
from ._schema import (
    CancelSessionResponse,
    CreateSessionRequest,
    CreateSessionResponse,
    ListMessagesResponse,
    ListSessionsResponse,
    RollbackSessionRequest,
    RollbackSessionResponse,
    SessionExportResponse,
    SessionView,
    SubAgentSessionView,
    TeamDetailResponse,
    TeamMemberView,
    UpdateSessionRequest,
)
from ..message_bus import MessageBus
from .._service import ChatService, SessionService
from ..storage import (
    AgentRecord,
    ChatModelConfig,
    SessionConfig,
    SessionRecord,
    StorageBase,
    TeamRecord,
)
from ...permission import PermissionContext
from ...state import AgentState


async def _build_team_detail(
    storage: StorageBase,
    user_id: str,
    team: TeamRecord,
) -> TeamDetailResponse:
    """Resolve a team's leader agent + member agents into a
    :class:`TeamDetailResponse` for the session list endpoint.

    Args:
        storage (`StorageBase`):
            Application storage. Used to look up the leader session,
            each member agent, and each member's session.
        user_id (`str`):
            The owner user id.
        team (`TeamRecord`):
            The team to resolve. Caller has already loaded it.

    Returns:
        `TeamDetailResponse`:
            The team plus its resolved leader and member agents (each
            member paired with its session id when available).
    """
    leader_agent: AgentRecord | None = None
    leader_session = await storage.get_session(user_id, "", team.session_id)
    if leader_session is not None:
        leader_agent = await storage.get_agent(
            user_id,
            leader_session.agent_id,
        )

    members: list[TeamMemberView] = []
    for member_id in team.data.member_ids:
        agent = await storage.get_agent(user_id, member_id)
        if agent is None:
            continue
        sessions = await storage.list_sessions(user_id, member_id)
        session_id = sessions[0].id if sessions else None
        members.append(TeamMemberView(agent=agent, session_id=session_id))

    return TeamDetailResponse(
        team=team,
        leader_agent=leader_agent,
        members=members,
    )


async def _build_child_sessions(
    storage: StorageBase,
    message_bus: MessageBus,
    user_id: str,
    parent_session_id: str,
) -> list[SubAgentSessionView]:
    """Resolve direct and nested child sessions for a parent session."""
    children = await storage.list_child_sessions(user_id, parent_session_id)
    views: list[SubAgentSessionView] = []
    for child in children:
        agent = await storage.get_agent(user_id, child.agent_id)
        if agent is None:
            continue
        views.append(
            SubAgentSessionView(
                session=child,
                agent=agent,
                is_running=await message_bus.session_is_running(child.id),
                children=await _build_child_sessions(
                    storage,
                    message_bus,
                    user_id,
                    child.id,
                ),
            ),
        )
    return views


session_router = APIRouter(
    prefix="/sessions",
    tags=["sessions"],
    responses={404: {"description": "Not found"}},
)


async def _ensure_credential_exists(
    storage: StorageBase,
    user_id: str,
    config: ChatModelConfig | None,
) -> None:
    """Validate that the credential referenced by ``config`` belongs to the
    given user. No-op when ``config`` is ``None``.

    Args:
        storage (`StorageBase`): Injected storage backend.
        user_id (`str`): The authenticated user ID.
        config (`ChatModelConfig | None`): Model config to validate. Pass
            ``None`` to skip the check.

    Raises:
        `HTTPException`: 404 if the credential does not exist or does not
            belong to the user.
    """
    if config is None:
        return
    credentials = await storage.list_credentials(user_id)
    if not any(c.id == config.credential_id for c in credentials):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Credential '{config.credential_id}' not found.",
        )


@session_router.get(
    "/",
    response_model=ListSessionsResponse,
    summary="List sessions for an agent",
)
async def list_sessions(
    agent_id: str = Query(description="Filter sessions by agent ID."),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    message_bus: MessageBus = Depends(get_message_bus),
) -> ListSessionsResponse:
    """Return all sessions for an agent as enriched
    :class:`SessionView` entries.

    Each entry bundles three things the chat UI needs to render
    without follow-up requests: the session record (incl.
    ``state``), whether a chat run is currently active, and — when
    the session participates in a team — the resolved team detail
    (leader agent + member agents with their session ids).

    Args:
        agent_id (`str`):
            Agent whose sessions to list.
        user_id (`str`):
            Injected authenticated user ID.
        storage (`StorageBase`):
            Injected storage backend.
        message_bus (`MessageBus`):
            Injected message bus (used for ``session_is_running``).

    Returns:
        `ListSessionsResponse`:
            Enriched session views and their count.

    Raises:
        `HTTPException`: 404 if the agent does not exist or does not
            belong to the authenticated user.
    """
    # Direct ownership check via get_agent — handles both source=user
    # and source=team agents (the latter aren't returned by
    # storage.list_agents but are still owned by the user; reachable
    # via team navigation).
    agent = await storage.get_agent(user_id, agent_id)
    if agent is None or agent.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found.",
        )

    sessions = await storage.list_sessions(user_id, agent_id)
    views: list[SessionView] = []
    for session in sessions:
        if session.parent_session_id is not None:
            continue
        team_detail = None
        if session.team_id:
            team_record = await storage.get_team(user_id, session.team_id)
            if team_record is not None:
                team_detail = await _build_team_detail(
                    storage,
                    user_id,
                    team_record,
                )
        views.append(
            SessionView(
                session=session,
                is_running=await message_bus.session_is_running(session.id),
                team=team_detail,
                children=await _build_child_sessions(
                    storage,
                    message_bus,
                    user_id,
                    session.id,
                ),
            ),
        )
    return ListSessionsResponse(sessions=views, total=len(views))


@session_router.post(
    "/",
    response_model=CreateSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new session",
)
async def create_session(
    body: CreateSessionRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> CreateSessionResponse:
    """Create (or resume) a session for a given agent and workspace.

    At most one session exists per ``(user_id, agent_id, workspace_id)``
    triple — a second call with the same triple updates the existing session
    rather than creating a duplicate.

    Args:
        body (`CreateSessionRequest`): Agent, workspace, and model config.
        user_id (`str`): Injected authenticated user ID.
        storage (`StorageBase`): Injected storage backend.

    Returns:
        `CreateSessionResponse`: The session identifier.

    Raises:
        `HTTPException`: 404 if the agent or credential does not exist or
            does not belong to the authenticated user.
    """
    agent = await storage.get_agent(user_id, body.agent_id)
    if agent is None or agent.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{body.agent_id}' not found.",
        )

    resolved_chat_model_config = (
        body.chat_model_config or agent.data.default_chat_model_config
    )
    await _ensure_credential_exists(
        storage,
        user_id,
        resolved_chat_model_config,
    )
    await _ensure_credential_exists(
        storage,
        user_id,
        body.fallback_chat_model_config,
    )

    state = None
    if body.permission_mode is not None:
        state = AgentState(
            permission_context=PermissionContext(mode=body.permission_mode),
        )

    session_record = await storage.upsert_session(
        user_id=user_id,
        agent_id=body.agent_id,
        config=SessionConfig(
            workspace_id=body.workspace_id or uuid.uuid4().hex,
            chat_model_config=resolved_chat_model_config,
            fallback_chat_model_config=body.fallback_chat_model_config,
            **({"name": body.name} if body.name is not None else {}),
        ),
        state=state,
    )
    return CreateSessionResponse(session_id=session_record.id)


@session_router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a session",
)
async def delete_session(
    session_id: str,
    agent_id: str = Query(description="Agent the session belongs to."),
    user_id: str = Depends(get_current_user_id),
    session_service: SessionService = Depends(get_session_service),
) -> None:
    """Permanently delete a session and all its associated state.

    Cancels any in-flight chat run for this session (and for every
    worker session if this one is a team leader) before dropping
    storage records and bus state. The cancel path is cross-process:
    whichever worker is actually running the session will receive the
    cancel broadcast and abort.

    Args:
        session_id (`str`): The session to delete.
        agent_id (`str`): The agent the session belongs to.
        user_id (`str`): Injected authenticated user ID.
        session_service (`SessionService`): Injected session service.

    Raises:
        `HTTPException`: 404 if the session does not exist or does not belong
            to the authenticated user.
    """
    deleted = await session_service.delete_session(
        user_id,
        agent_id,
        session_id,
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )


@session_router.post(
    "/{session_id}/cancel",
    response_model=CancelSessionResponse,
    summary="Cancel a running session",
)
async def cancel_session(
    session_id: str,
    agent_id: str = Query(description="Agent the session belongs to."),
    user_id: str = Depends(get_current_user_id),
    session_service: SessionService = Depends(get_session_service),
    storage: StorageBase = Depends(get_storage),
) -> CancelSessionResponse:
    """Request cancellation of the current run for ``session_id``.

    This endpoint keeps the session record intact and only targets the
    in-flight execution. The cancellation path is cross-process:
    whichever worker currently owns the run receives the broadcast and
    aborts locally via :class:`CancelDispatcher`.

    Args:
        session_id (`str`): The session whose active run should stop.
        agent_id (`str`): The agent the session belongs to.
        user_id (`str`): Injected authenticated user ID.
        session_service (`SessionService`): Injected session service.
        storage (`StorageBase`): Injected storage backend, used for
            ownership validation.

    Returns:
        `CancelSessionResponse`:
            Cancellation request status plus whether the run lock was
            observed released before returning.

    Raises:
        `HTTPException`: 404 if the session does not exist or does not belong
            to the authenticated user.
    """
    existing = await storage.get_session(user_id, agent_id, session_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )

    released = await session_service.cancel_session_run(
        session_id,
        user_id=user_id,
        agent_id=agent_id,
    )
    return CancelSessionResponse(
        session_id=session_id,
        status="cancel_requested",
        released=released,
    )


@session_router.patch(
    "/{session_id}",
    response_model=SessionRecord,
    summary="Update a session",
)
async def update_session(
    session_id: str,
    body: UpdateSessionRequest,
    agent_id: str = Query(description="Agent the session belongs to."),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> SessionRecord:
    """Update the model configuration of an existing session.

    Args:
        session_id (`str`): The session to update.
        body (`UpdateSessionRequest`): Fields to update.
        user_id (`str`): Injected authenticated user ID.
        storage (`StorageBase`): Injected storage backend.

    Returns:
        `SessionRecord`: The full session record after the update.

    Raises:
        `HTTPException`: 404 if the session, agent, or credential does not
            exist or does not belong to the authenticated user.
    """
    existing = await storage.get_session(user_id, agent_id, session_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )

    await _ensure_credential_exists(storage, user_id, body.chat_model_config)
    await _ensure_credential_exists(
        storage,
        user_id,
        body.fallback_chat_model_config,
    )

    updated_state = existing.state
    if body.permission_mode is not None:
        updated_ctx = existing.state.permission_context.model_copy(
            update={"mode": body.permission_mode},
        )

        updated_state = existing.state.model_copy(
            update={
                "permission_context": updated_ctx,
            },
        )

    # PATCH semantics: only fields explicitly present in the request body are
    # applied. ``exclude_unset=True`` lets clients distinguish "leave
    # unchanged" (omit) from "clear" (send ``null``) — required for clearing
    # ``fallback_chat_model_config``.
    config_updates = body.model_dump(
        exclude_unset=True,
        exclude={"permission_mode"},
    )

    return await storage.upsert_session(
        user_id=user_id,
        agent_id=agent_id,
        config=SessionConfig.model_validate(
            {**existing.config.model_dump(mode="json"), **config_updates},
        ),
        state=updated_state,
        session_id=session_id,
    )


# ----------------------------------------------------------------------
# Messages: fetch persisted messages for a session
# ----------------------------------------------------------------------


@session_router.get(
    "/{session_id}/messages",
    response_model=ListMessagesResponse,
    summary="List messages for a session",
)
async def list_messages(
    session_id: str,
    request: Request,
    agent_id: str = Query(description="Agent the session belongs to."),
    offset: int = Query(0, ge=0, description="Pagination offset."),
    limit: int = Query(50, ge=1, le=200, description="Max messages."),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    message_bus: MessageBus = Depends(get_message_bus),
    chat_service: ChatService = Depends(get_chat_service),
) -> ListMessagesResponse:
    """Return persisted messages for a session.

    Args:
        session_id: The session to query.
        agent_id: Agent the session belongs to.
        offset: Pagination offset.
        limit: Maximum number of messages to return.
        user_id: Injected authenticated user ID.
        storage: Injected storage backend.
        message_bus: Injected message bus.

    Returns:
        Messages and running status.
    """
    existing = await storage.get_session(user_id, agent_id, session_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )

    messages = await storage.list_messages(
        user_id,
        session_id,
        offset=offset,
        limit=limit,
    )
    return ListMessagesResponse(
        messages=chat_service.hydrate_public_messages(
            messages,
            request=request,
        ),
        is_running=await message_bus.session_is_running(session_id),
    )


@session_router.post(
    "/{session_id}/rollback",
    response_model=RollbackSessionResponse,
    summary="Rollback a session before a user message",
)
async def rollback_session(
    session_id: str,
    body: RollbackSessionRequest,
    agent_id: str = Query(description="Agent the session belongs to."),
    user_id: str = Depends(get_current_user_id),
    session_service: SessionService = Depends(get_session_service),
) -> RollbackSessionResponse:
    """Rollback the current session before the target user message."""
    (
        _session,
        restored_message,
        remaining_message_count,
    ) = await session_service.rollback_to_before_message(
        user_id=user_id,
        agent_id=agent_id,
        session_id=session_id,
        message_id=body.message_id,
    )
    return RollbackSessionResponse(
        session_id=session_id,
        rolled_back_from_message_id=body.message_id,
        remaining_message_count=remaining_message_count,
        restored_draft_message=ChatService.sanitize_public_message(
            restored_message,
        ),
    )


@session_router.get(
    "/{session_id}/export",
    response_model=SessionExportResponse,
    summary="Export a session as JSON payload",
)
async def export_session(
    session_id: str,
    agent_id: str = Query(description="Agent the session belongs to."),
    include_system_messages: bool = Query(
        False,
        description="Whether to include reconstructed system messages.",
    ),
    include_tool_schemas: bool = Query(
        False,
        description="Whether to include tool schemas.",
    ),
    truncate_tool_call_input: bool = Query(
        True,
        description="Whether to truncate tool-call inputs.",
    ),
    tool_call_input_max_length: int = Query(
        200,
        ge=1,
        description="Maximum tool-call input length when truncation is enabled.",
    ),
    truncate_tool_result: bool = Query(
        True,
        description="Whether to truncate tool execution results.",
    ),
    tool_result_max_length: int = Query(
        200,
        ge=1,
        description="Maximum tool-result length when truncation is enabled.",
    ),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    chat_service: ChatService = Depends(get_chat_service),
) -> SessionExportResponse:
    """Return the fully assembled export payload for a session."""
    existing = await storage.get_session(user_id, agent_id, session_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )

    payload = await chat_service.build_session_export_payload(
        user_id=user_id,
        agent_id=agent_id,
        session_id=session_id,
        include_system_messages=include_system_messages,
        include_tool_schemas=include_tool_schemas,
        truncate_tool_call_input=truncate_tool_call_input,
        tool_call_input_max_length=tool_call_input_max_length,
        truncate_tool_result=truncate_tool_result,
        tool_result_max_length=tool_result_max_length,
    )
    return SessionExportResponse.model_validate(payload)


# ----------------------------------------------------------------------
# Stream: live SSE connection for session events
# ----------------------------------------------------------------------

_HEARTBEAT_INTERVAL_SECS = 30
# Interval between SSE heartbeat comment frames (``:\\n\\n``).


async def _iter_session_sse_frames(
    message_bus: MessageBus,
    session_id: str,
    since: str | None = None,
) -> AsyncGenerator[str, None]:
    """Yield gap-free SSE frames for a session's event stream.

    The live subscription is established *before* replay is read so
    events published during the handoff window cannot fall through the
    cracks. Because those same events may also appear in the replay log,
    the generator deduplicates by the replay-log ``_entry_id`` carried
    on live Pub/Sub payloads.
    """
    queue: asyncio.Queue[dict | None] = asyncio.Queue()
    startup: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    seen_entry_ids: set[str] = set()
    events_key = message_bus._SESSION_EVENTS_KEY.format(sid=session_id)

    async def _feeder() -> None:
        """Forward raw live payloads to ``queue`` after subscription."""
        try:
            async for evt in message_bus.subscribe(
                events_key,
                on_ready=lambda: (
                    None if startup.done() else startup.set_result(None)
                ),
            ):
                await queue.put(evt)
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # pylint: disable=broad-except
            if not startup.done():
                startup.set_exception(exc)
            raise
        finally:
            if not startup.done():
                startup.set_result(None)
            await queue.put(None)

    feeder_task = asyncio.create_task(
        _feeder(),
        name=f"sse-feeder:{session_id}",
    )

    try:
        await startup

        # Replay after the live subscription is ready so anything
        # published during reconnect/refresh is captured either by the
        # replay read below or by the live queue.
        for entry_id, event in await message_bus.session_read_events(
            session_id,
            since=since,
        ):
            seen_entry_ids.add(entry_id)
            yield f"data: {json.dumps({**event, '_entry_id': entry_id})}\n\n"

        while True:
            try:
                item = await asyncio.wait_for(
                    queue.get(),
                    timeout=_HEARTBEAT_INTERVAL_SECS,
                )
                if item is None:
                    break

                entry_id = item.get("_entry_id")
                if isinstance(entry_id, str):
                    if entry_id in seen_entry_ids:
                        continue
                    seen_entry_ids.add(entry_id)

                yield f"data: {json.dumps(item)}\n\n"
            except asyncio.TimeoutError:
                yield ":\n\n"
    finally:
        feeder_task.cancel()
        try:
            await feeder_task
        except asyncio.CancelledError:
            pass


@session_router.get(
    "/{session_id}/stream",
    summary="Subscribe to a session's event stream (SSE)",
    response_description="Server-Sent Events stream of AgentEvent objects",
)
async def stream_session_events(
    session_id: str,
    agent_id: str = Query(description="Agent the session belongs to."),
    replay_after: str | None = Query(
        default=None,
        description=(
            "Optional replay-log entry id already folded into the client's "
            "history snapshot. When provided, replay starts strictly after "
            "this entry."
        ),
    ),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    message_bus: MessageBus = Depends(get_message_bus),
) -> StreamingResponse:
    """Subscribe to a session's live event stream.

    Returns a ``text/event-stream`` that first replays any buffered
    events from the current run's replay log (if a run is in progress
    or just finished), then streams live events as they are produced
    by :meth:`ChatService.run`. The connection stays open
    until the client disconnects — subsequent runs on the same session
    are delivered over the same connection.

    A heartbeat comment frame (``:\\n\\n``) is sent every 30 seconds to
    keep the connection alive through reverse proxies.

    Args:
        session_id (`str`):
            The session to subscribe to.
        agent_id (`str`):
            The agent that owns the session (used for ownership
            validation).
        user_id (`str`):
            Injected authenticated user id.
        storage (`StorageBase`):
            Injected storage backend (ownership check only).
        message_bus (`MessageBus`):
            Injected message bus (replay + live subscription).

    Returns:
        `StreamingResponse`:
            SSE stream of AgentEvent frames + periodic heartbeats.
    """
    existing = await storage.get_session(user_id, agent_id, session_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )

    replay_since = replay_after
    if replay_since is None:
        current_reply = await storage.get_message(
            user_id,
            session_id,
            existing.state.reply_id,
        )
        replay_since = get_reply_checkpoint_replay_entry_id(current_reply)

    return StreamingResponse(
        _iter_session_sse_frames(message_bus, session_id, since=replay_since),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
