"""Runtime-local tool call context for backend request attribution."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ToolCallContext:
    """Per-tool-call metadata propagated to backend requests."""

    tool_call_id: str
    tool_name: str


_CURRENT_TOOL_CALL: ContextVar[ToolCallContext | None] = ContextVar(
    "agentscope_current_tool_call",
    default=None,
)


def set_current_tool_call(context: ToolCallContext) -> Token[ToolCallContext | None]:
    """Set the current tool call context for the active task."""
    return _CURRENT_TOOL_CALL.set(context)


def reset_current_tool_call(token: Token[ToolCallContext | None]) -> None:
    """Restore the previous tool call context."""
    _CURRENT_TOOL_CALL.reset(token)


def get_current_tool_call() -> ToolCallContext | None:
    """Return the current tool call context, if any."""
    return _CURRENT_TOOL_CALL.get()
