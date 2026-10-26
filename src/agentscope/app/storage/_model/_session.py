# -*- coding: utf-8 -*-
"""The session data classes for storage."""
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from ._base import _RecordBase
from ....permission import PermissionMode
from ....state import AgentState


class SessionSource(str, Enum):
    """The source that created the session."""

    USER = "user"
    SCHEDULE = "schedule"
    CHANNEL = "channel"
    SUBAGENT = "subagent"


class ChatModelConfig(BaseModel):
    """The model configuration class."""

    type: str
    """The provider type."""

    credential_id: str
    """The credential id."""

    model: str
    """The model name."""

    parameters: dict
    """The model parameters."""

    context_size: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Optional context-size override for this model config. Must not "
            "exceed the model card's declared context size."
        ),
    )
    """Optional context-size override for this model config."""

    @model_validator(mode="after")
    def _validate_context_size(self) -> "ChatModelConfig":
        """Validate the optional context-size override against the model card.

        The override is intentionally constrained to be less than or equal to
        the model card default, so callers can shrink the context window but
        cannot claim a larger one than the provider/model supports.
        """
        if self.context_size is None:
            return self

        from ....credential import CredentialFactory

        credential_cls = CredentialFactory.get_credential_class(self.type)
        if credential_cls is None:
            raise ValueError(f"Provider '{self.type}' not found.")

        model_cls = credential_cls.get_chat_model_class()
        card = model_cls.get_model_card(self.model)
        if card is None:
            raise ValueError(
                f"Model '{self.model}' not found for provider '{self.type}'."
            )

        if self.context_size > card.context_size:
            raise ValueError(
                "context_size must be less than or equal to the model "
                f"limit ({card.context_size}) for model '{self.model}'."
            )

        return self


class TTSModelConfig(BaseModel):
    """The TTS model configuration class."""

    type: str
    """The provider type."""

    credential_id: str
    """The credential id."""

    model: str
    """The TTS model name."""

    parameters: dict = Field(default_factory=dict)
    """TTS parameters (voice, language, etc.)."""


class EmbeddingModelConfig(BaseModel):
    """Configuration for constructing an embedding model from a credential.

    This DTO is kept independent from :class:`SessionConfig` because this
    project does not enable the official knowledge base / RAG subsystem yet.
    """

    type: str
    """The provider type (e.g. ``"openai_credential"``)."""

    credential_id: str
    """The credential id to use for authentication."""

    model: str
    """The embedding model name."""

    dimensions: int = Field(..., gt=0)
    """The output embedding vector dimensions."""

    parameters: dict = Field(default_factory=dict)
    """The provider-specific non-dimensional parameters."""


class SessionConfig(BaseModel):
    """Session configuration — set at creation, updatable via PATCH."""

    workspace_id: str
    """The workspace id this session is bound to."""

    name: str = Field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        description="Display name for the session.",
    )
    """The session display name."""

    chat_model_config: ChatModelConfig | None = None
    """The chat model config. None means no model has been configured yet."""

    fallback_chat_model_config: ChatModelConfig | None = None
    """The fallback chat model config. Used as a backup when the primary
    model fails. None means no fallback configured."""

    tts_model_config: TTSModelConfig | None = None
    """The TTS model config. None means TTS is disabled."""

    permission_mode: PermissionMode = PermissionMode.DEFAULT
    """Persisted permission mode for this session."""


class SessionRecord(_RecordBase):
    """The lightweight persisted session record."""

    user_id: str
    """The user id."""

    agent_id: str
    """The agent id."""

    source: SessionSource = SessionSource.USER
    """The source that created this session."""

    source_schedule_id: str | None = None
    """The source schedule Id."""

    source_chat_id: str | None = None
    """For channel-created sessions, the platform chat this session maps
    to."""

    source_chat_name: str | None = None
    """For channel-created sessions, that chat's title when available."""

    source_chat_user_id: str | None = None
    """For one-to-one channel sessions, the target platform user id."""

    source_chat_user_name: str | None = None
    """For one-to-one channel sessions, the target user name when known."""

    source_channel_id: str | None = None
    """For channel-created sessions, the owning channel id."""

    conversation_kind: str | None = None
    """The audience shape of this session, e.g. ``group`` or ``private``.

    This is intentionally source-agnostic so the same capability can be
    reused by other multi-party session types beyond external channels.
    """

    team_id: str | None = None
    """The team this session participates in, if any.

    Team membership is session-level: a user agent can lead multiple teams
    across different sessions, and each worker session belongs to exactly
    one team. ``None`` means the session is not part of any team.
    """

    config: SessionConfig
    """Session configuration (workspace, name, model, permission)."""

    parent_session_id: str | None = None
    """The parent session that spawned this child session, if any."""


class SessionWithState(SessionRecord):
    """A hydrated session record with runtime state attached."""

    state: AgentState = Field(default_factory=AgentState)
    """Mutable runtime state, loaded on demand for full session reads."""
