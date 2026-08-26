# -*- coding: utf-8 -*-
"""Cross-resource session lifecycle service.

Owns the "stop in-flight runs + delete records + drop bus state"
cascades that ``DELETE /sessions/{sid}``, ``DELETE /agents/{aid}``,
``DELETE /schedules/{sid}`` and the agent-facing
:class:`~agentscope.app._tools.TeamDelete` /
:class:`~agentscope.app._manager._scheduler._tools.ScheduleDelete`
tools all share.

Layering
========

Methods deliberately delegate down the cascade so the bus-touching
logic lives in exactly one place — :meth:`delete_session`. Higher-level
methods only orchestrate which sessions to delete, then ask storage
to clean its own non-session scope (records, indexes, back-refs).

::

    delete_session    ← atomic: cancel run, storage.delete_session,
                        bus.session_purge
        │
    delete_team       → service.delete_agent per worker
                      → storage.delete_team    (record + leader detach)
        │
    delete_agent      → service.delete_session per session
                      → service.delete_schedule per owned schedule
                      → storage.delete_agent   (agent record + team back-refs)
        │
    delete_schedule   → service.delete_session per spawned session
                      → storage.delete_schedule (schedule record + indexes)

Storage's own internal cascades (e.g.
``storage.delete_agent`` re-iterating sessions) become idempotent
no-ops because the records are already gone — they still execute, but
do no work and never touch the bus, so the storage layer stays
unaware of the message bus.

Separation of concerns
======================

Storage and message bus are treated as distinct backends — they may
live in different databases in the future. The service is the **only**
component that touches both in the same call. Storage code never
imports the bus; bus code never imports storage.
"""
import asyncio
import uuid
from typing import TYPE_CHECKING

from fastapi import HTTPException

from ..message_bus import MessageBus, MessageBusKeys
from ..storage import StorageBase
from ..workspace_manager import WorkspaceManagerBase
from ..._logging import logger
from ...state import AgentState
from ._attachment_store import AttachmentStore

if TYPE_CHECKING:
    from .._manager import ChatRunRegistry
    from ._chat import ChatService


