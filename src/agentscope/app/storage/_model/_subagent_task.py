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

    root_session_id: str | None = None
    """The durable root session for the current subagent tree."""

    parent_agent_id: str | None = None
    """The direct parent agent id that created or resumed this task."""

    name: str | None = None
    """The child name unique among siblings, if known."""

    path: str | None = None
    """The root-relative child path used for runtime settlement notices."""

    label: str | None = None
    """The delegated task label shown to the parent."""

    mode: str | None = None
    """The task mode, such as spawn or message."""

    continuable: bool = True
    """Whether the child can accept follow-up input after settling."""

    delivery: str = "parent"
    """How the terminal result should be delivered."""

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
