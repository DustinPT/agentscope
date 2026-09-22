# -*- coding: utf-8 -*-
"""Feishu interactive-card helpers for the tool-approval flow.

The card round-trips lookup keys (``tool_call_id``, ``chat_id`` and the
resolved ``agent_id`` / ``session_id``) plus the click's approve/deny.
For tool-specific approval UIs such as ``RequestSandboxAccess``, the
card may also carry a narrowed replacement ``tool_call.input`` selected
by the user. Resume still loads the authoritative tool call from session
state and only applies that scoped input override.
"""
import json
from typing import Any

_ACTION_TYPE = "tool_guard_approval"
_APPROVE = "approve"
_DENY = "deny"
_REQUEST_SANDBOX_ACCESS = "RequestSandboxAccess"


def _build_approval_card(
    tool_call_id: str,
    chat_id: str,
    tool_name: str,
    tool_input: str,
    agent_id: str = "",
    session_id: str = "",
) -> str:
    """Build the approval card (JSON string) for a pending tool call.

    Args:
        tool_call_id (`str`): The awaiting tool call the buttons answer.
        chat_id (`str`): Chat the card is sent to, echoed on click for
            session routing.
        tool_name (`str`): Name of the tool, shown in the card body.
        tool_input (`str`): The raw tool input string.
        agent_id (`str`): Target agent, echoed on click to resume the
            exact run without re-resolving routing.
        session_id (`str`): Target session, echoed on click alongside
            ``agent_id``.

    Returns:
        `str`: The card as a JSON string.
    """
    base = {
        "type": _ACTION_TYPE,
        "tool_call_id": tool_call_id,
        "chat_id": chat_id,
        "agent_id": agent_id,
        "session_id": session_id,
    }
    if tool_name == _REQUEST_SANDBOX_ACCESS:
        card = _build_sandbox_access_card(base, tool_input)
    else:
        summary = str(tool_input)[:800]
        body = f"**Tool:** `{tool_name}`"
        if summary:
            shown = summary if len(summary) <= 800 else summary[:799] + "…"
            body += f"\n**Arguments:** {shown}"
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "orange",
                "title": {
                    "tag": "plain_text",
                    "content": "🛡️ Tool execution needs approval",
                },
            },
            "elements": [
                {"tag": "markdown", "content": body},
                {"tag": "hr"},
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "✅ Allow"},
                            "type": "primary",
                            "value": {**base, "action": _APPROVE},
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "❌ Deny"},
                            "type": "danger",
                            "value": {**base, "action": _DENY},
                        },
                    ],
                },
            ],
        }
    return json.dumps(card, ensure_ascii=False)


def _resolved_card(approved: bool, decision_label: str = "") -> dict:
    """Build the post-decision card object that replaces the approval card.

    Args:
        approved (`bool`): The decision, selecting the colour and text.

    Returns:
        `dict`: The card object (schema 1.0).
    """
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "green" if approved else "red",
            "title": {
                "tag": "plain_text",
                "content": "✅ Allowed" if approved else "🚫 Denied",
            },
        },
        "elements": [
            {
                "tag": "markdown",
                "content": (
                    f"The tool was allowed to run.\n\n{decision_label}"
                    if approved and decision_label
                    else "The tool was allowed to run."
                    if approved
                    else "The tool was denied."
                ),
            },
        ],
    }