class SessionService:
    """Cancel in-flight chat runs and cascade-delete related records.

    The cancel side broadcasts via
    :meth:`MessageBus.session_publish_cancel`, then polls
    :meth:`MessageBus.session_is_running` until the run-lock clears or
    a timeout expires — so the implementation is multi-process and
    multi-node by construction.

    Args:
        storage (`StorageBase`):
            Persistent storage backend. Owns durable records and their
            cascades among themselves.
        message_bus (`MessageBus`):
            Live message bus. Owns transient per-session state (events
            log, inbox, run-lock, cancel channel).
    """

    _CANCEL_POLL_INTERVAL_SECS: float = 0.1
    """Interval between :meth:`MessageBus.session_is_running` polls
    while waiting for a cancelled run to release its distributed
    run-lock."""

    def __init__(
        self,
        storage: StorageBase,
        message_bus: MessageBus,
        workspace_manager: WorkspaceManagerBase,
        attachment_store: AttachmentStore,
        chat_service: "ChatService | None" = None,
        chat_run_registry: "ChatRunRegistry | None" = None,
    ) -> None:
        """Bind dependencies.

        Args:
            storage (`StorageBase`): Persistent storage backend.
            message_bus (`MessageBus`): Live message bus.
        """
        self._storage = storage
        self._bus = message_bus
        self._workspace_manager = workspace_manager
        self._attachment_store = attachment_store
        self._chat_service = chat_service
        self._chat_run_registry = chat_run_registry

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    async def cancel_session_run(
        self,
        session_id: str,
        *,
        timeout: float = 10.0,
    ) -> bool:
        """Broadcast an internal hard-cancel and wait for run-lock release.

        Args:
            session_id (`str`):
                The session whose chat run + BG tasks should be
                cancelled.
            timeout (`float`, defaults to ``10.0``):
                Maximum seconds to wait for the chat-run lock to
                release. On timeout the method returns ``False`` so
                callers can proceed (e.g. with cascade delete) instead
                of hanging on a process that may have died.

        Returns:
            `bool`:
                ``True`` if the chat-run lock was confirmed released
                within ``timeout`` seconds (or was never held).
                ``False`` if the lock was still held when the timeout
                expired.
        """
        return await self._cancel_single_session_run(
            session_id,
            timeout=timeout,
        )

    async def _cancel_single_session_run(
        self,
        session_id: str,
        *,
        timeout: float,
    ) -> bool:
        """Broadcast a single session cancel and wait for its run-lock."""
        was_running = await self._bus.is_locked(
            MessageBusKeys.session_lock(session_id),
        )
        await self._bus.publish(
            MessageBusKeys.session_cancel_channel(),
            {"session_id": session_id},
        )
        if not was_running:
            return True
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            if not await self._bus.is_locked(
                MessageBusKeys.session_lock(session_id),
            ):
                return True
            if asyncio.get_event_loop().time() >= deadline:
                logger.warning(
                    "Session %s did not release its run-lock within "
                    "%.1fs after cancel; proceeding anyway.",
                    session_id,
                    timeout,
                )
                return False
            await asyncio.sleep(self._CANCEL_POLL_INTERVAL_SECS)

    # ------------------------------------------------------------------
    # Delete cascades — every higher-level method delegates to
    # ``delete_session`` so the cancel + bus-purge logic exists in
    # exactly one place.
    # ------------------------------------------------------------------

    async def delete_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> bool:
        """Cancel, delete and bus-purge a single session.

        This is the atomic primitive — every other cascade delegates
        here for per-session work.

        Steps:

        1. Cancel any in-flight run for ``session_id`` (cross-process
           via the bus cancel channel).
        2. Delete the session record (and its storage-side cascade:
           message log, schedule-session index, team dissolution when
           this session leads one — recursive into worker agents).
        3. Purge transient bus state for ``session_id`` (events log,
           inbox).

        Worker sessions that storage cascades through are picked up
        here too: when this session is a team leader,
        ``storage.delete_session`` calls ``storage.delete_team`` →
        ``storage.delete_agent`` → ``storage.delete_session`` for each
        worker, and we mirror that on the bus side by purging worker
        sessions identified up front via
        :meth:`_team_worker_session_ids`.

        Args:
            user_id (`str`): The owner user id.
            agent_id (`str`): The agent that owns the session.
            session_id (`str`): The session to delete.

        Returns:
            `bool`:
                ``True`` if the session record existed and was deleted,
                ``False`` otherwise. Mirrors
                :meth:`StorageBase.delete_session`.
        """
        # Identify all bus-purge targets before storage mutates anything.
        worker_sids = await self._team_worker_session_ids(
            user_id,
            agent_id,
            session_id,
        )
        descendant_sessions = await self._descendant_sessions(
            user_id,
            session_id,
        )
        session = await self._storage.get_session_meta(user_id, session_id)
        if session is not None and session.agent_id != agent_id:
            session = None
        descendant_sids = [session.id for session in descendant_sessions]
        all_sids = list(
            dict.fromkeys([session_id, *worker_sids, *descendant_sids]),
        )

        await self._cancel_runs(all_sids)
        for descendant in reversed(descendant_sessions):
            await self._delete_session_attachments(
                user_id,
                descendant,
            )
            await self._storage.delete_session(
                user_id,
                descendant.agent_id,
                descendant.id,
            )
        if session is not None:
            await self._delete_session_attachments(user_id, session)
        deleted = await self._storage.delete_session(
            user_id,
            agent_id,
            session_id,
        )
        await self._purge_bus(all_sids)
        return deleted

    async def delete_team(self, user_id: str, team_id: str) -> bool:
        """Cancel, delete and bus-purge a team.

        Delegates worker dissolution to :meth:`delete_agent` (one call
        per ``member_id``) so the per-session cancel + bus purge runs
        for each worker. The leader's own session is **not** deleted —
        teams dissolve, leaders survive (and have their ``team_id``
        cleared by ``storage.delete_team``).

        Args:
            user_id (`str`): The owner user id.
            team_id (`str`): The team to dissolve.

        Returns:
            `bool`:
                ``True`` if the team record existed and was deleted.
        """
        team = await self._storage.get_team(user_id, team_id)
        if team is None:
            # Still call storage.delete_team so it can clean any index
            # residue, but the return value will be False.
            return await self._storage.delete_team(user_id, team_id)

        for member_id in team.data.member_ids:
            await self.delete_agent(user_id, member_id)

        # storage.delete_team will iterate member_ids again to delete
        # each worker agent — those calls are now no-ops because the
        # agents are already gone, leaving only the leader-detach and
        # team-record cleanup work.
        return await self._storage.delete_team(user_id, team_id)

    async def delete_agent(self, user_id: str, agent_id: str) -> bool:
        """Cancel, delete and bus-purge every session and schedule
        owned by an agent, then drop the agent record.

        Delegates per-session work to :meth:`delete_session` and
        per-schedule work to :meth:`delete_schedule`, then asks
        storage to clean the remaining agent-scoped state (the agent
        record, the agent index entry, and any team back-references).

        Args:
            user_id (`str`): The owner user id.
            agent_id (`str`): The agent to delete.

        Returns:
            `bool`:
                ``True`` if the agent record existed and was deleted.
        """
        for session in await self._storage.list_sessions(user_id, agent_id):
            await self.delete_session(user_id, agent_id, session.id)

        for schedule in await self._storage.list_schedules(user_id):
            if schedule.agent_id == agent_id:
                await self.delete_schedule(user_id, schedule.id)

        # storage.delete_agent re-iterates sessions and schedules —
        # those re-runs are idempotent no-ops because the records were
        # already removed above. What remains is the agent record,
        # the agent index entry, and team back-reference scrubbing.
        return await self._storage.delete_agent(user_id, agent_id)

    async def delete_schedule(
        self,
        user_id: str,
        schedule_id: str,
    ) -> bool:
        """Cancel, delete and bus-purge every session spawned by a
        schedule, then drop the schedule record.

        Args:
            user_id (`str`): The owner user id.
            schedule_id (`str`): The schedule to delete.

        Returns:
            `bool`:
                ``True`` if the schedule record existed and was deleted.
        """
        for session in await self._storage.list_sessions_by_schedule(
            user_id,
            schedule_id,
        ):
            await self.delete_session(
                user_id,
                session.agent_id,
                session.id,
            )

        # storage.delete_schedule re-iterates the same sessions —
        # idempotent no-ops; only schedule record + indexes remain.
        return await self._storage.delete_schedule(user_id, schedule_id)

    async def rollback_to_before_message(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        message_id: str,
    ) -> tuple:
        """Rollback the current session to the state before a user message."""
        session = await self._storage.get_session_meta(user_id, session_id)
        if session is not None and session.agent_id != agent_id:
            session = None
        if session is None:
            raise HTTPException(
                status_code=404,
                detail=f"Session '{session_id}' not found.",
            )

        await self.cancel_session_run(session_id, timeout=10.0)
        messages = await self._list_all_messages(user_id, session_id)

        target_index = -1
        target_message = None
        for index, message in enumerate(messages):
            if message.id == message_id:
                target_index = index
                target_message = message
                break

        if target_message is None:
            raise HTTPException(
                status_code=404,
                detail=f"Message '{message_id}' not found in session.",
            )
        if target_message.role != "user":
            raise HTTPException(
                status_code=400,
                detail="Rollback is only supported for user messages.",
            )

        snapshot = target_message.metadata.get("rollback_snapshot")
        if not isinstance(snapshot, dict):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Rollback is not supported for this historical message "
                    "because no rollback snapshot was stored."
                ),
            )

        restored_state = AgentState.model_validate(
            {
                **snapshot,
                "session_id": session_id,
                "cur_iter": 0,
                "reply_id": uuid.uuid4().hex,
            },
        )
        retained_messages = messages[:target_index]

        await self._prune_session_attachments(
            user_id=user_id,
            session=session,
            kept_message_ids={message.id for message in retained_messages},
        )
        await self._storage.replace_messages(
            user_id=user_id,
            session_id=session_id,
            messages=retained_messages,
        )
        await self._storage.update_session_state(
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            state=restored_state,
        )
        await self._bus.log_trim(MessageBusKeys.session_events(session_id))
        await self._bus.queue_delete(MessageBusKeys.inbox(session_id))
        await self._bus.registry_drop(MessageBusKeys.bg_tasks(session_id))

        updated_session = await self._storage.get_session_meta(
            user_id,
            session_id,
        )
        if updated_session is not None and updated_session.agent_id != agent_id:
            updated_session = None
        if updated_session is None:
            raise HTTPException(
                status_code=404,
                detail=f"Session '{session_id}' not found after rollback.",
            )
        return updated_session, target_message, len(retained_messages)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _resolve_workspace_for_session(
        self,
        user_id: str,
        session,
    ):
        """Return the current runtime workspace for one session."""
        return await self._workspace_manager.get_workspace(
            user_id,
            session.agent_id,
            session.id,
            session.config.workspace_id,
        )

    async def _delete_session_attachments(
        self,
        user_id: str,
        session,
    ) -> None:
        """Delete all persisted attachments for one session."""
        workspace = await self._resolve_workspace_for_session(user_id, session)
        await self._attachment_store.delete_session_attachments(
            workspace,
            session_id=session.id,
        )

    async def _prune_session_attachments(
        self,
        *,
        user_id: str,
        session,
        kept_message_ids: set[str],
    ) -> None:
        """Delete attachments that no longer belong to retained messages."""
        workspace = await self._resolve_workspace_for_session(user_id, session)
        await self._attachment_store.prune_session_attachments(
            workspace,
            session_id=session.id,
            kept_message_ids=kept_message_ids,
        )

    async def _team_worker_session_ids(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> list[str]:
        """Return the session ids of every worker in the team that
        ``session_id`` leads, or ``[]`` when the session does not lead
        a team.

        Mirrors :meth:`StorageBase.delete_session`'s own team-leader
        cascade so the bus side can purge the same sessions.

        Args:
            user_id (`str`): The owner user id.
            agent_id (`str`):
                The agent that owns ``session_id``. May be empty when
                unknown; team-leader lookup does not depend on it.
            session_id (`str`):
                The candidate leader session.

        Returns:
            `list[str]`:
                Worker session ids, empty when this session is not a
                team leader.
        """
        session = await self._storage.get_session_meta(user_id, session_id)
        if session is not None and agent_id and session.agent_id != agent_id:
            session = None
        if session is None or not session.team_id:
            return []
        team = await self._storage.get_team(user_id, session.team_id)
        if team is None or team.session_id != session_id:
            return []
        sids: list[str] = []
        for member_id in team.data.member_ids:
            worker_sessions = await self._storage.list_sessions(
                user_id,
                member_id,
            )
            sids.extend(s.id for s in worker_sessions)
        return sids

    async def _team_worker_sessions(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> list:
        """Return worker session records for the team led by ``session_id``."""
        session = await self._storage.get_session_meta(user_id, session_id)
        if session is not None and agent_id and session.agent_id != agent_id:
            session = None
        if session is None or not session.team_id:
            return []
        team = await self._storage.get_team(user_id, session.team_id)
        if team is None or team.session_id != session_id:
            return []
        worker_sessions = []
        for member_id in team.data.member_ids:
            worker_sessions.extend(
                await self._storage.list_sessions(user_id, member_id),
            )
        return worker_sessions

    async def _descendant_sessions(
        self,
        user_id: str,
        parent_session_id: str,
    ) -> list:
        """Return every descendant session under ``parent_session_id``.

        Sessions are returned parent-first so callers can reverse the list
        when they need a safe leaf-first delete order.
        """
        descendants = []
        direct_children = await self._storage.list_child_sessions(
            user_id,
            parent_session_id,
        )
        for child in direct_children:
            descendants.append(child)
            descendants.extend(
                await self._descendant_sessions(user_id, child.id),
            )
        return descendants

    async def _list_all_messages(
        self,
        user_id: str,
        session_id: str,
        *,
        batch_size: int = 200,
    ) -> list:
        """Fetch the full persisted message list for a session."""
        messages = []
        offset = 0
        while True:
            batch = await self._storage.list_messages(
                user_id,
                session_id,
                offset=offset,
                limit=batch_size,
            )
            if not batch:
                break
            messages.extend(batch)
            if len(batch) < batch_size:
                break
            offset += len(batch)
        return messages

    async def _cancel_runs(self, session_ids: list[str]) -> None:
        """Cancel every in-flight run in ``session_ids`` concurrently.

        Args:
            session_ids (`list[str]`):
                Sessions whose runs should be cancelled.
        """
        if not session_ids:
            return
        await asyncio.gather(
            *(self.cancel_session_run(sid) for sid in session_ids),
        )

    async def _purge_bus(self, session_ids: list[str]) -> None:
        """Drop bus state (events log + inbox) for each id concurrently.

        Args:
            session_ids (`list[str]`):
                Sessions whose bus state should be purged.
        """
        if not session_ids:
            return
        await asyncio.gather(
            *(
                self._bus.log_trim(MessageBusKeys.session_events(sid))
                for sid in session_ids
            ),
            *(self._bus.queue_delete(MessageBusKeys.inbox(sid)) for sid in session_ids),
            *(self._bus.registry_drop(MessageBusKeys.bg_tasks(sid)) for sid in session_ids),
        )
