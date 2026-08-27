# -*- coding: utf-8 -*-
"""The channel storage model."""
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from ._base import _RecordBase
from ....permission import PermissionMode


class SessionScope(str, Enum):
    """How inbound messages are grouped into agent sessions."""

    PER_CHAT = "per_chat"
    PER_CHAT_USER = "per_chat_user"


class ChannelBinding(BaseModel):
    """One routing rule for an inbound channel event."""

    match_key: str = "chat_id"
    match_value: str = "*"
    agent_id: str
    session_scope: SessionScope = SessionScope.PER_CHAT


class RoutingConfig(BaseModel):
    """Ordered routing rules for a channel."""

    bindings: list[ChannelBinding] = Field(default_factory=list)

    @field_validator("bindings")
    @classmethod
    def _validate_bindings(
        cls,
        bindings: list[ChannelBinding],
    ) -> list[ChannelBinding]:
        if not bindings:
            raise ValueError(
                "routing.bindings must contain at least a catch-all rule "
                "(match_value='*').",
            )
        catch_all_indices = [
            i for i, binding in enumerate(bindings)
            if binding.match_value == "*"
        ]
        if len(catch_all_indices) != 1:
            raise ValueError(
                "routing.bindings must contain exactly one catch-all rule "
                "(match_value='*').",
            )
        if catch_all_indices[0] != len(bindings) - 1:
            raise ValueError(
                "The catch-all rule (match_value='*') must be the last "
                "binding; rules after it would be unreachable.",
            )

        seen: set[tuple[str, str]] = set()
        for binding in bindings:
            key = (binding.match_key, binding.match_value)
            if key in seen:
                raise ValueError(
                    f"Duplicate routing rule for {key}; the later one "
                    "would be unreachable.",
                )
            seen.add(key)
        return bindings


class SessionSettings(BaseModel):
    """Settings applied when a channel creates an agent session."""

    chat_model_config: dict[str, Any]
    fallback_chat_model_config: dict[str, Any] | None = None
    permission_mode: str = PermissionMode.DEFAULT.value

    @field_validator("permission_mode")
    @classmethod
    def _validate_permission_mode(cls, value: str) -> str:
        PermissionMode(value)
        return value


class ChannelRecord(_RecordBase):
    """Persistent record for a channel instance."""

    channel_type: str
    name: str | None = None
    user_id: str
    enabled: bool = True
    credentials: dict[str, Any] = Field(default_factory=dict)
    platform_config: dict[str, Any] = Field(default_factory=dict)
    routing: RoutingConfig
    session: SessionSettings
