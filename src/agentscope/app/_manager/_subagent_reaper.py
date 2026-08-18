# -*- coding: utf-8 -*-
"""Background watchdog that closes stalled child-session invocations."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json
from typing import Self

from ..._logging import logger
from ...message import HintBlock
from ..message_bus import MessageBus
from ..middleware._subagent_result_middleware import (
    _build_error_hint_content,
    is_msg_awaiting_tool_interaction,
    is_terminal_parent_invocation_status,
    load_session_current_reply,
)
from ..storage import StorageBase, SubAgentTaskRecord


class SubAgentReaper:
    """Periodically scan active child invocations and synthesize failures."""

    _SCAN_INTERVAL_SECS = 10.0
    _WAIT_GRACE_TIMEOUT_SECS = 60.0

    def __init__(
        self,
        storage: StorageBase,
        message_bus: MessageBus,
    ) -> None:
        """Bind dependencies."""
        self._storage = storage
        self._bus = message_bus
        self._task: asyncio.Task | None = None

    async def __aenter__(self) -> Self:
        """Start the background scan loop."""
        self._task = asyncio.create_task(
            self._loop(),
            name="subagent-reaper",
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Stop the background scan loop."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self) -> None:
        """Run the periodic scan until cancelled."""
        while True:
            try:
                await self._reap_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # pylint: disable=broad-except
                logger.exception("SubAgentReaper: scan failed.")
            await asyncio.sleep(self._SCAN_INTERVAL_SECS)

    async def _reap_once(self) -> None:
        """Scan all active child invocations once."""
        for user_id, task_id in await self._storage.list_active_subagent_tasks():
            await self._check_active_task(user_id=user_id, task_id=task_id)

    async def _check_active_task(self, *, user_id: str, task_id: str) -> None:
        """Check one active delegated task and close it if it stalled."""
        task = await self._storage.get_subagent_task(user_id, task_id)
        if task is None:
            return
        if is_terminal_parent_invocation_status(task.status):
            await self._storage.unmark_active_subagent_task(
                user_id,
                task.child_session_id,
                task.id,
            )
            return
        if await self._bus.session_is_running(task.child_session_id):
            return
        if await self._bus.has_pending_wakeup(task.child_session_id):
            return

        reference_time = task.last_progress_at or task.launch_requested_at
        if reference_time is None:
            return
        if datetime.now() - reference_time < timedelta(
            seconds=self._WAIT_GRACE_TIMEOUT_SECS,
        ):
            return
        if task.open_descendant_count != 0:
            return

        child_session = await self._storage.get_session(
            user_id,
            task.child_agent_id,
            task.child_session_id,
        )
        if child_session is None:
            await self._persist_reaped_state(
                user_id=user_id,
                task=task,
                reason="reaped_missing_child_session",
            )
            return

        current_reply = await load_session_current_reply(
            self._storage,
            user_id,
            child_session,
        )
        if is_msg_awaiting_tool_interaction(current_reply):
            return

        await self._deliver_reaper_error(
            user_id=user_id,
            task=task,
            child_session_name=child_session.config.name,
        )

    async def _decrement_parent_task_descendant_count(
        self,
        *,
        user_id: str,
        task: SubAgentTaskRecord,
    ) -> None:
        """Close out one descendant slot on the direct parent task."""
        if not task.parent_task_id:
            return
        parent_task = await self._storage.get_subagent_task(
            user_id,
            task.parent_task_id,
        )
        if parent_task is None:
            return
        if parent_task.open_descendant_count <= 0:
            return
        parent_task.open_descendant_count -= 1
        parent_task.last_progress_at = datetime.now()
        await self._storage.upsert_subagent_task(user_id, parent_task)

    async def _persist_reaped_state(
        self,
        *,
        user_id: str,
        task: SubAgentTaskRecord,
        reason: str,
    ) -> None:
        """Persist the synthesized terminal error state on one task."""
        task.status = "error_delivered"
        task.terminal_reason = reason
        task.last_progress_at = datetime.now()
        await self._storage.upsert_subagent_task(user_id, task)
        await self._storage.unmark_active_subagent_task(
            user_id,
            task.child_session_id,
            task.id,
        )

    async def _deliver_reaper_error(
        self,
        *,
        user_id: str,
        task: SubAgentTaskRecord,
        child_session_name: str,
    ) -> None:
        """Send one synthetic terminal error for a stalled child invocation."""
        if is_terminal_parent_invocation_status(task.status):
            await self._storage.unmark_active_subagent_task(
                user_id,
                task.child_session_id,
                task.id,
            )
            return

        parent_session = await self._storage.get_session_meta(
            user_id,
            task.parent_session_id,
        )
        child_agent = await self._storage.get_agent(user_id, task.child_agent_id)
        if parent_session is None or child_agent is None:
            await self._persist_reaped_state(
                user_id=user_id,
                task=task,
                reason="reaped_without_parent",
            )
            return

        await self._decrement_parent_task_descendant_count(
            user_id=user_id,
            task=task,
        )

        hint = HintBlock(
            hint=_build_error_hint_content(
                agent_id=task.child_agent_id,
                agent_name=child_agent.data.name,
                session_id=task.child_session_id,
                session_name=child_session_name,
                error_text=(
                    "The child session finished running but did not produce "
                    "a final reply."
                ),
            ),
            source=json.dumps(
                {
                    "label": "subagent_error",
                    "sublabel": child_agent.data.name,
                    "session_id": task.child_session_id,
                    "session_name": child_session_name,
                    "state": "error",
                    "subagent_task_id": task.id,
                },
                ensure_ascii=False,
            ),
        )
        await self._bus.inbox_push(parent_session.id, hint.model_dump(mode="json"))
        await self._bus.enqueue_wakeup(
            user_id=user_id,
            session_id=parent_session.id,
            agent_id=parent_session.agent_id,
        )
        await self._persist_reaped_state(
            user_id=user_id,
            task=task,
            reason="reaped_stalled_child",
        )
