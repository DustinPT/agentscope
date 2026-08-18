# -*- coding: utf-8 -*-
"""Persisted runtime state for one session."""

from pydantic import Field

from ._base import _RecordBase
from ....state import AgentState


class SessionStateRecord(_RecordBase):
    """One persisted runtime-state snapshot for a session."""

    session_id: str
    """The owning session id."""

    user_id: str
    """The owner user id."""

    state: AgentState = Field(default_factory=AgentState)
    """The mutable runtime state for the session."""
