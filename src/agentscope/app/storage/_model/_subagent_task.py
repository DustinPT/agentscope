# -*- coding: utf-8 -*-
"""The sub-agent task record for delegated child-session invocations."""
from datetime import datetime

from ._base import _RecordBase


class SubAgentTaskRecord(_RecordBase):
    """Persisted task record for one delegated sub-agent invocation."""

    user_id: str
    """The owner user id."""

    parent_session_id: str
    """The direct parent session id."""

    child_session_id: str
    """The child session id handling this delegated invocation."""

    child_agent_id: str
    """The child agent id handling this delegated invocation."""

    parent_tool_call_id: str
    """The direct parent tool call id bound to this invocation."""

    parent_task_id: str | None = None
    """The direct parent task id if this invocation is a descendant."""

    status: str = "running"
    """Protocol status for this delegated invocation."""

    open_descendant_count: int = 0
    """Number of unfinished descendant invocations spawned by this task."""

    terminal_reason: str | None = None
    """Terminal reason for this delegated invocation, if any."""

    launch_requested_at: datetime | None = None
    """When this delegated invocation was last launched or resumed."""

    last_progress_at: datetime | None = None
    """When this delegated invocation last made observable progress."""
