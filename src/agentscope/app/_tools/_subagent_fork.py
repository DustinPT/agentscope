# -*- coding: utf-8 -*-
"""DSH-style fork subagent tool backed by managed AgentScope child sessions."""
from __future__ import annotations

from ...state import AgentState
import copy

from ._subagent import Subagent
from .._subagent_runtime import build_fork_child_state


class SubagentFork(Subagent):
    name = "subagent_fork"
    mode = "fork"
    description = (
        "Delegate a task to a subagent that inherits this conversation: a child agent seeded with the completed "
        "ReAct steps so far, excluding the current in-progress step. Use this when the subtask should build on "
        "work that is already finished in this conversation without copying the step that is still being executed. "
        "You receive its result, not its intermediate steps. This tool starts an independently managed subagent and "
        "immediately returns its launch metadata. If you are still online when it finishes, the runtime automatically "
        "delivers its completion notice. The notice includes the final answer unless the child sent you a message "
        "just before finishing. Use `send_message` to steer it while running or continue its conversation after it "
        "finishes."
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        schema = copy.deepcopy(self.input_schema)
        schema["properties"]["prompt"]["description"] = (
            "The task for the subagent. It already sees the completed ReAct steps inherited from this conversation, "
            "but not the current in-progress step, so state only what is new."
        )
        self.input_schema = schema

    def _build_child_state(
        self,
        *,
        caller_state: AgentState,
        root_session_id: str,
        parent_name: str,
        child_name: str,
        child_path: str,
        description: str,
        cwd: str | None,
    ) -> AgentState:
        return build_fork_child_state(
            parent_state=caller_state,
            permission_mode=caller_state.permission_context.mode,
            root_session_id=root_session_id,
            parent_session_id=self._session_id,
            parent_name=parent_name,
            name=child_name,
            path=child_path,
            label=description.strip(),
            cwd=(cwd or "").strip() or None,
        )
