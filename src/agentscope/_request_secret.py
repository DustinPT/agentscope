# -*- coding: utf-8 -*-
"""Helpers for RequestSecret submission, storage, and redaction."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .app.storage import StorageBase

REQUEST_SECRET_TOOL_NAME = "RequestSecret"
REQUEST_SECRET_SUBMISSION_KEY = "request_secret_submission"
REQUEST_SECRET_SUCCESS_OUTPUT = (
    "Secrets captured and bound to this session's Bash environment."
)


def is_request_secret_tool_name(tool_name: str) -> bool:
    """Whether a tool name refers to RequestSecret."""
    return tool_name == REQUEST_SECRET_TOOL_NAME


def is_request_secret_result(
    tool_name: str,
    metadata: dict[str, Any] | None,
) -> bool:
    """Whether one external result carries a RequestSecret submission."""
    return is_request_secret_tool_name(tool_name) and isinstance(
        (metadata or {}).get(REQUEST_SECRET_SUBMISSION_KEY),
        dict,
    )


def _parse_request_secret_submission(
    metadata: dict[str, Any] | None,
) -> tuple[dict[str, str], int]:
    """Extract env bindings and ttl from an untrusted submission payload."""
    payload = (metadata or {}).get(REQUEST_SECRET_SUBMISSION_KEY)
    if not isinstance(payload, dict):
        raise ValueError("missing request secret submission payload")

    raw_bindings = payload.get("bindings")
    if not isinstance(raw_bindings, list) or not raw_bindings:
        raise ValueError("request secret submission must include bindings")

    env_bindings: dict[str, str] = {}
    for raw_binding in raw_bindings:
        if not isinstance(raw_binding, dict):
            raise ValueError("invalid request secret binding entry")
        env_var_name = str(
            raw_binding.get("env_var_name") or raw_binding.get("key") or "",
        ).strip()
        secret_value = str(
            raw_binding.get("secret_value") or raw_binding.get("value") or "",
        )
        if not env_var_name:
            raise ValueError("request secret binding is missing env_var_name")
        if not secret_value:
            raise ValueError(f"secret value for {env_var_name} cannot be empty")
        env_bindings[env_var_name] = secret_value

    ttl_raw = payload.get("ttl_minutes", 30)
    try:
        ttl_minutes = int(ttl_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("ttl_minutes must be an integer") from exc
    if ttl_minutes <= 0:
        raise ValueError("ttl_minutes must be positive")

    return env_bindings, ttl_minutes


def build_request_secret_safe_metadata(
    *,
    env_bindings: dict[str, str],
    expires_at: datetime,
) -> dict[str, Any]:
    """Build the safe metadata returned to the model."""
    return {
        "bound_env_vars": sorted(env_bindings.keys()),
        "binding_scope": "session",
        "expires_at": expires_at.isoformat(),
    }


async def sanitize_request_secret_result(
    *,
    storage: StorageBase,
    user_id: str,
    session_id: str,
    tool_call_id: str,
    tool_name: str,
    output: str,
    metadata: dict[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    """Persist a RequestSecret submission and remove cleartext from output."""
    if not is_request_secret_result(tool_name, metadata):
        return output, metadata or {}

    env_bindings, ttl_minutes = _parse_request_secret_submission(metadata)
    expires_at = datetime.now() + timedelta(minutes=ttl_minutes)
    await storage.upsert_session_secret(
        user_id=user_id,
        session_id=session_id,
        env_bindings=env_bindings,
        expires_at=expires_at,
        source_tool_call_id=tool_call_id,
    )
    return REQUEST_SECRET_SUCCESS_OUTPUT, build_request_secret_safe_metadata(
        env_bindings=env_bindings,
        expires_at=expires_at,
    )


async def get_session_secret_env_map(
    storage: StorageBase,
    *,
    user_id: str,
    session_id: str,
) -> dict[str, str]:
    """Load the currently active env bindings for one session."""
    record = await storage.get_session_secret(
        user_id=user_id,
        session_id=session_id,
    )
    if record is None:
        return {}

    now = datetime.now()
    active = {
        env_var_name: binding.secret_value
        for env_var_name, binding in record.bindings.items()
        if binding.expires_at > now and binding.secret_value
    }
    if len(active) != len(record.bindings):
        await storage.cleanup_expired_session_secret(
            user_id=user_id,
            session_id=session_id,
        )
    return active


def redact_text_by_secret_values(
    text: str,
    secret_values: list[str],
) -> str:
    """Replace exact secret values with asterisks in output text."""
    redacted = text
    for secret_value in sorted(
        {value for value in secret_values if value},
        key=len,
        reverse=True,
    ):
        redacted = redacted.replace(secret_value, "***")
    return redacted
