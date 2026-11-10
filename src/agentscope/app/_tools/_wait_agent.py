# -*- coding: utf-8 -*-
"""DSH-style wait_agent tool for the calling agent's next-step inbox."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import Field

from ..message_bus import MessageBusKeys
from ...message import ToolResultState
from ...state import AgentState
from ...tool import ParamsBase
from ._session_tool_base import _SessionToolBase

_WAIT_POLL_INTERVAL_SECONDS = 0.5
_DEFAULT_TIMEOUT_MS = 30_000
_MIN_TIMEOUT_MS = 10_000
_MAX_TIMEOUT_MS = 3_600_000


class _WaitAgentParams(ParamsBase):
    timeout_ms: int = Field(
        default=_DEFAULT_TIMEOUT_MS,
        description=(
            "Wait duration in milliseconds, from 10000 through 3600000. "
            "Defaults to 30000."
        ),
    )


class WaitAgent(_SessionToolBase):
    """Wait for input arriving at the calling agent's own inbox."""

    name = "wait_agent"
    description = (
        "Wait for input at your next step, including agent messages, completion "
        "notifications, and user steering. Returns immediately if new input is "
        "pending. `timedOut: false` means new input is pending. Pending input is "
        "delivered to the model after code execution finishes."
    )
    input_schema: dict[str, Any] = _WaitAgentParams.model_json_schema()
    is_read_only = True
    is_state_injected = True

    async def _peek_inbox_signatures(self) -> list[str]:
        """Read current inbox payload signatures without consuming them."""
        inbox_key = MessageBusKeys.inbox(self._session_id)
        lock_key = MessageBusKeys.inbox_lock(self._session_id)
        async with self._message_bus.acquire_lock(
            lock_key,
            ttl_secs=MessageBusKeys.INBOX_LOCK_TTL_SECS,
        ):
            entries: list[tuple[str, dict]] = []
            while True:
                batch = await self._message_bus.queue_drain(inbox_key, max_count=100)
                if not batch:
                    break
                entries.extend(batch)

            for _entry_id, payload in entries:
                await self._message_bus.queue_push(inbox_key, payload)

        return [
            json.dumps(payload, ensure_ascii=False, sort_keys=True)
            for _entry_id, payload in entries
        ]

    async def call(
        self,
        timeout_ms: int = _DEFAULT_TIMEOUT_MS,
        _agent_state: AgentState | None = None,
    ):
        if (
            not isinstance(timeout_ms, int)
            or timeout_ms < _MIN_TIMEOUT_MS
            or timeout_ms > _MAX_TIMEOUT_MS
        ):
            return self._result(
                {
                    "error": (
                        "timeout_ms must be an integer from 10000 through 3600000"
                    ),
                    "code": "WAIT_INVALID_TIMEOUT",
                },
                state=ToolResultState.ERROR,
            )

        runtime_context = (
            _agent_state.tool_context.runtime_context if _agent_state is not None else None
        )
        if runtime_context is None or not runtime_context.current_tool_call_id:
            return self._result(
                {
                    "error": "wait_agent requires a calling agent",
                    "code": "WAIT_UNAVAILABLE",
                },
                state=ToolResultState.ERROR,
            )

        tool_call_id = runtime_context.current_tool_call_id
        deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)

        while True:
            session = await self._get_owned_session(
                agent_id=self._agent_id,
                session_id=self._session_id,
            )
            if session is None:
                return self._result(
                    {
                        "error": (
                            "wait_agent ended because the calling agent is unavailable"
                        ),
                        "code": "WAIT_UNAVAILABLE",
                    },
                    state=ToolResultState.ERROR,
                )

            pending_signatures = await self._peek_inbox_signatures()
            previously_reported = set(
                _agent_state.tool_context.wait_agent_reported_inputs.get(tool_call_id, [])
                if _agent_state is not None
                else []
            )
            if pending_signatures and any(
                signature not in previously_reported
                for signature in pending_signatures
            ):
                if _agent_state is not None:
                    _agent_state.tool_context.wait_agent_reported_inputs[tool_call_id] = (
                        list(pending_signatures)
                    )
                return self._result({"timedOut": False})

            if asyncio.get_running_loop().time() >= deadline:
                return self._result({"timedOut": True})

            await asyncio.sleep(_WAIT_POLL_INTERVAL_SECONDS)
