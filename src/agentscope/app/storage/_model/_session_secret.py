# -*- coding: utf-8 -*-
"""Session-scoped secret storage models."""
from datetime import datetime

from pydantic import Field

from ._base import _RecordBase


class SessionSecretBinding(_RecordBase):
    """One env var binding stored for a session."""

    env_var_name: str = Field(
        min_length=1,
        description="The environment variable name exposed to Bash.",
    )
    secret_value: str = Field(
        default="",
        description="The secret value stored only in backend storage.",
    )
    source_tool_call_id: str | None = Field(
        default=None,
        description="Tool call that created or updated this binding.",
    )
    expires_at: datetime = Field(
        description="When this binding expires and stops being injected.",
    )


class SessionSecretRecord(_RecordBase):
    """All active secret bindings for one user/session pair."""

    user_id: str = Field(description="Owner user id.")
    session_id: str = Field(description="Owning session id.")
    bindings: dict[str, SessionSecretBinding] = Field(
        default_factory=dict,
        description="Bindings keyed by env var name.",
    )
