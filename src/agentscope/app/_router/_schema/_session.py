# -*- coding: utf-8 -*-
"""Request / response schemas for the session router."""
from pydantic import BaseModel, Field

from ....message import Msg
from ....permission import PermissionMode
from ...storage import (
    AgentRecord,
    ChatModelConfig,
    SessionRecord,
    SessionWithState,
    TeamRecord,
    TTSModelConfig,
)


class TeamMemberView(BaseModel):
    """One row in :attr:`TeamDetailResponse.members`.

    Pairs each member's :class:`AgentRecord` with its single
    ``session_id`` so the UI can subscribe to the worker's chat
    stream without a separate lookup.
    """

    agent: AgentRecord = Field(
        description="The worker agent record.",
    )
    session_id: str | None = Field(
        default=None,
        description=(
            "The worker's session id. ``None`` if the agent is in an "
            "inconsistent state (worker without a session)."
        ),
    )


class TeamDetailResponse(BaseModel):
    """Resolved team detail embedded inside :class:`SessionView.team`."""

    team: TeamRecord = Field(description="The team record.")
    leader_agent: AgentRecord | None = Field(
        default=None,
        description=(
            "Leader's agent record (resolved from the team's "
            "``session_id`` → session.agent_id)."
        ),
    )
    members: list[TeamMemberView] = Field(
        default_factory=list,
        description=(
            "Worker agents listed in :attr:`TeamData.member_ids`, each "
            "paired with its single session id when available."
        ),
    )


class SubAgentSessionView(BaseModel):
    """Recursive child-session view for sub-agent sessions."""

    session: SessionWithState = Field(
        description="The hydrated child session record.",
    )
    agent: AgentRecord = Field(description="The child agent record.")
    is_running: bool = Field(
        description="Whether a chat run is currently active on this child session.",
    )
    children: list["SubAgentSessionView"] = Field(
        default_factory=list,
        description="Direct child sessions spawned from this child session.",
    )


class CreateSessionRequest(BaseModel):
    """Request body for creating a new session."""

    agent_id: str = Field(description="Agent this session belongs to.")
    workspace_id: str | None = Field(
        default=None,
        description=(
            "Optional explicit workspace binding. When omitted the "
            "server resolves one through the configured workspace "
            "manager isolation policy. Set only when you need to "
            "force sharing or reconnect to a specific workspace."
        ),
    )
    name: str | None = Field(
        default=None,
        description="Display name. Defaults to current datetime if omitted.",
    )
    chat_model_config: ChatModelConfig | None = Field(
        default=None,
        description="Model provider and parameters. "
        "If omitted, the agent default model is used when configured. "
        "Can be set later via PATCH.",
    )
    fallback_chat_model_config: ChatModelConfig | None = Field(
        default=None,
        description="Fallback model used when the primary model fails. "
        "Can be set later via PATCH.",
    )
    tts_model_config: TTSModelConfig | None = Field(
        default=None,
        description="Optional TTS model. Can be set later via PATCH.",
    )
    permission_mode: PermissionMode | None = Field(
        default=None,
        description="Initial permission mode for the session. "
        "If omitted, the default permission context is used.",
    )


class CreateSessionResponse(BaseModel):
    """Response body after creating a session."""

    session_id: str = Field(description="Server-assigned session identifier.")


class InterruptSessionResponse(BaseModel):
    """Response body after requesting interruption of a session reply."""

    session_id: str = Field(description="Echo of the interrupted session id.")


class RollbackSessionRequest(BaseModel):
    """Request body for rolling a session back before a user message."""

    message_id: str = Field(
        description=(
            "User message id to roll back before. The target message "
            "itself is restored into the draft area and removed from "
            "the persisted timeline."
        ),
    )


class RollbackSessionResponse(BaseModel):
    """Response body after a successful session rollback."""

    session_id: str = Field(description="The rolled-back session id.")
    rolled_back_from_message_id: str = Field(
        description="The user message id that was used as the rollback target.",
    )
    remaining_message_count: int = Field(
        description="Number of persisted messages left after rollback.",
    )
    restored_draft_message: Msg = Field(
        description=(
            "The original user message content restored to the input area."
        ),
    )


