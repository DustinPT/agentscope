# -*- coding: utf-8 -*-
"""Shared helpers for DSH-style subagent tools on top of AgentScope sessions."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from typing import Any

from .storage import SessionRecord, SessionSource, SubAgentTaskRecord
from ..message import (
    ContentBlock,
    DataBlock,
    HintBlock,
    Msg,
    TextBlock,
    ToolCallBlock,
    ToolCallState,
    ToolResultBlock,
)
from ..permission import PermissionContext
from ..state import AgentState

ROOT_AGENT_NAME = "lead"
SUBAGENT_META_KEY = "subagent_runtime"
TERMINAL_PARENT_INVOCATION_STATUSES = frozenset(
    {
        "final_delivered",
        "protocol_failed",
        "error_delivered",
    },
)
RUN_ERROR_KIND_SUMMARIES = {
    "rate_limit": "Model service is rate limited.",
    "auth": "Model authentication failed.",
    "timeout": "Model request timed out.",
    "connection": "Model service connection failed.",
    "http": "Model request failed.",
    "unknown": "Reply generation failed.",
}


def get_subagent_meta(state: AgentState | None) -> dict[str, Any] | None:
    if state is None:
        return None
    data = state.middle_context.get(SUBAGENT_META_KEY)
    return data if isinstance(data, dict) else None


def is_subagent_session(session: SessionRecord, state: AgentState | None = None) -> bool:
    if session.source == SessionSource.SUBAGENT:
        return True
    return get_subagent_meta(state) is not None


def is_terminal_parent_invocation_status(status: str | None) -> bool:
    return status in TERMINAL_PARENT_INVOCATION_STATUSES


def is_msg_awaiting_tool_interaction(msg: Msg | None) -> bool:
    if msg is None:
        return False
    return any(
        tool_call.state in (ToolCallState.ASKING, ToolCallState.SUBMITTED)
        for tool_call in msg.get_content_blocks("tool_call")
    )


def _has_result_content(blocks: list[TextBlock | DataBlock]) -> bool:
    for block in blocks:
        if isinstance(block, DataBlock):
            return True
        if block.text.strip():
            return True
    return False


def extract_result_blocks(
    reply_content: str | list[ContentBlock],
) -> list[TextBlock | DataBlock]:
    if isinstance(reply_content, str):
        if not reply_content.strip():
            return []
        return [TextBlock(text=reply_content)]

    extracted_blocks_reversed: list[TextBlock | DataBlock] = []
    collecting = False
    for block in reversed(reply_content):
        if isinstance(block, (TextBlock, DataBlock)):
            extracted_blocks_reversed.append(deepcopy(block))
            collecting = True
            continue
        if collecting:
            break

    extracted_blocks = list(reversed(extracted_blocks_reversed))
    if not _has_result_content(extracted_blocks):
        return []
    return extracted_blocks


def extract_run_error_text(reply_msg: Msg | None) -> str | None:
    if reply_msg is None:
        return None

    metadata = reply_msg.metadata or {}
    if metadata.get("terminal_state") != "failed":
        return None

    run_error = metadata.get("run_error")
    if not isinstance(run_error, dict):
        return None

    kind = run_error.get("kind")
    summary = run_error.get("summary")
    detail = run_error.get("detail")
    retryable = run_error.get("retryable")
    status_code = run_error.get("status_code")

    lines: list[str] = []
    if isinstance(summary, str) and summary.strip():
        lines.append(summary.strip())
    elif isinstance(kind, str) and kind in RUN_ERROR_KIND_SUMMARIES:
        lines.append(RUN_ERROR_KIND_SUMMARIES[kind])

    extra_lines: list[str] = []
    if isinstance(kind, str) and kind.strip():
        extra_lines.append(f"Kind: {kind.strip()}")
    if status_code is not None:
        extra_lines.append(f"Status code: {status_code}")
    if isinstance(detail, str) and detail.strip():
        extra_lines.append(f"Detail: {detail.strip()}")
    if isinstance(retryable, bool):
        extra_lines.append(f"Retryable: {'yes' if retryable else 'no'}")

    if extra_lines:
        if lines:
            lines.append("")
        lines.extend(extra_lines)

    if retryable is True:
        lines.extend(
            [
                "",
                "This error may be recoverable. The main agent can resume "
                "this existing child session to continue execution.",
            ],
        )

    formatted = "\n".join(lines).strip()
    return formatted or None


def root_session_id_for(session: SessionRecord, state: AgentState | None) -> str:
    meta = get_subagent_meta(state)
    if meta is not None and isinstance(meta.get("root_session_id"), str):
        return meta["root_session_id"]
    return session.id


def agent_name_for(_session: SessionRecord, state: AgentState | None) -> str:
    meta = get_subagent_meta(state)
    if meta is not None and isinstance(meta.get("path"), str):
        return meta["path"]
    return ROOT_AGENT_NAME


def agent_short_name_for(_session: SessionRecord, state: AgentState | None) -> str:
    meta = get_subagent_meta(state)
    if meta is not None and isinstance(meta.get("name"), str):
        return meta["name"]
    return ROOT_AGENT_NAME


def agent_label_for(session: SessionRecord, state: AgentState | None) -> str:
    meta = get_subagent_meta(state)
    if meta is not None and isinstance(meta.get("label"), str):
        return meta["label"]
    return session.config.name or "root agent"


def attach_subagent_meta(
    state: AgentState,
    *,
    root_session_id: str,
    parent_session_id: str,
    parent_name: str,
    name: str,
    path: str,
    label: str,
    mode: str,
    cwd: str | None = None,
) -> AgentState:
    payload: dict[str, Any] = {
        "root_session_id": root_session_id,
        "parent_session_id": parent_session_id,
        "parent_name": parent_name,
        "name": name,
        "path": path,
        "label": label,
        "mode": mode,
    }
    if cwd:
        payload["cwd"] = cwd
    state.middle_context[SUBAGENT_META_KEY] = payload
    return state


def build_task_record(
    *,
    user_id: str,
    parent_session_id: str,
    child_session_id: str,
    child_agent_id: str,
    parent_tool_call_id: str,
    parent_task_id: str | None,
    name: str,
    path: str,
    label: str,
    mode: str,
    root_session_id: str,
    parent_agent_id: str,
) -> SubAgentTaskRecord:
    now = datetime.now()
    return SubAgentTaskRecord(
        user_id=user_id,
        parent_session_id=parent_session_id,
        child_session_id=child_session_id,
        child_agent_id=child_agent_id,
        parent_tool_call_id=parent_tool_call_id,
        parent_task_id=parent_task_id,
        root_session_id=root_session_id,
        parent_agent_id=parent_agent_id,
        name=name,
        path=path,
        label=label,
        mode=mode,
        continuable=True,
        delivery="parent",
        status="running",
        open_descendant_count=0,
        terminal_reason=None,
        launch_requested_at=now,
        last_progress_at=None,
    )


def build_task_hint_block(
    *,
    parent_agent_id: str,
    parent_agent_name: str,
    parent_session_id: str,
    prompt: str,
    task_id: str,
    mode: str,
    parent_tool_call_id: str | None = None,
) -> HintBlock:
    prompt_text = prompt.rstrip()
    hint = (
        f'<subagent-task parent_agent_id="{parent_agent_id}" '
        f'parent_agent_name="{parent_agent_name}" '
        f'parent_session_id="{parent_session_id}">\n'
        f"{prompt_text}\n"
        f"</subagent-task>"
    )
    return HintBlock(
        hint=hint,
        source=json.dumps(
            {
                "label": "subagent_task",
                "sublabel": parent_agent_name,
                "parent_session_id": parent_session_id,
                "parent_tool_call_id": parent_tool_call_id,
                "mode": mode,
                "subagent_task_id": task_id,
            },
            ensure_ascii=False,
        ),
    )


def build_agent_message_hint(
    *,
    sender_name: str,
    message: str,
    task_id: str | None = None,
) -> HintBlock:
    return HintBlock(
        hint=f"Agent {sender_name} sent a message:\n\n{message.strip()}",
        source=json.dumps(
            {
                "label": "subagent_message",
                "sublabel": sender_name,
                **({"subagent_task_id": task_id} if task_id is not None else {}),
            },
            ensure_ascii=False,
        ),
    )


def settlement_summary(child_name: str, stop_reason: str, continuable: bool = True) -> str:
    subject = f"Background subagent {child_name}"
    if stop_reason == "completed":
        if continuable:
            return f"{subject} finished and will do no further work unless you send it more."
        return f"{subject} finished. It cannot receive follow-up messages."
    if stop_reason == "interrupted":
        return f"{subject} was stopped before it finished."
    if stop_reason == "max-iters":
        return f"{subject} ran out of room before it finished."
    if stop_reason == "refusal":
        return f"{subject} declined the task."
    if stop_reason == "error":
        return f"{subject} failed before it finished."
    return f"{subject} ended abnormally ({stop_reason}) before it finished."


def build_settlement_hint_content(
    *,
    child_name: str,
    stop_reason: str,
    content_blocks: list[TextBlock | DataBlock],
    continuable: bool = True,
    diagnostic: str | None = None,
) -> str | list[TextBlock | DataBlock]:
    summary = settlement_summary(
        child_name=child_name,
        stop_reason=stop_reason,
        continuable=continuable,
    )
    blocks: list[TextBlock | DataBlock] = [TextBlock(text=summary)]
    if content_blocks:
        blocks.append(TextBlock(text="Its closing message:"))
        blocks.extend(content_blocks)
    else:
        blocks.append(TextBlock(text="It left no closing message."))
    if diagnostic and diagnostic.strip():
        blocks.append(TextBlock(text=diagnostic.strip()))
    return blocks


def _extract_completed_step_blocks(msg: Msg) -> list[ContentBlock]:
    """Return the completed ReAct-step prefix within one in-flight reply.

    AgentScope stores an entire reply under one assistant message, even when
    that reply spans multiple reasoning/acting iterations. For our customized
    fork semantics, we keep only the finished step prefix inside the current
    reply: every completed tool-calling step is preserved, while the current
    in-progress step (its lead-in text, tool calls, and any later blocks) is
    excluded.
    """
    if isinstance(msg.content, str):
        return []

    last_completed_index = -1
    pending_tool_call_ids: set[str] = set()
    seen_tool_call_in_step = False
    for index, block in enumerate(msg.content):
        if isinstance(block, ToolCallBlock):
            pending_tool_call_ids.add(block.id)
            seen_tool_call_in_step = True
            continue
        if isinstance(block, ToolResultBlock) and block.id in pending_tool_call_ids:
            pending_tool_call_ids.discard(block.id)
            if seen_tool_call_in_step and not pending_tool_call_ids:
                last_completed_index = index
                seen_tool_call_in_step = False

    if last_completed_index < 0:
        return []
    return deepcopy(msg.content[: last_completed_index + 1])


def extract_completed_react_context(state: AgentState) -> list[Msg]:
    """Return fork seed context up to completed ReAct steps only.

    Previous messages are inherited as-is. Inside the current in-flight reply,
    only the prefix ending at the last finished tool-calling step is kept; the
    current step being executed is intentionally excluded.
    """
    inherited: list[Msg] = []
    for msg in state.context:
        if msg.role == "assistant" and msg.id == state.reply_id:
            completed_blocks = _extract_completed_step_blocks(msg)
            if not completed_blocks:
                continue
            inherited.append(
                msg.model_copy(
                    deep=True,
                    update={"content": completed_blocks},
                ),
            )
            continue
        inherited.append(deepcopy(msg))
    return inherited


def build_fork_child_state(
    *,
    parent_state: AgentState,
    permission_mode: str,
    root_session_id: str,
    parent_session_id: str,
    parent_name: str,
    name: str,
    path: str,
    label: str,
    cwd: str | None = None,
) -> AgentState:
    """Create a fresh child state seeded with completed ReAct steps only."""
    child_state = AgentState(
        permission_context=PermissionContext(mode=permission_mode),
    )
    child_state.summary = deepcopy(parent_state.summary)
    child_state.context = extract_completed_react_context(parent_state)
    attach_subagent_meta(
        child_state,
        root_session_id=root_session_id,
        parent_session_id=parent_session_id,
        parent_name=parent_name,
        name=name,
        path=path,
        label=label,
        mode="fork",
        cwd=cwd,
    )
    return child_state


async def load_session_state(storage: Any, user_id: str, session: SessionRecord) -> AgentState | None:
    hydrated = await storage.get_session(user_id, session.agent_id, session.id)
    return hydrated.state if hydrated is not None else None


async def load_session_current_reply(
    storage: Any,
    user_id: str,
    session: SessionRecord,
) -> Msg | None:
    if not session.state.reply_id:
        return None
    return await storage.get_message(
        user_id=user_id,
        session_id=session.id,
        message_id=session.state.reply_id,
    )


async def list_direct_subagent_children(storage: Any, user_id: str, parent_session_id: str) -> list[SessionRecord]:
    sessions = await storage.list_child_sessions(user_id, parent_session_id)
    return [session for session in sessions if session.source == SessionSource.SUBAGENT]


async def count_subagent_descendants(
    storage: Any,
    user_id: str,
    root_session_id: str,
) -> int:
    total = 0
    queue = [root_session_id]
    while queue:
        parent_session_id = queue.pop(0)
        children = await list_direct_subagent_children(storage, user_id, parent_session_id)
        total += len(children)
        queue.extend(child.id for child in children)
    return total


async def allocate_child_name(
    storage: Any,
    user_id: str,
    parent_session_id: str,
    requested_name: str | None,
) -> str:
    children = await list_direct_subagent_children(storage, user_id, parent_session_id)
    used: set[str] = set()
    for child in children:
        state = await load_session_state(storage, user_id, child)
        meta = get_subagent_meta(state)
        if meta is not None and isinstance(meta.get("name"), str):
            used.add(meta["name"])

    if requested_name:
        if requested_name == ROOT_AGENT_NAME:
            raise ValueError("'lead' is reserved for the root agent.")
        if requested_name in used:
            raise ValueError(f"Subagent name '{requested_name}' is already in use.")
        return requested_name

    index = 1
    while True:
        candidate = f"agent-{index}"
        if candidate not in used:
            return candidate
        index += 1


async def collect_subagent_catalog(
    storage: Any,
    message_bus: Any,
    user_id: str,
    root_session: SessionRecord,
) -> list[dict[str, Any]]:
    from .message_bus import MessageBusKeys

    rows: list[dict[str, Any]] = [
        {
            "name": ROOT_AGENT_NAME,
            "status": "running" if await message_bus.is_locked(MessageBusKeys.session_lock(root_session.id)) else "inactive",
            "description": "root agent",
        },
    ]

    async def _walk(parent: SessionRecord, parent_path: str | None = None) -> None:
        children = await list_direct_subagent_children(storage, user_id, parent.id)
        for child in children:
            state = await load_session_state(storage, user_id, child)
            meta = get_subagent_meta(state)
            short_name = meta.get("name") if meta and isinstance(meta.get("name"), str) else child.id
            stored_path = meta.get("path") if meta and isinstance(meta.get("path"), str) else short_name
            path = stored_path if not parent_path else f"{parent_path}/{short_name}"
            rows.append(
                {
                    "name": path,
                    "status": "running" if await message_bus.is_locked(MessageBusKeys.session_lock(child.id)) else "inactive",
                    "description": meta.get("label") if meta and isinstance(meta.get("label"), str) else child.config.name,
                },
            )
            await _walk(child, path)

    await _walk(root_session, None)
    return rows


async def is_descendant_session(
    storage: Any,
    user_id: str,
    *,
    ancestor_session_id: str,
    session: SessionRecord,
) -> bool:
    current_parent_id = session.parent_session_id
    while current_parent_id is not None:
        if current_parent_id == ancestor_session_id:
            return True
        parent = await storage.get_session_meta(user_id, current_parent_id)
        if parent is None:
            return False
        current_parent_id = parent.parent_session_id
    return False


async def resolve_tree_target(storage: Any, user_id: str, root_session: SessionRecord, name: str) -> SessionRecord | None:
    if name in {ROOT_AGENT_NAME, root_session.id}:
        return root_session

    queue = [root_session]
    seen: set[str] = set()
    while queue:
        parent = queue.pop(0)
        if parent.id in seen:
            continue
        seen.add(parent.id)
        children = await list_direct_subagent_children(storage, user_id, parent.id)
        for child in children:
            state = await load_session_state(storage, user_id, child)
            meta = get_subagent_meta(state)
            child_name = meta.get("path") if meta and isinstance(meta.get("path"), str) else child.id
            if name in {child_name, child.id}:
                return child
            queue.append(child)
    return None
