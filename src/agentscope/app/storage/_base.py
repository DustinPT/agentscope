# -*- coding: utf-8 -*-
# pylint: disable=too-many-public-methods
"""The storage base class."""
from abc import ABC, abstractmethod
from typing import Any, Self


from ._model import (
    AgentRecord,
    ChannelRecord,
    CredentialRecord,
    ScheduleRecord,
    SessionConfig,
    SessionRecord,
    SessionSource,
    SessionWithState,
    SubAgentTaskRecord,
    TeamRecord,
    UserRecord,
)
from ...credential import CredentialBase
from ...message import Msg
from ...state import AgentState


class StorageBase(ABC):
    """The storage abstract base class."""

    async def __aenter__(self) -> Self:
        """Start the storage backend (open connection pool, etc.)."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: Any,
    ) -> None:
        """Shut down the storage backend."""
        await self.aclose()

    async def aclose(self) -> None:
        """Release underlying connection resources. Default is a no-op."""

    @abstractmethod
    async def get_user(self, user_id: str) -> UserRecord | None:
        """Fetch the persisted settings record for one user.

        Args:
            user_id (`str`):
                The user id.

        Returns:
            `UserRecord | None`:
                The stored user settings record, or ``None`` if the user has
                not saved any settings yet.
        """

    @abstractmethod
    async def upsert_user(
        self,
        user_id: str,
        user_record: UserRecord,
    ) -> UserRecord:
        """Create or update the persisted settings record for one user.

        Args:
            user_id (`str`):
                The user id.
            user_record (`UserRecord`):
                The settings record to store.

        Returns:
            `UserRecord`:
                The stored user settings record.
        """

    @abstractmethod
    async def upsert_credential(
        self,
        user_id: str,
        credential_data: CredentialBase,
    ) -> str:
        """Create or update a credential in the storage.

        Args:
            user_id (`str`):
                The user id.
            credential_data (`CredentialBase`):
                The credential data.

        Returns:
            `str`:
                The credential id.
        """

    @abstractmethod
    async def list_credentials(self, user_id: str) -> list[CredentialRecord]:
        """List all credentials for a given user.

        Args:
            user_id (`str`):
                The user id.

        Returns:
            `list[CredentialRecord]`:
                List of all credentials for a given user.
        """

    @abstractmethod
    async def get_credential(
        self,
        user_id: str,
        credential_id: str,
    ) -> CredentialRecord | None:
        """Fetch a single credential record by id.

        Args:
            user_id (`str`): The owner user id.
            credential_id (`str`): The credential id.

        Returns:
            `CredentialRecord | None`: The record, or ``None`` if not found.
        """

    @abstractmethod
    async def delete_credential(
        self,
        user_id: str,
        credential_id: str,
    ) -> bool:
        """Delete a credential.

        Args:
            user_id (`str`):
                The user id.
            credential_id (`str`):
                The credential id.

        Returns:
            `bool`:
                True if deleted, False if not found.
        """

    @abstractmethod
    async def upsert_agent(
        self,
        user_id: str,
        agent_record: AgentRecord,
    ) -> str:
        """Create an agent record in the storage.

        Args:
            user_id (`str`):
                The user id.
            agent_record (`AgentRecord`):
                The agent record.

        Returns:
            `str`:
                The agent id.
        """

    @abstractmethod
    async def list_agents(self, user_id: str) -> list[AgentRecord]:
        """List all agents for a given user.

        Args:
            user_id (`str`):
                The user id.

        Returns:
            `list[AgentRecord]`:
                List of all agents for a given user.
        """

    @abstractmethod
    async def get_agent(
        self,
        user_id: str,
        agent_id: str,
    ) -> AgentRecord | None:
        """Fetch a single agent record by id.

        Args:
            user_id (`str`): The owner user id.
            agent_id (`str`): The agent id.

        Returns:
            `AgentRecord | None`: The record, or ``None`` if not found.
        """

    @abstractmethod
    async def delete_agent(self, user_id: str, agent_id: str) -> bool:
        """Delete an agent record.

        Args:
            user_id (`str`):
                The user id.
            agent_id (`str`):
                The agent id.

        Returns:
            `bool`:
                True if deleted, False if not found.
        """

    @abstractmethod
    async def upsert_session(
        self,
        user_id: str,
        agent_id: str,
        config: SessionConfig,
        state: AgentState | None = None,
        session_id: str | None = None,
        source: SessionSource = SessionSource.USER,
        source_schedule_id: str | None = None,
        source_chat_id: str | None = None,
        source_chat_name: str | None = None,
        source_channel_id: str | None = None,
        conversation_kind: str | None = None,
        parent_session_id: str | None = None,
    ) -> SessionWithState:
        """Create or update a session for a (user, agent) pair.

        Args:
            user_id (`str`): The owner user id.
            agent_id (`str`): The agent id.
            config (`SessionConfig`): Immutable session configuration
                (model, workspace). Required on create; passed unchanged on
                state-only updates.
            state (`AgentState | None`, optional): Runtime state to persist.
                Defaults to a fresh ``AgentState()`` when ``None``.
            session_id (`str | None`, optional): If provided, update the
                existing session with this id. If ``None``, create a new
                session.
            source (`SessionSource`, optional): The source that created this
                session. Defaults to ``SessionSource.USER``.
            source_schedule_id (`str | None`, optional): The schedule that
                created this session. When set, the session is indexed under
                the schedule for execution history queries.
            source_chat_id (`str | None`, optional): The platform chat this
                session maps to when created by a channel.
            source_chat_name (`str | None`, optional): That chat's title, as
                supplied by the channel when available.
            source_channel_id (`str | None`, optional): The owning channel
                id when the session came from a channel.
            conversation_kind (`str | None`, optional): The audience shape
                of the session, e.g. ``group`` or ``private``.
            parent_session_id (`str | None`, optional): Parent session id
                when this session is spawned as a child session.

        Returns:
            `SessionWithState`: The created or updated full session.
        """

    @abstractmethod
    async def set_session_team_id(
        self,
        user_id: str,
        session_id: str,
        team_id: str | None,
    ) -> None:
        """Set or clear ``team_id`` on an existing session record.

        Bypasses :meth:`upsert_session` because that method does not
        write ``team_id``. Used by team operations (create/dissolve/
        leave) to keep the leader/worker → team relationship consistent.
        Idempotent: a no-op if the session does not exist or already
        holds the given value.

        Args:
            user_id (`str`):
                The owner user id.
            session_id (`str`):
                The session whose ``team_id`` should be updated.
            team_id (`str | None`):
                The new value. ``None`` detaches the session from any
                team.
        """

    @abstractmethod
    async def update_session_state(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        state: AgentState,
    ) -> None:
        """Update only the mutable state of an existing session.

        Convenience method for the hot path (post-chat-turn persistence).
        Raises ``KeyError`` if the session does not exist.

        Args:
            user_id (`str`): The owner user id.
            agent_id (`str`): The agent id.
            session_id (`str`): The session id.
            state (`AgentState`): The new agent state to persist.
        """

    @abstractmethod
    async def get_session_meta(
        self,
        user_id: str,
        session_id: str,
    ) -> SessionRecord | None:
        """Fetch one lightweight session record by id."""

    @abstractmethod
    async def get_session_state(
        self,
        user_id: str,
        session_id: str,
    ) -> AgentState | None:
        """Fetch one persisted session state snapshot by session id."""

    @abstractmethod
    async def get_sessions_meta_by_ids(
        self,
        user_id: str,
        session_ids: list[str],
    ) -> list[SessionRecord]:
        """Fetch lightweight session records for the given ids."""

    @abstractmethod
    async def list_sessions(
        self,
        user_id: str,
        agent_id: str,
    ) -> list[SessionRecord]:
        """List all sessions for a given user and agent entity.

        Args:
            user_id (`str`): The user id.
            agent_id (`str`): The agent id.

        Returns:
            `list[SessionRecord]`: List of all sessions for the (user, agent).
        """

    @abstractmethod
    async def list_child_sessions(
        self,
        user_id: str,
        parent_session_id: str,
    ) -> list[SessionRecord]:
        """List all direct child sessions for a parent session.

        Args:
            user_id (`str`): The owner user id.
            parent_session_id (`str`): Parent session id.

        Returns:
            `list[SessionRecord]`: Direct child sessions ordered by creation
            time descending.
        """

    @abstractmethod
    async def upsert_subagent_task(
        self,
        user_id: str,
        record: SubAgentTaskRecord,
    ) -> str:
        """Create or update one delegated sub-agent task record."""

    @abstractmethod
    async def get_subagent_task(
        self,
        user_id: str,
        task_id: str,
    ) -> SubAgentTaskRecord | None:
        """Fetch one delegated sub-agent task record by id."""

    @abstractmethod
    async def get_active_subagent_task_by_child_session(
        self,
        user_id: str,
        child_session_id: str,
    ) -> SubAgentTaskRecord | None:
        """Fetch the active delegated task bound to one child session."""

    @abstractmethod
    async def mark_active_subagent_task(
        self,
        user_id: str,
        child_session_id: str,
        task_id: str,
    ) -> None:
        """Mark one delegated task as the active task for a child session."""

    @abstractmethod
    async def unmark_active_subagent_task(
        self,
        user_id: str,
        child_session_id: str,
        task_id: str,
    ) -> None:
        """Remove one delegated task from the active task indexes."""

    @abstractmethod
    async def list_active_subagent_tasks(self) -> list[tuple[str, str]]:
        """List ``(user_id, task_id)`` pairs with active child invocations."""

    @abstractmethod
    async def delete_subagent_task(
        self,
        user_id: str,
        task_id: str,
    ) -> bool:
        """Delete one delegated sub-agent task record."""

    @abstractmethod
    async def delete_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> bool:
        """Delete a session.

        Args:
            user_id (`str`): The user id.
            agent_id (`str`): The agent id.
            session_id (`str`): The session id.

        Returns:
            `bool`: True if deleted, False if not found.
        """

    @abstractmethod
    async def get_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> SessionWithState | None:
        """Fetch a single session record by id.

        Args:
            user_id (`str`): The owner user id.
            agent_id (`str`): The agent id.
            session_id (`str`): The session id.

        Returns:
            `SessionWithState | None`: The hydrated record, or ``None`` if not
            found.
        """

    @abstractmethod
    async def list_sessions_by_schedule(
        self,
        user_id: str,
        schedule_id: str,
    ) -> list[SessionRecord]:
        """Return all sessions created by a given schedule.

        Args:
            user_id (`str`): The owner user id.
            schedule_id (`str`): The schedule id.

        Returns:
            `list[SessionRecord]`: Sessions triggered by this schedule,
            ordered by creation time (newest first).
        """

    @abstractmethod
    async def list_sessions_by_channel(
        self,
        user_id: str,
        channel_id: str,
    ) -> list[SessionRecord]:
        """Return all sessions derived from a given channel.

        Args:
            user_id (`str`): The owner user id.
            channel_id (`str`): The channel id.

        Returns:
            `list[SessionRecord]`: Sessions the channel spawned, ordered by
            creation time (newest first).
        """

    @abstractmethod
    async def upsert_schedule(
        self,
        user_id: str,
        record: ScheduleRecord,
    ) -> str:
        """Persist a cron task record and register it in the user's index.

        Args:
            user_id (`str`): The owner user id.
            record (`ScheduleRecord`): The fully-populated record to store.

        Returns:
            `str`: The id of the stored record.
        """

    @abstractmethod
    async def get_schedule(
        self,
        user_id: str,
        schedule_id: str,
    ) -> ScheduleRecord | None:
        """Fetch a single cron task record by id.

        Args:
            user_id (`str`): The owner user id.
            schedule_id (`str`): The task id.

        Returns:
            `ScheduleRecord | None`: The record, or ``None`` if not found.
        """

    @abstractmethod
    async def list_schedules(
        self,
        user_id: str,
    ) -> list[ScheduleRecord]:
        """Return all cron task records belonging to the given user.

        Args:
            user_id (`str`): The owner user id.

        Returns:
            `list[ScheduleRecord]`: All cron task records for the user.
        """

    @abstractmethod
    async def delete_schedule(
        self,
        user_id: str,
        schedule_id: str,
    ) -> bool:
        """Delete a cron task record and remove it from the user's index.

        Args:
            user_id (`str`): The owner user id.
            schedule_id (`str`): The id of the task to delete.

        Returns:
            `bool`: ``True`` if deleted, ``False`` if not found.
        """

    @abstractmethod
    async def list_all_schedules(self) -> list[ScheduleRecord]:
        """Return every schedule record across all users.

        Used on startup to restore the in-memory scheduler from persisted
        state.  Normal per-user listing should use :meth:`list_schedules`.

        Returns:
            `list[ScheduleRecord]`: All schedule records in the store.
        """

    # ------------------------------------------------------------------
    # Channel persistence
    # ------------------------------------------------------------------

    @abstractmethod
    async def upsert_channel(
        self,
        record: ChannelRecord,
        platform_bot_id: str,
    ) -> str:
        """Persist a channel record and refresh its indexes."""

    @abstractmethod
    async def get_channel(
        self,
        channel_id: str,
    ) -> ChannelRecord | None:
        """Fetch a channel record by its global id."""

    @abstractmethod
    async def list_channels(self, user_id: str) -> list[ChannelRecord]:
        """Return all channel records owned by the given user."""

    @abstractmethod
    async def list_all_channels(self) -> list[ChannelRecord]:
        """Return every channel record across all users."""

    @abstractmethod
    async def delete_channel(
        self,
        channel_id: str,
        platform_bot_id: str,
    ) -> bool:
        """Delete a channel record and clean up all indexes."""

    @abstractmethod
    async def get_channel_id_by_platform_bot_id(
        self,
        platform_bot_id: str,
    ) -> str | None:
        """Return the channel id bound to a platform bot, if any."""

    # ------------------------------------------------------------------
    # Message persistence
    # ------------------------------------------------------------------

    @abstractmethod
    async def upsert_message(
        self,
        user_id: str,
        session_id: str,
        msg: Msg,
    ) -> None:
        """Persist a message to the session's message list.

        If the last message in the list has the same ``id`` as *msg*, it is
        replaced (merge/overwrite for the same reply_id across continuation
        calls).  Otherwise, *msg* is appended as a new entry.

        Args:
            user_id (`str`): The owner user id.
            session_id (`str`): The session id.
            msg (`Msg`): The message to persist.
        """

    @abstractmethod
    async def get_message(
        self,
        user_id: str,
        session_id: str,
        message_id: str,
    ) -> Msg | None:
        """Fetch a single message by id from the session's message list.

        Args:
            user_id (`str`): The owner user id.
            session_id (`str`): The session id.
            message_id (`str`): The message id to look up.

        Returns:
            `Msg | None`: The message, or ``None`` if not found.
        """

    @abstractmethod
    async def list_messages(
        self,
        user_id: str,
        session_id: str,
        offset: int = 0,
        limit: int = 50,
    ) -> list[Msg]:
        """Return messages for a session with pagination.

        Args:
            user_id (`str`): The owner user id.
            session_id (`str`): The session id.
            offset (`int`): Starting index (0-based). Defaults to 0.
            limit (`int`): Maximum number of messages to return.

        Returns:
            `list[Msg]`: Messages in chronological order.
        """

    @abstractmethod
    async def replace_messages(
        self,
        user_id: str,
        session_id: str,
        messages: list[Msg],
    ) -> None:
        """Replace the full persisted message list for a session.

        Args:
            user_id (`str`): The owner user id.
            session_id (`str`): The session id.
            messages (`list[Msg]`): Full replacement message list in
                chronological order.
        """

    # ------------------------------------------------------------------
    # Team persistence
    # ------------------------------------------------------------------

    @abstractmethod
    async def upsert_team(
        self,
        user_id: str,
        record: TeamRecord,
    ) -> TeamRecord:
        """Create or update a team record.

        Args:
            user_id (`str`): The owner user id.
            record (`TeamRecord`): The team record to persist. The record's
                ``id`` is used as the primary key; if a record with the same
                id already exists it is overwritten.

        Returns:
            `TeamRecord`: The stored record (with ``updated_at`` refreshed).
        """

    @abstractmethod
    async def get_team(
        self,
        user_id: str,
        team_id: str,
    ) -> TeamRecord | None:
        """Fetch a single team record by id.

        Args:
            user_id (`str`): The owner user id.
            team_id (`str`): The team id.

        Returns:
            `TeamRecord | None`: The record, or ``None`` if not found.
        """

    @abstractmethod
    async def list_teams(self, user_id: str) -> list[TeamRecord]:
        """List all teams owned by a given user.

        Args:
            user_id (`str`): The user id.

        Returns:
            `list[TeamRecord]`: All team records belonging to the user.
        """

    @abstractmethod
    async def delete_team(self, user_id: str, team_id: str) -> bool:
        """Delete a team record and cascade-delete all of its workers.

        The cascade mirrors SQL's ``ON DELETE CASCADE`` semantics:

        1. For each ``member_id`` in :attr:`TeamData.member_ids`, call
           :meth:`delete_agent` (which cascades that worker's session).
        2. Clear ``team_id`` on the leader session referenced by
           :attr:`TeamRecord.session_id` (``ON DELETE SET NULL`` for the
           leader's back-reference to the team). Idempotent if the
           session has already been deleted.
        3. Delete the :class:`TeamRecord` key and the per-user team
           index entry.

        Args:
            user_id (`str`):
                The owner user id.
            team_id (`str`):
                The id of the team to delete.

        Returns:
            `bool`:
                ``True`` if the team record existed and was deleted,
                ``False`` if not found.
        """