class UpdateSessionRequest(BaseModel):
    """Request body for updating an existing session.

    Omit any field to keep its current value.
    """

    name: str | None = Field(
        default=None,
        description="New display name.",
    )
    chat_model_config: ChatModelConfig | None = Field(
        default=None,
        description="New model configuration. "
        "Replaces the existing one entirely. "
        "Pass null to clear; omit to leave unchanged.",
    )
    fallback_chat_model_config: ChatModelConfig | None = Field(
        default=None,
        description="New fallback model configuration. "
        "Pass null to clear; omit to leave unchanged.",
    )
    tts_model_config: TTSModelConfig | None = Field(
        default=None,
        description="New TTS model configuration. "
        "Pass null to disable; omit to leave unchanged.",
    )
    permission_mode: PermissionMode | None = Field(
        default=None,
        description="New permission mode for the session.",
    )


class SessionSummaryView(BaseModel):
    """Lightweight session bundle used by the session sidebar."""

    session: SessionRecord = Field(
        description="The persisted lightweight session record.",
    )
    is_running: bool = Field(
        description="Whether a chat run is currently active on this session.",
    )


class SessionView(BaseModel):
    """Per-session bundle with everything the frontend needs to
    render either the list view or open a session.

    Bundles three orthogonal pieces of information so opening a
    session does not require a waterfall of follow-up requests:

    - the persisted :class:`SessionWithState` itself (config + state),
    - whether the session has an active chat run right now,
    - the team detail (resolved leader + members) when the session
      participates in a team.

    Messages are intentionally **not** included here — they are
    paginated separately via ``GET /sessions/{id}/messages``.
    """

    session: SessionWithState = Field(
        description=(
            "The persisted session record. Includes ``state`` "
            "(``permission_context`` / ``tool_context`` / "
            "``tasks_context``) inline."
        ),
    )
    is_running: bool = Field(
        description="Whether a chat run is currently active on this session.",
    )
    team: TeamDetailResponse | None = Field(
        default=None,
        description=(
            "Resolved team detail when ``session.team_id`` is set "
            "(leader agent + member agents with their session ids). "
            "``None`` when the session does not participate in any team."
        ),
    )
    children: list[SubAgentSessionView] = Field(
        default_factory=list,
        description="Recursive child sessions spawned under this session.",
    )


class ListSessionsResponse(BaseModel):
    """Response body for listing sessions."""

    sessions: list[SessionSummaryView] = Field(
        description="Lightweight session views for the session sidebar.",
    )
    total: int = Field(description="Total number of sessions.")


class ListMessagesResponse(BaseModel):
    """Response body for listing messages in a session."""

    messages: list = Field(description="Messages in chronological order.")
    is_running: bool = Field(
        description="Whether the session is currently running.",
    )


class SessionExportSessionInfo(BaseModel):
    """Session metadata included in an export payload."""

    agent_id: str = Field(description="Owning agent id.")
    agent_name: str = Field(description="Owning agent name.")
    session_id: str = Field(description="Session id.")
    session_name: str = Field(description="Session display name.")
    source: str = Field(description="Session source.")
    workspace_id: str = Field(description="Workspace id bound to the session.")
    created_at: str = Field(description="Session creation timestamp.")
    updated_at: str = Field(description="Session update timestamp.")


class SessionExportOptions(BaseModel):
    """Export options applied by the backend."""

    include_system_messages: bool = Field(
        description="Whether system messages are included in the export.",
    )
    include_tool_schemas: bool = Field(
        description="Whether tool schemas are included in the export.",
    )
    truncate_tool_call_input: bool = Field(
        description="Whether tool-call inputs are truncated.",
    )
    tool_call_input_max_length: int = Field(
        description="Maximum tool-call input length when truncation is enabled.",
    )
    truncate_tool_result: bool = Field(
        description="Whether tool execution results are truncated.",
    )
    tool_result_max_length: int = Field(
        description="Maximum tool-result length when truncation is enabled.",
    )


class SessionExportResponse(BaseModel):
    """Response body for exporting a session."""

    version: int = Field(description="Export payload version.")
    exported_at: str = Field(description="Export generation timestamp.")
    session: SessionExportSessionInfo = Field(
        description="Session metadata bundled with the export.",
    )
    export_options: SessionExportOptions = Field(
        description="Options applied while building the export payload.",
    )
    tool_schemas: list[dict] = Field(
        default_factory=list,
        description="Tool JSON schemas exported for the current session.",
    )
    messages: list[dict] = Field(
        default_factory=list,
        description="Exported messages as JSON-serializable objects.",
    )