def _parse_action(
    value: Any,
) -> tuple[str, str, bool, str, str, str, str] | None:
    """Parse a card button's value into ``(tool_call_id, chat_id,
    approved, agent_id, session_id, tool_input_override, decision_label)``.

    Args:
        value (`Any`): The clicked button's ``value`` — a dict (or JSON
            string) carrying ``type`` / ``tool_call_id`` / ``chat_id`` /
            ``action`` / ``agent_id`` / ``session_id`` and optional
            ``tool_input_override`` / ``decision_label``.

    Returns:
        `tuple[str, str, bool, str, str, str, str] | None`:
            ``(tool_call_id, chat_id, approved, agent_id, session_id,
            tool_input_override, decision_label)`` for a valid button,
            or ``None`` if not one of ours.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(value, dict) or value.get("type") != _ACTION_TYPE:
        return None
    tool_call_id = str(value.get("tool_call_id") or "").strip()
    chat_id = str(value.get("chat_id") or "").strip()
    action = str(value.get("action") or "").strip().lower()
    if not tool_call_id or action not in (_APPROVE, _DENY):
        return None
    agent_id = str(value.get("agent_id") or "").strip()
    session_id = str(value.get("session_id") or "").strip()
    tool_input_override = str(value.get("tool_input_override") or "")
    decision_label = str(value.get("decision_label") or "").strip()
    return (
        tool_call_id,
        chat_id,
        action == _APPROVE,
        agent_id,
        session_id,
        tool_input_override,
        decision_label,
    )


def _build_toast(approved: bool) -> Any:
    """Build a toast-only card-callback response (no card update).

    Used for clicks we cannot act on (e.g. an unparseable button), where
    the card should stay as-is.

    Args:
        approved (`bool`):
            The decision, selecting the toast style and text.

    Returns:
        `Any`:
            A ``P2CardActionTriggerResponse`` when lark_oapi is
            importable, else a plain dict with the same shape.
    """
    return _wrap_response({"toast": _toast(approved)})


def _build_action_response(
    approved: bool,
    decision_label: str = "",
) -> Any:
    """Build the card-callback response for a decision: a toast plus the
    resolved card, so Feishu updates the clicked card in place — no
    separate, rate-limit-racing PATCH.

    Args:
        approved (`bool`): The decision, selecting toast and card.

    Returns:
        `Any`: A ``P2CardActionTriggerResponse`` (or dict fallback).
    """
    return _wrap_response(
        {
            "toast": _toast(approved),
            "card": {
                "type": "raw",
                "data": _resolved_card(approved, decision_label),
            },
        },
    )


def _toast(approved: bool) -> dict:
    """The toast payload for a decision.

    Args:
        approved (`bool`): The decision, selecting style and text.
    """
    return {
        "type": "success" if approved else "info",
        "content": "Allowed" if approved else "Denied",
    }


def _build_sandbox_access_card(base: dict[str, str], tool_input: str) -> dict:
    """Build the Feishu approval card for ``RequestSandboxAccess``."""
    parsed = _parse_sandbox_access_input(tool_input)
    pattern = parsed.get("pattern") or "the requested target"
    resource_type = parsed.get("resource_type") or "path"
    reason = str(parsed.get("reason") or "").strip()

    if resource_type == "domain":
        request_summary = f"Request permission to connect to domain `{pattern}`."
        action_rows = [
            _build_sandbox_scope_actions(base, parsed, operations=["connect"]),
            _build_deny_row(base),
        ]
    else:
        request_summary = f"Request sandbox access to `{pattern}`."
        action_rows = [
            _build_sandbox_scope_actions(base, parsed, operations=["read"]),
            _build_sandbox_scope_actions(
                base,
                parsed,
                operations=["read", "write"],
            ),
            _build_deny_row(base),
        ]

    body_lines = [
        request_summary,
        "Choose the smallest permission that will work.",
    ]
    if reason:
        body_lines.append(f"Reason: {reason}")

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange",
            "title": {
                "tag": "plain_text",
                "content": "🛡️ Sandbox access requires approval",
            },
        },
        "elements": [
            {"tag": "markdown", "content": "\n\n".join(body_lines)},
            {"tag": "hr"},
            *action_rows,
        ],
    }


def _parse_sandbox_access_input(tool_input: str) -> dict[str, Any]:
    """Parse the sandbox access tool input JSON."""
    try:
        parsed = json.loads(tool_input)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _build_sandbox_scope_actions(
    base: dict[str, str],
    raw_input: dict[str, Any],
    *,
    operations: list[str],
) -> dict[str, Any]:
    """Build one button row for sandbox approvals of one operation mode."""
    has_write = "write" in operations
    if raw_input.get("resource_type") == "domain":
        button_defs = [
            ("This workspace", "workspace"),
            ("This agent", "agent"),
            ("This user", "user"),
        ]
    else:
        suffix = "read/write" if has_write else "read-only"
        button_defs = [
            (f"Workspace {suffix}", "workspace"),
            (f"Agent {suffix}", "agent"),
            (f"User {suffix}", "user"),
        ]

    actions = []
    for label, scope in button_defs:
        next_input = dict(raw_input)
        next_input["scope"] = scope
        next_input["operations"] = operations
        actions.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": label},
                "type": "primary" if scope == "workspace" else "default",
                "value": {
                    **base,
                    "action": _APPROVE,
                    "tool_input_override": json.dumps(
                        next_input,
                        ensure_ascii=False,
                    ),
                    "decision_label": f"Approved as {label}.",
                },
            },
        )
    return {"tag": "action", "actions": actions}


def _build_deny_row(base: dict[str, str]) -> dict[str, Any]:
    """Build the deny button row."""
    return {
        "tag": "action",
        "actions": [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "❌ Deny"},
                "type": "danger",
                "value": {**base, "action": _DENY},
            },
        ],
    }


def _wrap_response(body: dict) -> Any:
    """Wrap a response body in ``P2CardActionTriggerResponse`` when the
    SDK is importable, else return the plain dict.

    Args:
        body (`dict`): The callback response body (``toast`` / ``card``).
    """
    try:
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            P2CardActionTriggerResponse,
        )

        return P2CardActionTriggerResponse(body)
    except (ImportError, AttributeError):
        return body
