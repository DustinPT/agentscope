# -*- coding: utf-8 -*-
"""Feishu interactive-card helpers for channel HITL flows.

The card round-trips lookup keys (``tool_call_id``, ``chat_id`` and the
resolved ``agent_id`` / ``session_id``) plus the click's approve/deny.
For tool-specific approval UIs such as ``RequestSandboxAccess``, the
card may also carry a narrowed replacement ``tool_call.input`` selected
by the user. Resume still loads the authoritative tool call from session
state and only applies that scoped input override.
"""
import json
from typing import Any

from ....message import ToolResultState
from ....tool import AskUserAnswer, AskUserMetadata, AskUserParams

_ACTION_TYPE = "tool_guard_approval"
_APPROVE = "approve"
_DENY = "deny"
_EXTERNAL_SUBMIT = "external_submit"
_REQUEST_SANDBOX_ACCESS = "RequestSandboxAccess"
_ASK_USER = "AskUser"


def _build_approval_card(
    tool_call_id: str,
    chat_id: str,
    tool_name: str,
    tool_input: str,
    agent_id: str = "",
    session_id: str = "",
    reply_id: str = "",
    header_title: str | None = None,
    body_intro: str | None = None,
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
        "reply_id": reply_id,
        "interaction_kind": "confirm",
    }
    if tool_name == _REQUEST_SANDBOX_ACCESS:
        card = _build_sandbox_access_card(
            base,
            tool_input,
            header_title=header_title,
            body_intro=body_intro,
        )
    else:
        summary = str(tool_input)[:800]
        body = f"**Tool:** `{tool_name}`"
        if summary:
            shown = summary if len(summary) <= 800 else summary[:799] + "…"
            body += f"\n**Arguments:** {shown}"
        if body_intro:
            body = f"{body_intro}\n\n{body}"
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "orange",
                "title": {
                    "tag": "plain_text",
                    "content": header_title or "🛡️ Tool execution needs approval",
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


def _build_external_execution_card(
    tool_call_id: str,
    chat_id: str,
    reply_id: str,
    tool_name: str,
    tool_input: str,
    agent_id: str = "",
    session_id: str = "",
    header_title: str | None = None,
    body_intro: str | None = None,
) -> str:
    """Build the external-execution card for AskUser."""
    if tool_name != _ASK_USER:
        raise ValueError(f"Unsupported external tool card: {tool_name}")
    base = {
        "type": _ACTION_TYPE,
        "tool_call_id": tool_call_id,
        "chat_id": chat_id,
        "agent_id": agent_id,
        "session_id": session_id,
        "reply_id": reply_id,
        "interaction_kind": "external_execution",
        "tool_name": tool_name,
        "tool_input": tool_input,
    }
    return json.dumps(
        _build_ask_user_card(
            base,
            tool_input,
            header_title=header_title,
            body_intro=body_intro,
        ),
        ensure_ascii=False,
    )


def _resolved_confirm_card(
    approved: bool,
    decision_label: str = "",
) -> dict:
    """Build the post-decision confirm card object (schema 1.0).

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


def _resolved_external_execution_card(
    approved: bool,
    decision_label: str = "",
) -> dict:
    """Build the post-submit external execution card object (schema 2.0)."""
    if approved:
        content = "The answers were submitted."
        if decision_label:
            content = f"{content}\n\n{decision_label}"
        template = "green"
        title = "Answers submitted"
    else:
        content = "The submission was rejected."
        if decision_label:
            content = f"{content}\n\n{decision_label}"
        template = "red"
        title = "Submission rejected"
    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {
            "template": template,
            "title": {
                "tag": "plain_text",
                "content": title,
            },
        },
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": content,
                },
            ],
        },
    }


def _parse_action(action: Any) -> dict[str, Any] | None:
    """Parse a card action into a normalised channel interaction payload."""
    form_values = _extract_form_values(action)
    value = _extract_action_value(action)
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
    if not tool_call_id or action not in (_APPROVE, _DENY, _EXTERNAL_SUBMIT):
        return None
    agent_id = str(value.get("agent_id") or "").strip()
    session_id = str(value.get("session_id") or "").strip()
    reply_id = str(value.get("reply_id") or "").strip()
    tool_input_override = str(value.get("tool_input_override") or "")
    decision_label = str(value.get("decision_label") or "").strip()
    if action == _EXTERNAL_SUBMIT:
        tool_name = str(value.get("tool_name") or "").strip()
        tool_input = str(value.get("tool_input") or "")
        if tool_name == _ASK_USER:
            output, metadata, state = _parse_ask_user_submission(
                tool_input,
                form_values,
            )
        else:
            output, metadata, state = ("", {}, ToolResultState.ERROR)
        return {
            "tool_call_id": tool_call_id,
            "chat_id": chat_id,
            "approved": True,
            "agent_id": agent_id,
            "session_id": session_id,
            "reply_id": reply_id,
            "tool_input_override": "",
            "decision_label": decision_label,
            "interaction_kind": "external_execution",
            "tool_output": output,
            "tool_result_state": state,
            "tool_result_metadata": metadata,
        }
    return {
        "tool_call_id": tool_call_id,
        "chat_id": chat_id,
        "approved": action == _APPROVE,
        "agent_id": agent_id,
        "session_id": session_id,
        "reply_id": reply_id,
        "tool_input_override": tool_input_override,
        "decision_label": decision_label,
        "interaction_kind": "confirm",
        "tool_output": "",
        "tool_result_state": ToolResultState.SUCCESS,
        "tool_result_metadata": {},
    }


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
    interaction_kind: str = "confirm",
) -> Any:
    """Build the card-callback response for a decision: a toast plus the
    resolved card, so Feishu updates the clicked card in place — no
    separate, rate-limit-racing PATCH.

    Args:
        approved (`bool`): The decision, selecting toast and card.

    Returns:
        `Any`: A ``P2CardActionTriggerResponse`` (or dict fallback).
    """
    card_data = (
        _resolved_external_execution_card(approved, decision_label)
        if interaction_kind == "external_execution"
        else _resolved_confirm_card(approved, decision_label)
    )
    return _wrap_response(
        {
            "toast": _toast(approved),
            "card": {
                "type": "raw",
                "data": card_data,
            },
        },
    )


def _extract_action_value(action: Any) -> Any:
    """Return the raw action value payload from a callback object."""
    if hasattr(action, "value"):
        return getattr(action, "value")
    return action


def _extract_form_values(action: Any) -> dict[str, Any]:
    """Return submitted form values from a callback object when present."""
    candidates = [
        getattr(action, "form_value", None),
        getattr(action, "formValue", None),
    ]
    for candidate in candidates:
        if isinstance(candidate, str):
            try:
                candidate = json.loads(candidate)
            except (json.JSONDecodeError, TypeError):
                candidate = None
        if isinstance(candidate, dict):
            return candidate
    return {}


def _build_ask_user_card(
    base: dict[str, str],
    tool_input: str,
    *,
    header_title: str | None = None,
    body_intro: str | None = None,
) -> dict[str, Any]:
    """Build one AskUser submission card."""
    try:
        params = AskUserParams.model_validate_json(tool_input)
    except Exception as exc:  # noqa: BLE001
        return {
            "schema": "2.0",
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "red",
                "title": {
                    "tag": "plain_text",
                    "content": header_title or "AskUser submission failed",
                },
            },
            "body": {
                "elements": [
                    {
                        "tag": "markdown",
                        "content": (
                            f"{body_intro}\n\nInvalid AskUser input: {exc}"
                            if body_intro
                            else f"Invalid AskUser input: {exc}"
                        ),
                    },
                ],
            },
        }

    form_elements: list[dict[str, Any]] = []

    for index, question in enumerate(params.questions):
        title = f"**{question.header}**\n{question.question}"
        form_elements.append({"tag": "markdown", "content": title})
        if question.context:
            form_elements.append(
                {"tag": "markdown", "content": question.context},
            )

        option_lines = [
            f"- **{option.label}**: {option.description}"
            for option in question.options
        ]
        if option_lines:
            form_elements.append(
                {
                    "tag": "markdown",
                    "content": "\n".join(option_lines),
                },
            )

        if question.multi_select:
            form_elements.append(
                {
                    "tag": "multi_select_static",
                    "name": f"q{index}_selected",
                    "required": False,
                    "placeholder": {
                        "tag": "plain_text",
                        "content": "Select one or more options",
                    },
                    "options": [
                        {
                            "text": {
                                "tag": "plain_text",
                                "content": option.label,
                            },
                            "value": option.label,
                        }
                        for option in question.options
                    ],
                },
            )
        else:
            form_elements.append(
                {
                    "tag": "select_static",
                    "name": f"q{index}_selected",
                    "required": False,
                    "placeholder": {
                        "tag": "plain_text",
                        "content": "Select an option",
                    },
                    "options": [
                        {
                            "text": {
                                "tag": "plain_text",
                                "content": option.label,
                            },
                            "value": option.label,
                        }
                        for option in question.options
                    ],
                },
            )
        form_elements.append(
            {
                "tag": "input",
                "name": f"q{index}_other",
                "required": False,
                "input_type": "multiline_text",
                "rows": 2,
                "auto_resize": True,
                "placeholder": {
                    "tag": "plain_text",
                    "content": "Optional custom answer (overrides options)",
                },
            },
        )
        form_elements.append(
            {
                "tag": "markdown",
                "content": (
                    "Custom answers are mutually exclusive with the "
                    "provided options."
                ),
            },
        )
        if index < len(params.questions) - 1:
            form_elements.append({"tag": "hr"})

    form_elements.extend(
        [
            {
                "tag": "column_set",
                "columns": [
                    {
                        "tag": "column",
                        "width": "auto",
                        "elements": [
                            {
                                "tag": "button",
                                "name": "ask_user_submit",
                                "text": {
                                    "tag": "plain_text",
                                    "content": "Submit answers",
                                },
                                "type": "primary",
                                "form_action_type": "submit",
                                "behaviors": [
                                    {
                                        "type": "callback",
                                        "value": {
                                            **base,
                                            "action": _EXTERNAL_SUBMIT,
                                        },
                                    },
                                ],
                            },
                        ],
                    },
                    {
                        "tag": "column",
                        "width": "auto",
                        "elements": [
                            {
                                "tag": "button",
                                "name": "ask_user_reset",
                                "text": {
                                    "tag": "plain_text",
                                    "content": "Reset",
                                },
                                "type": "default",
                                "form_action_type": "reset",
                            },
                        ],
                    },
                ],
            },
        ],
    )

    body_elements: list[dict[str, Any]] = [
        {
            "tag": "markdown",
            "content": body_intro or "Please answer the questions below.",
        },
        {"tag": "hr"},
        {
            "tag": "form",
            "name": "ask_user_form",
            "elements": form_elements,
        },
    ]

    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {
                "tag": "plain_text",
                "content": header_title or "User input required",
            },
        },
        "body": {
            "elements": body_elements,
        },
    }


def _parse_ask_user_submission(
    tool_input: str,
    form_values: dict[str, Any],
) -> tuple[str, dict[str, Any], ToolResultState]:
    """Convert one AskUser form submission into output + metadata."""
    try:
        params = AskUserParams.model_validate_json(tool_input)
    except Exception as exc:  # noqa: BLE001
        return str(exc), AskUserMetadata(answers=[]).model_dump(mode="json"), ToolResultState.ERROR

    answers: list[AskUserAnswer] = []
    for index, question in enumerate(params.questions):
        raw_selected = form_values.get(f"q{index}_selected")
        if isinstance(raw_selected, list):
            selected = [str(item) for item in raw_selected if str(item).strip()]
        elif raw_selected is None or raw_selected == "":
            selected = []
        else:
            selected = [str(raw_selected)]
        other_raw = form_values.get(f"q{index}_other")
        other = str(other_raw).strip() if other_raw is not None else ""
        if other:
            selected = []
        answers.append(
            AskUserAnswer(
                question=question.question,
                selected=selected,
                other=other or None,
            ),
        )

    metadata = AskUserMetadata(answers=answers).model_dump(mode="json")
    lines = []
    for answer in answers:
        value = answer.other or ", ".join(answer.selected)
        lines.append(f"{answer.question}\n{value}")
    return "\n\n".join(lines), metadata, ToolResultState.SUCCESS


def _toast(approved: bool) -> dict:
    """The toast payload for a decision.

    Args:
        approved (`bool`): The decision, selecting style and text.
    """
    return {
        "type": "success" if approved else "info",
        "content": "Allowed" if approved else "Denied",
    }


def _build_sandbox_access_card(
    base: dict[str, str],
    tool_input: str,
    *,
    header_title: str | None = None,
    body_intro: str | None = None,
) -> dict:
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
    elif resource_type == "unix_socket":
        request_summary = (
            f"Request permission to connect to Unix socket `{pattern}`."
        )
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
    if body_intro:
        body_lines.insert(0, body_intro)
    if reason:
        body_lines.append(f"Reason: {reason}")

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange",
            "title": {
                "tag": "plain_text",
                "content": (
                    header_title or "🛡️ Sandbox access requires approval"
                ),
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
    elif raw_input.get("resource_type") == "unix_socket":
        button_defs = [
            ("Workspace connect", "workspace"),
            ("Agent connect", "agent"),
            ("User connect", "user"),
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
