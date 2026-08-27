# -*- coding: utf-8 -*-
# pylint: disable=too-many-public-methods
"""The Redis storage implementation."""

from datetime import datetime
from typing import Any, TYPE_CHECKING, Self

from pydantic import BaseModel

from ._base import StorageBase
from ._model import (
    AgentRecord,
    ChannelRecord,
    CredentialRecord,
    ScheduleRecord,
    SubAgentTaskRecord,
    SessionConfig,
    SessionRecord,
    SessionSource,
    SessionStateRecord,
    SessionWithState,
    TeamRecord,
    UserRecord,
)
from ._utils import _dump_with_secrets
from ...credential import CredentialBase
from ...message import Msg
from ...state import AgentState

if TYPE_CHECKING:
    from redis.asyncio import ConnectionPool, Redis
else:
    ConnectionPool = Any
    Redis = Any


class RedisStorage(StorageBase):
    """The Redis storage implementation."""

    class KeyConfig(BaseModel):
        """Key templates for all Redis keys used by :class:`RedisStorage`.

        Nested on :class:`RedisStorage` because customising key prefixes
        is meaningful only for this backend; users tweak them via
        ``RedisStorage(key_config=RedisStorage.KeyConfig(...))``.
        """

        # Record keys
        user: str = "agentscope:user:{user_id}:profile"
        credential: str = (
            "agentscope:user:{user_id}:credential:{credential_id}"
        )
        agent: str = "agentscope:user:{user_id}:agent:{agent_id}"
        session: str = "agentscope:user:{user_id}:session:{session_id}"
        session_state: str = (
            "agentscope:user:{user_id}:session_state:{session_id}"
        )
        subagent_task: str = (
            "agentscope:user:{user_id}:subagent_task:{task_id}"
        )

        # Index keys (Redis Sets — store all IDs for a given scope)
        credential_index: str = "agentscope:user:{user_id}:credentials"
        agent_index: str = "agentscope:user:{user_id}:agents"
        session_index: str = (
            "agentscope:user:{user_id}:agent:{agent_id}:sessions"
        )
        session_children: str = (
            "agentscope:user:{user_id}:session_children:{parent_session_id}"
        )
        active_subagent_tasks: str = "agentscope:active_subagent_tasks"
        active_subagent_task_by_child_session: str = (
            "agentscope:user:{user_id}:session:{child_session_id}:"
            "active_subagent_task"
        )

        # Lookup key: maps (user_id, agent_id) → session_id
        session_lookup: str = (
            "agentscope:user:{user_id}:agent:{agent_id}:session"
        )

        # Message list key (Redis List — ordered message history per session)
        messages: str = (
            "agentscope:user:{user_id}:session:{session_id}:messages"
        )

        schedule: str = "agentscope:user:{user_id}:schedule:{schedule_id}"
        schedule_index: str = "agentscope:user:{user_id}:schedules"
        schedule_global_index: str = "agentscope:schedules"
        schedule_session_index: str = (
            "agentscope:user:{user_id}:schedule:{schedule_id}:sessions"
        )
        channel: str = "agentscope:channel_record:{channel_id}"
        channel_index: str = "agentscope:user:{user_id}:channels"
        channel_all_index: str = "agentscope:channels"
        channel_botid_index: str = "agentscope:channel_botid:{platform_bot_id}"
        channel_session_index: str = (
            "agentscope:user:{user_id}:channel:{channel_id}:sessions"
        )

        team: str = "agentscope:user:{user_id}:team:{team_id}"
        team_index: str = "agentscope:user:{user_id}:teams"

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: str | None = None,
        connection_pool: ConnectionPool | None = None,
        key_ttl: int | None = None,
        key_config: "RedisStorage.KeyConfig | None" = None,
        **kwargs: Any,
    ) -> None:
        """Store connection parameters; the actual pool is created in
        :meth:`__aenter__`.

        Args:
            host (`str`, defaults to `"localhost"`): Redis server host.
            port (`int`, defaults to `6379`): Redis server port.
            db (`int`, defaults to `0`): Redis database index.
            password (`str | None`, optional): Redis password if required.
            connection_pool (`ConnectionPool | None`, optional):
                An externally managed connection pool.  When provided the pool
                is used as-is and **not** closed by :meth:`aclose` — the
                caller retains ownership of its lifecycle.  When omitted a
                pool is created from *host*/*port*/*db*/*password* on
                :meth:`__aenter__` and closed on :meth:`aclose`.
                Extra ``**kwargs`` (e.g. ``max_connections``) are forwarded to
                the pool constructor only when the pool is created internally.
            key_ttl (`int | None`, optional):
                Expire time in seconds for record keys. Refreshed on every
                write (sliding TTL). If `None`, keys do not expire.
            key_config (`RedisStorage.KeyConfig | None`, optional):
                Key template configuration. Defaults to
                ``RedisStorage.KeyConfig()``.
            **kwargs (`Any`):
                Extra keyword arguments forwarded to
                ``redis.asyncio.ConnectionPool`` when the pool is created
                internally (e.g. ``max_connections=20``, ``socket_timeout=5``).
        """
        self._host = host
        self._port = port
        self._db = db
        self._password = password
        self._external_pool: ConnectionPool | None = connection_pool
        self._kwargs = kwargs
        self.key_ttl = key_ttl
        self.key_config = key_config or RedisStorage.KeyConfig()

        # Populated in __aenter__; None until the context is entered.
        self._client: Redis | None = None
        self._owned_pool: ConnectionPool | None = None

    def _key(self, template: str, **kwargs: str) -> str:
        """Format a key template with the given keyword arguments."""
        return template.format(**kwargs)

    @staticmethod
    def _encode_active_subagent_task_member(user_id: str, task_id: str) -> str:
        """Encode one active delegated task member for the global index."""
        return f"{user_id}:{task_id}"

    @staticmethod
    def _decode_active_subagent_task_member(
        member: str,
    ) -> tuple[str, str] | None:
        """Decode one active delegated task member from the global index."""
        user_id, separator, task_id = member.partition(":")
        if not separator or not user_id or not task_id:
            return None
        return user_id, task_id

    async def _set_with_ttl(self, key: str, value: str) -> None:
        """SET a key and optionally apply the sliding TTL."""
        await self._client.set(key, value)
        await self._refresh_key_ttl(key)

    async def _refresh_key_ttl(self, key: str) -> None:
        """Apply the sliding TTL to a key, if configured."""
        if self.key_ttl is not None:
            await self._client.expire(key, self.key_ttl)

    async def __aenter__(self) -> Self:
        """Create the connection pool and Redis client.

        If an external pool was supplied at construction time it is used
        directly and its lifecycle remains the caller's responsibility.
        Otherwise, an internal pool is created from the stored host/port/db
        parameters and will be closed by :meth:`aclose`.
        """
        try:
            import redis.asyncio as aioredis
        except ImportError as e:
            raise ImportError(
                "The 'redis' package is required for RedisStorage. "
                "Install it with: pip install redis[async]",
            ) from e

        if self._external_pool is not None:
            pool = self._external_pool
        else:
            self._owned_pool = aioredis.ConnectionPool(
                host=self._host,
                port=self._port,
                db=self._db,
                password=self._password,
                decode_responses=True,
                **self._kwargs,
            )
            pool = self._owned_pool

        self._client = aioredis.Redis(connection_pool=pool)
        return self

    async def aclose(self) -> None:
        """Close the connection pool if it was created internally.

        Externally supplied pools are left open — the caller owns them.
        """
        if self._owned_pool is not None:
            await self._owned_pool.aclose()
            self._owned_pool = None
        self._client = None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: Any,
    ) -> None:
        """Exit the async context manager."""
        await self.aclose()

    def get_client(self) -> Redis:
        """Get the underlying Redis client instance."""
        return self._client

    async def get_user(self, user_id: str) -> UserRecord | None:
        """Fetch the persisted settings record for one user."""
        key = self._key(self.key_config.user, user_id=user_id)
        raw = await self._client.get(key)
        return UserRecord.model_validate_json(raw) if raw else None

    async def upsert_user(
        self,
        user_id: str,
        user_record: UserRecord,
    ) -> UserRecord:
        """Create or update the persisted settings record for one user."""
        key = self._key(self.key_config.user, user_id=user_id)
        raw = await self._client.get(key)
        if raw:
            record = UserRecord.model_validate_json(raw)
            record.global_default_models = user_record.global_default_models
            record.updated_at = datetime.now()
        else:
            record = UserRecord(
                id=user_record.id,
                user_id=user_id,
                global_default_models=user_record.global_default_models,
            )
        await self._set_with_ttl(key, record.model_dump_json())
        return record

    async def _generate_credential_name(
        self,
        user_id: str,
        credential_data: CredentialBase,
    ) -> str:
        """Auto-generate a display name for a credential based on its type.

        Produces names like "OpenAI", "OpenAI (2)", "OpenAI (3)", etc.
        """
        cred_type = getattr(credential_data, "type", "")
        base_name = (
            cred_type.removesuffix("_credential").replace("_", " ").title()
        )
        if not base_name:
            base_name = "Credential"

        existing = await self.list_credentials(user_id)
        same_type_names = [
            c.data.get("name", "")
            for c in existing
            if c.data.get("type") == cred_type and c.id != credential_data.id
        ]

        if base_name not in same_type_names:
            return base_name

        idx = 2
        while f"{base_name} ({idx})" in same_type_names:
            idx += 1
        return f"{base_name} ({idx})"

    async def upsert_credential(
        self,
        user_id: str,
        credential_data: CredentialBase,
    ) -> str:
        """Create or update a credential record for the given user.

        If `credential_data.id` is set and the record already exists, the
        existing record's `data` field is updated in place (preserving
        `created_at`). If the id is set but no record exists, a new record is
        created with that id. If `credential_data.id` is ``None``, a new
        record with a generated id is always created.

        Args:
            user_id (`str`):
                The owner user id.
            credential_data (`CredentialBase`):
                Input data containing an optional `id` and the credential
                `data` dict.

        Returns:
            `str`:
                The id of the created or updated credential record.
        """
        if not credential_data.name:
            credential_data.name = await self._generate_credential_name(
                user_id,
                credential_data,
            )

        data_dump = _dump_with_secrets(credential_data)

        if credential_data.id:
            key = self._key(
                self.key_config.credential,
                user_id=user_id,
                credential_id=credential_data.id,
            )
            raw = await self._client.get(key)
            if raw:
                record = CredentialRecord.model_validate_json(raw)
                record.data = data_dump
                record.updated_at = datetime.now()
            else:
                record = CredentialRecord(
                    id=credential_data.id,
                    user_id=user_id,
                    data=data_dump,
                )
        else:
            record = CredentialRecord(
                user_id=user_id,
                data=data_dump,
            )

        key = self._key(
            self.key_config.credential,
            user_id=user_id,
            credential_id=record.id,
        )
        index_key = self._key(
            self.key_config.credential_index,
            user_id=user_id,
        )
        await self._set_with_ttl(key, record.model_dump_json())
        await self._client.sadd(index_key, record.id)
        return record.id

    async def list_credentials(self, user_id: str) -> list[CredentialRecord]:
        """Return all credential records belonging to the given user.

        Reads the per-user credential index Set to obtain all ids, then
        fetches each record individually. Records whose keys have expired or
        been deleted externally are silently skipped.

        Args:
            user_id (`str`): The owner user id.

        Returns:
            `list[CredentialRecord]`: All credential records for the user.
        """
        index_key = self._key(
            self.key_config.credential_index,
            user_id=user_id,
        )
        ids = await self._client.smembers(index_key)
        records = []
        for cred_id in ids:
            raw = await self._client.get(
                self._key(
                    self.key_config.credential,
                    user_id=user_id,
                    credential_id=cred_id,
                ),
            )
            if raw:
                records.append(CredentialRecord.model_validate_json(raw))
        return records

    async def get_credential(
        self,
        user_id: str,
        credential_id: str,
    ) -> CredentialRecord | None:
        """Fetch a single credential record by id."""
        key = self._key(
            self.key_config.credential,
            user_id=user_id,
            credential_id=credential_id,
        )
        raw = await self._client.get(key)
        return CredentialRecord.model_validate_json(raw) if raw else None

    async def delete_credential(
        self,
        user_id: str,
        credential_id: str,
    ) -> bool:
        """Delete a credential record and remove it from the user's index.

        Args:
            user_id (`str`): The owner user id.
            credential_id (`str`): The id of the credential to delete.

        Returns:
            `bool`: ``True`` if the record existed and was deleted,
            ``False`` if it did not exist.
        """
        key = self._key(
            self.key_config.credential,
            user_id=user_id,
            credential_id=credential_id,
        )
        index_key = self._key(
            self.key_config.credential_index,
            user_id=user_id,
        )
        deleted = await self._client.delete(key)
        await self._client.srem(index_key, credential_id)
        return deleted > 0

    async def upsert_agent(
        self,
        user_id: str,
        agent_record: AgentRecord,
    ) -> str:
        """Persist an agent record and register it in the user's agent index.

        The caller is responsible for constructing the full `AgentRecord`
        (including its `id`). If a record with the same id already exists it
        will be overwritten.

        Args:
            user_id (`str`):
                The owner user id.
            agent_record (`AgentRecord`):
                The fully-populated agent record to store.

        Returns:
            `str`:
                The id of the stored agent record.
        """
        key = self._key(
            self.key_config.agent,
            user_id=user_id,
            agent_id=agent_record.id,
        )
        index_key = self._key(self.key_config.agent_index, user_id=user_id)
        await self._set_with_ttl(key, agent_record.model_dump_json())
        await self._client.sadd(index_key, agent_record.id)
        return agent_record.id

    async def list_agents(self, user_id: str) -> list[AgentRecord]:
        """Return user-facing agent records (``source='user'``).

        Reads the per-user agent index Set to obtain all ids, fetches
        each record individually, and **filters out team-spawned
        workers** (``source='team'``) — those are scoped to a team
        and only addressable via team detail / direct id lookup, not
        enumerated as part of the user's regular agent list.

        Records whose keys have expired or been deleted externally
        are silently skipped.

        Args:
            user_id (`str`): The owner user id.

        Returns:
            `list[AgentRecord]`:
                All ``source='user'`` agent records for the user.
        """
        index_key = self._key(self.key_config.agent_index, user_id=user_id)
        ids = await self._client.smembers(index_key)
        records = []
        for agent_id in ids:
            raw = await self._client.get(
                self._key(
                    self.key_config.agent,
                    user_id=user_id,
                    agent_id=agent_id,
                ),
            )
            if raw:
                record = AgentRecord.model_validate_json(raw)
                if record.source == "user":
                    records.append(record)
        return records

    async def get_agent(
        self,
        user_id: str,
        agent_id: str,
    ) -> AgentRecord | None:
        """Fetch a single agent record by id."""
        key = self._key(
            self.key_config.agent,
            user_id=user_id,
            agent_id=agent_id,
        )
        raw = await self._client.get(key)
        return AgentRecord.model_validate_json(raw) if raw else None

    async def delete_agent(self, user_id: str, agent_id: str) -> bool:
        """Delete an agent record and cascade-delete its sessions,
        schedules, and any team back-references.

        Cascade order:

        1. **Sessions** — every session belonging to this agent is
           deleted via :meth:`delete_session` (which itself cascades
           message log, schedule-session index, and — if a session leads
           a team — the team).
        2. **Schedules** — every schedule whose ``data.agent_id`` matches
           is deleted via :meth:`delete_schedule`.
        3. **Team back-references (defensive)** — if the agent is a team
           worker (``source='team'``) but the caller chose to delete it
           directly instead of going through :meth:`delete_team`, scan
           the user's teams and remove the agent id from every
           :attr:`TeamData.member_ids` list it appears in. The normal
           path (``delete_team`` iterates ``member_ids`` and calls
           ``delete_agent`` for each) does not need this scan, but it
           keeps the team record consistent if a caller bypasses it.
        4. **Agent record + index** — finally delete the agent key and
           remove from the per-user agent index.

        Args:
            user_id (`str`):
                The owner user id.
            agent_id (`str`):
                The id of the agent to delete.

        Returns:
            `bool`:
                ``True`` if the agent record existed and was deleted,
                ``False`` if it did not exist.
        """
        # Cascade: sessions
        sessions = await self.list_sessions(user_id, agent_id)
        for session in sessions:
            await self.delete_session(user_id, agent_id, session.id)

        # Cascade: schedules owned by this agent
        schedules = await self.list_schedules(user_id)
        for schedule in schedules:
            if schedule.agent_id == agent_id:
                await self.delete_schedule(user_id, schedule.id)

        # Defensive: scrub agent_id from any team's member_ids list.
        # The common path (delete_team -> delete_agent) is unaffected
        # because the team is being torn down anyway and removed from
        # the index in step 4 of delete_team.
        teams = await self.list_teams(user_id)
        for team in teams:
            if agent_id in team.data.member_ids:
                team.data.member_ids = [
                    mid for mid in team.data.member_ids if mid != agent_id
                ]
                await self.upsert_team(user_id, team)

        key = self._key(
            self.key_config.agent,
            user_id=user_id,
            agent_id=agent_id,
        )
        index_key = self._key(self.key_config.agent_index, user_id=user_id)
        deleted = await self._client.delete(key)
        await self._client.srem(index_key, agent_id)
        return deleted > 0

    @staticmethod
    def _sync_permission_mode(
        permission_mode,
        state: AgentState,
    ) -> AgentState:
        """Keep runtime permission mode aligned with persisted config."""
        state.permission_context = state.permission_context.model_copy(
            update={"mode": permission_mode},
        )
        return state

    def _session_state_key(self, user_id: str, session_id: str) -> str:
        return self._key(
            self.key_config.session_state,
            user_id=user_id,
            session_id=session_id,
        )

    async def _write_session_state(
        self,
        user_id: str,
        session_id: str,
        permission_mode,
        state: AgentState,
        *,
        created_at: datetime | None = None,
    ) -> AgentState:
        synced_state = self._sync_permission_mode(permission_mode, state)
        key = self._session_state_key(user_id, session_id)
        raw = await self._client.get(key)
        record = (
            SessionStateRecord.model_validate_json(raw)
            if raw
            else SessionStateRecord(
                id=session_id,
                session_id=session_id,
                user_id=user_id,
                created_at=created_at or datetime.now(),
            )
        )
        record.state = synced_state
        record.updated_at = datetime.now()
        await self._set_with_ttl(key, record.model_dump_json())
        return synced_state

    @staticmethod
    def _hydrate_session(
        record: SessionRecord,
        state: AgentState,
    ) -> SessionWithState:
        return SessionWithState(
            **record.model_dump(mode="python"),
            state=state,
        )

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

        When *session_id* is provided the existing session is updated.
        When *session_id* is ``None`` a new session is always created.
        """
        if session_id:
            existing = await self.get_session_meta(user_id, session_id)
            if existing:
                record = existing.model_copy(deep=True)
                old_parent_session_id = record.parent_session_id
                record.config = config
                if parent_session_id is not None:
                    record.parent_session_id = parent_session_id
                record.updated_at = datetime.now()
                key = self._key(
                    self.key_config.session,
                    user_id=user_id,
                    session_id=session_id,
                )
                await self._set_with_ttl(key, record.model_dump_json())
                if old_parent_session_id != record.parent_session_id:
                    if old_parent_session_id is not None:
                        old_children_key = self._key(
                            self.key_config.session_children,
                            user_id=user_id,
                            parent_session_id=old_parent_session_id,
                        )
                        await self._client.srem(old_children_key, record.id)
                    if record.parent_session_id is not None:
                        children_key = self._key(
                            self.key_config.session_children,
                            user_id=user_id,
                            parent_session_id=record.parent_session_id,
                        )
                        await self._client.sadd(children_key, record.id)
                session_state = state or await self.get_session_state(
                    user_id,
                    session_id,
                )
                if session_state is None:
                    raise KeyError(f"Session {session_id!r} state not found.")
                synced_state = await self._write_session_state(
                    user_id=user_id,
                    session_id=session_id,
                    permission_mode=config.permission_mode,
                    state=session_state,
                    created_at=record.created_at,
                )
                return self._hydrate_session(record, synced_state)

        # Use the caller-provided ``session_id`` when given so a
        # "create-if-missing under this id" call (e.g. scheduler's
        # stateful-mode session) lands at the expected key.
        new_id_kwargs = {"id": session_id} if session_id else {}
        record = SessionRecord(
            user_id=user_id,
            agent_id=agent_id,
            config=config,
            source=source,
            source_schedule_id=source_schedule_id,
            source_chat_id=source_chat_id,
            source_chat_name=source_chat_name,
            source_channel_id=source_channel_id,
            conversation_kind=conversation_kind,
            parent_session_id=parent_session_id,
            **new_id_kwargs,
        )
        key = self._key(
            self.key_config.session,
            user_id=user_id,
            session_id=record.id,
        )
        index_key = self._key(
            self.key_config.session_index,
            user_id=user_id,
            agent_id=agent_id,
        )
        await self._set_with_ttl(key, record.model_dump_json())
        synced_state = await self._write_session_state(
            user_id=user_id,
            session_id=record.id,
            permission_mode=config.permission_mode,
            state=state if state is not None else AgentState(),
            created_at=record.created_at,
        )
        await self._client.sadd(index_key, record.id)
        if record.parent_session_id is not None:
            children_key = self._key(
                self.key_config.session_children,
                user_id=user_id,
                parent_session_id=record.parent_session_id,
            )
            await self._client.sadd(children_key, record.id)

        if source_schedule_id:
            schedule_session_key = self._key(
                self.key_config.schedule_session_index,
                user_id=user_id,
                schedule_id=source_schedule_id,
            )
            await self._client.sadd(schedule_session_key, record.id)

        if source_channel_id:
            channel_session_key = self._key(
                self.key_config.channel_session_index,
                user_id=user_id,
                channel_id=source_channel_id,
            )
            await self._client.sadd(channel_session_key, record.id)

        return self._hydrate_session(record, synced_state)

    async def update_session_state(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        state: AgentState,
    ) -> None:
        """Update only the mutable state of an existing session.

        Raises:
            KeyError: If the session does not exist.
        """
        record = await self.get_session_meta(user_id, session_id)
        if record is None:
            raise KeyError(f"Session {session_id!r} not found.")
        record.updated_at = datetime.now()
        await self._set_with_ttl(
            self._key(
                self.key_config.session,
                user_id=user_id,
                session_id=session_id,
            ),
            record.model_dump_json(),
        )
        synced_state = self._sync_permission_mode(
            record.config.permission_mode,
            state,
        )
        await self._write_session_state(
            user_id=user_id,
            session_id=session_id,
            permission_mode=record.config.permission_mode,
            state=synced_state,
            created_at=record.created_at,
        )

    async def get_session_meta(
        self,
        user_id: str,
        session_id: str,
    ) -> SessionRecord | None:
        key = self._key(
            self.key_config.session,
            user_id=user_id,
            session_id=session_id,
        )
        raw = await self._client.get(key)
        if not raw:
            return None
        return SessionRecord.model_validate_json(raw)

    async def get_session_state(
        self,
        user_id: str,
        session_id: str,
    ) -> AgentState | None:
        raw = await self._client.get(self._session_state_key(user_id, session_id))
        if not raw:
            return None
        return SessionStateRecord.model_validate_json(raw).state

    async def get_sessions_meta_by_ids(
        self,
        user_id: str,
        session_ids: list[str],
    ) -> list[SessionRecord]:
        if not session_ids:
            return []
        raws = await self._client.mget(
            [
                self._key(
                    self.key_config.session,
                    user_id=user_id,
                    session_id=session_id,
                )
                for session_id in session_ids
            ],
        )
        return [
            SessionRecord.model_validate_json(raw)
            for raw in raws
            if raw
        ]

    async def list_sessions(
        self,
        user_id: str,
        agent_id: str,
    ) -> list[SessionRecord]:
        """Return all session records for a given (user, agent) pair.

        Reads the per-agent session index Set to obtain all session ids, then
        fetches each record individually. Records whose keys have expired or
        been deleted externally are silently skipped.

        Args:
            user_id (`str`): The owner user id.
            agent_id (`str`): The agent id whose sessions to list.

        Returns:
            `list[SessionRecord]`: All session records for the (user, agent)
            pair.
        """
        index_key = self._key(
            self.key_config.session_index,
            user_id=user_id,
            agent_id=agent_id,
        )
        ids = await self._client.smembers(index_key)
        records = await self.get_sessions_meta_by_ids(user_id, list(ids))
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records

    async def list_child_sessions(
        self,
        user_id: str,
        parent_session_id: str,
    ) -> list[SessionRecord]:
        """Return direct child sessions for a parent session."""
        children_key = self._key(
            self.key_config.session_children,
            user_id=user_id,
            parent_session_id=parent_session_id,
        )
        ids = await self._client.smembers(children_key)
        records = await self.get_sessions_meta_by_ids(user_id, list(ids))
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records

    async def upsert_subagent_task(
        self,
        user_id: str,
        record: SubAgentTaskRecord,
    ) -> str:
        """Create or update one delegated sub-agent task record."""
        key = self._key(
            self.key_config.subagent_task,
            user_id=user_id,
            task_id=record.id,
        )
        await self._set_with_ttl(key, record.model_dump_json())
        return record.id

    async def get_subagent_task(
        self,
        user_id: str,
        task_id: str,
    ) -> SubAgentTaskRecord | None:
        """Fetch one delegated sub-agent task record by id."""
        key = self._key(
            self.key_config.subagent_task,
            user_id=user_id,
            task_id=task_id,
        )
        raw = await self._client.get(key)
        if not raw:
            return None
        return SubAgentTaskRecord.model_validate_json(raw)

    async def get_active_subagent_task_by_child_session(
        self,
        user_id: str,
        child_session_id: str,
    ) -> SubAgentTaskRecord | None:
        """Fetch the active delegated task bound to one child session."""
        key = self._key(
            self.key_config.active_subagent_task_by_child_session,
            user_id=user_id,
            child_session_id=child_session_id,
        )
        task_id = await self._client.get(key)
        if not task_id:
            return None
        record = await self.get_subagent_task(user_id, task_id)
        if record is None:
            await self._client.delete(key)
            await self._client.srem(
                self.key_config.active_subagent_tasks,
                self._encode_active_subagent_task_member(user_id, task_id),
            )
        return record

    async def mark_active_subagent_task(
        self,
        user_id: str,
        child_session_id: str,
        task_id: str,
    ) -> None:
        """Mark one delegated task as the active task for a child session."""
        child_key = self._key(
            self.key_config.active_subagent_task_by_child_session,
            user_id=user_id,
            child_session_id=child_session_id,
        )
        await self._client.set(child_key, task_id)
        await self._refresh_key_ttl(child_key)
        await self._client.sadd(
            self.key_config.active_subagent_tasks,
            self._encode_active_subagent_task_member(user_id, task_id),
        )

    async def unmark_active_subagent_task(
        self,
        user_id: str,
        child_session_id: str,
        task_id: str,
    ) -> None:
        """Remove one delegated task from the active task indexes."""
        child_key = self._key(
            self.key_config.active_subagent_task_by_child_session,
            user_id=user_id,
            child_session_id=child_session_id,
        )
        current_task_id = await self._client.get(child_key)
        if current_task_id == task_id:
            await self._client.delete(child_key)
        await self._client.srem(
            self.key_config.active_subagent_tasks,
            self._encode_active_subagent_task_member(user_id, task_id),
        )

    async def list_active_subagent_tasks(self) -> list[tuple[str, str]]:
        """Return all active delegated task pairs across users."""
        members = await self._client.smembers(
            self.key_config.active_subagent_tasks,
        )
        results: list[tuple[str, str]] = []
        for member in members:
            parsed = self._decode_active_subagent_task_member(member)
            if parsed is not None:
                user_id, task_id = parsed
                if await self.get_subagent_task(user_id, task_id) is None:
                    await self._client.srem(
                        self.key_config.active_subagent_tasks,
                        member,
                    )
                    continue
                results.append(parsed)
        return results

    async def delete_subagent_task(
        self,
        user_id: str,
        task_id: str,
    ) -> bool:
        """Delete one delegated sub-agent task record."""
        record = await self.get_subagent_task(user_id, task_id)
        if record is None:
            return False
        await self.unmark_active_subagent_task(
            user_id=user_id,
            child_session_id=record.child_session_id,
            task_id=task_id,
        )
        key = self._key(
            self.key_config.subagent_task,
            user_id=user_id,
            task_id=task_id,
        )
        deleted = await self._client.delete(key)
        return deleted > 0

    async def get_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> SessionWithState | None:
        """Fetch one hydrated session record by id."""
        record = await self.get_session_meta(user_id, session_id)
        if record is None:
            return None
        state = await self.get_session_state(user_id, session_id)
        if state is None:
            return None
        synced_state = self._sync_permission_mode(
            record.config.permission_mode,
            state,
        )
        return self._hydrate_session(record, synced_state)

    async def delete_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> bool:
        """Delete a session record and cascade clean-up.

        Cascades:

        - Existing: per-session message log, schedule-session index entry.
        - **NEW**: if this session is the leader of a team (``team_id``
          set AND a :class:`TeamRecord` exists with
          ``session_id == this session_id``), call :meth:`delete_team`
          first. ``delete_team`` will recursively cascade workers and
          clear ``team_id`` on this session — that clear is idempotent
          and the session itself is deleted right after, so the order is
          safe.

        Worker sessions (``team_id`` set, but the team's
        ``leader_session_id`` is **not** this session) are deleted
        without dissolving the team — the team and the surviving leader
        keep their member_ids list pointing to the now-orphaned worker
        agent. This intentional asymmetry mirrors SQL: there is no FK
        from :class:`SessionRecord` back to the agent that owns it, so
        deleting a session doesn't automatically delete the agent.

        Args:
            user_id (`str`):
                The owner user id.
            agent_id (`str`):
                The id of the agent that owns the session (used to
                clean up the per-agent session index).
            session_id (`str`):
                The id of the session to delete.

        Returns:
            `bool`:
                ``True`` if the session existed and was deleted,
                ``False`` if no record was found.
        """
        key = self._key(
            self.key_config.session,
            user_id=user_id,
            session_id=session_id,
        )
        raw = await self._client.get(key)
        if not raw:
            return False

        record = SessionRecord.model_validate_json(raw)

        # Cascade: if this session leads a team, dissolve it first.
        if record.team_id:
            team = await self.get_team(user_id, record.team_id)
            if team is not None and team.session_id == session_id:
                await self.delete_team(user_id, record.team_id)

        index_key = self._key(
            self.key_config.session_index,
            user_id=user_id,
            agent_id=agent_id,
        )
        msg_key = self._key(
            self.key_config.messages,
            user_id=user_id,
            session_id=session_id,
        )
        active_task = await self.get_active_subagent_task_by_child_session(
            user_id=user_id,
            child_session_id=session_id,
        )
        await self._client.delete(key)
        await self._client.delete(self._session_state_key(user_id, session_id))
        await self._client.srem(index_key, session_id)
        await self._client.delete(msg_key)
        if active_task is not None:
            await self.delete_subagent_task(user_id, active_task.id)
        if record.parent_session_id is not None:
            children_key = self._key(
                self.key_config.session_children,
                user_id=user_id,
                parent_session_id=record.parent_session_id,
            )
            await self._client.srem(children_key, session_id)

        if record.source_schedule_id:
            schedule_session_key = self._key(
                self.key_config.schedule_session_index,
                user_id=user_id,
                schedule_id=record.source_schedule_id,
            )
            await self._client.srem(schedule_session_key, session_id)

        if record.source_channel_id:
            channel_session_key = self._key(
                self.key_config.channel_session_index,
                user_id=user_id,
                channel_id=record.source_channel_id,
            )
            await self._client.srem(channel_session_key, session_id)

        return True

    async def list_sessions_by_schedule(
        self,
        user_id: str,
        schedule_id: str,
    ) -> list[SessionRecord]:
        """Return all sessions created by a given schedule."""
        schedule_session_key = self._key(
            self.key_config.schedule_session_index,
            user_id=user_id,
            schedule_id=schedule_id,
        )
        ids = await self._client.smembers(schedule_session_key)
        records = await self.get_sessions_meta_by_ids(user_id, list(ids))
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records

    async def list_sessions_by_channel(
        self,
        user_id: str,
        channel_id: str,
    ) -> list[SessionRecord]:
        """Return all sessions derived from a given channel."""
        channel_session_key = self._key(
            self.key_config.channel_session_index,
            user_id=user_id,
            channel_id=channel_id,
        )
        ids = await self._client.smembers(channel_session_key)
        records = await self.get_sessions_meta_by_ids(user_id, list(ids))
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records

    async def upsert_schedule(
        self,
        user_id: str,
        record: ScheduleRecord,
    ) -> str:
        """Persist a cron task record and register it in the user and global
        indexes."""
        key = self._key(
            self.key_config.schedule,
            user_id=user_id,
            schedule_id=record.id,
        )
        index_key = self._key(self.key_config.schedule_index, user_id=user_id)
        await self._set_with_ttl(key, record.model_dump_json())
        await self._client.sadd(index_key, record.id)
        await self._client.sadd(
            self.key_config.schedule_global_index,
            f"{user_id}:{record.id}",
        )
        return record.id

    async def get_schedule(
        self,
        user_id: str,
        schedule_id: str,
    ) -> ScheduleRecord | None:
        """Fetch a single cron task record by id."""
        key = self._key(
            self.key_config.schedule,
            user_id=user_id,
            schedule_id=schedule_id,
        )
        raw = await self._client.get(key)
        if not raw:
            return None
        return ScheduleRecord.model_validate_json(raw)

    async def list_schedules(self, user_id: str) -> list[ScheduleRecord]:
        """Return all cron task records belonging to the given user."""
        index_key = self._key(
            self.key_config.schedule_index,
            user_id=user_id,
        )
        ids = await self._client.smembers(index_key)
        records = []
        for schedule_id in ids:
            raw = await self._client.get(
                self._key(
                    self.key_config.schedule,
                    user_id=user_id,
                    schedule_id=schedule_id,
                ),
            )
            if raw:
                records.append(ScheduleRecord.model_validate_json(raw))
        return records

    async def delete_schedule(self, user_id: str, schedule_id: str) -> bool:
        """Delete a cron task record, cascade-delete its execution sessions,
        and remove it from the user and global indexes."""
        key = self._key(
            self.key_config.schedule,
            user_id=user_id,
            schedule_id=schedule_id,
        )
        raw = await self._client.get(key)
        if not raw:
            return False

        record = ScheduleRecord.model_validate_json(raw)

        # Cascade: delete all sessions created by this schedule
        sessions = await self.list_sessions_by_schedule(user_id, schedule_id)
        for session in sessions:
            await self.delete_session(
                user_id,
                record.agent_id,
                session.id,
            )

        # Clean up the schedule session index key itself
        schedule_session_key = self._key(
            self.key_config.schedule_session_index,
            user_id=user_id,
            schedule_id=schedule_id,
        )
        await self._client.delete(schedule_session_key)

        # Delete the schedule record and its index entries
        index_key = self._key(self.key_config.schedule_index, user_id=user_id)
        await self._client.delete(key)
        await self._client.srem(index_key, schedule_id)
        await self._client.srem(
            self.key_config.schedule_global_index,
            f"{user_id}:{schedule_id}",
        )
        return True

    async def list_all_schedules(self) -> list[ScheduleRecord]:
        """Return every schedule record across all users.

        Reads the global schedule index (a Redis Set of ``user_id:schedule_id``
        pairs) and fetches each record individually.  Records whose keys have
        expired or been deleted externally are silently skipped.

        Returns:
            `list[ScheduleRecord]`: All schedule records in the store.
        """
        entries = await self._client.smembers(
            self.key_config.schedule_global_index,
        )
        records = []
        for entry in entries:
            user_id, schedule_id = entry.split(":", 1)
            raw = await self._client.get(
                self._key(
                    self.key_config.schedule,
                    user_id=user_id,
                    schedule_id=schedule_id,
                ),
            )
            if raw:
                records.append(ScheduleRecord.model_validate_json(raw))
        return records

    # ------------------------------------------------------------------
    # Channel persistence
    # ------------------------------------------------------------------

    async def upsert_channel(
        self,
        record: ChannelRecord,
        platform_bot_id: str,
    ) -> str:
        """Persist a channel record and refresh its indexes."""
        key = self._key(self.key_config.channel, channel_id=record.id)
        index_key = self._key(
            self.key_config.channel_index,
            user_id=record.user_id,
        )
        botid_key = self._key(
            self.key_config.channel_botid_index,
            platform_bot_id=platform_bot_id,
        )
        await self._set_with_ttl(key, record.model_dump_json())
        await self._client.sadd(index_key, record.id)
        await self._client.sadd(self.key_config.channel_all_index, record.id)
        await self._set_with_ttl(botid_key, record.id)
        return record.id

    async def get_channel(
        self,
        channel_id: str,
    ) -> ChannelRecord | None:
        """Fetch a channel record by its global id."""
        raw = await self._client.get(
            self._key(self.key_config.channel, channel_id=channel_id),
        )
        if not raw:
            return None
        return ChannelRecord.model_validate_json(raw)

    async def _collect_channels(
        self,
        index_key: str,
        ids: set[str],
    ) -> list[ChannelRecord]:
        """Load channel records for ``ids`` and purge stale index entries."""
        records: list[ChannelRecord] = []
        stale: list[str] = []
        for channel_id in ids:
            raw = await self._client.get(
                self._key(self.key_config.channel, channel_id=channel_id),
            )
            if raw:
                records.append(ChannelRecord.model_validate_json(raw))
            else:
                stale.append(channel_id)
        if stale:
            await self._client.srem(index_key, *stale)
        return records

    async def list_channels(self, user_id: str) -> list[ChannelRecord]:
        """Return all channel records owned by the given user."""
        index_key = self._key(self.key_config.channel_index, user_id=user_id)
        return await self._collect_channels(
            index_key,
            await self._client.smembers(index_key),
        )

    async def list_all_channels(self) -> list[ChannelRecord]:
        """Return every channel record across all users."""
        index_key = self.key_config.channel_all_index
        return await self._collect_channels(
            index_key,
            await self._client.smembers(index_key),
        )

    async def delete_channel(
        self,
        channel_id: str,
        platform_bot_id: str,
    ) -> bool:
        """Delete a channel record and clean up all indexes."""
        key = self._key(self.key_config.channel, channel_id=channel_id)
        raw = await self._client.get(key)
        if not raw:
            return False
        record = ChannelRecord.model_validate_json(raw)
        await self._client.delete(key)
        await self._client.srem(
            self._key(
                self.key_config.channel_index,
                user_id=record.user_id,
            ),
            channel_id,
        )
        await self._client.srem(self.key_config.channel_all_index, channel_id)
        await self._client.delete(
            self._key(
                self.key_config.channel_botid_index,
                platform_bot_id=platform_bot_id,
            ),
        )
        return True

    async def get_channel_id_by_platform_bot_id(
        self,
        platform_bot_id: str,
    ) -> str | None:
        """Return the channel id bound to a platform bot, if any."""
        return await self._client.get(
            self._key(
                self.key_config.channel_botid_index,
                platform_bot_id=platform_bot_id,
            ),
        )

    # ------------------------------------------------------------------
    # Message persistence
    # ------------------------------------------------------------------

    def _message_key(self, user_id: str, session_id: str) -> str:
        """Return the Redis List key for a session's messages."""
        return self._key(
            self.key_config.messages,
            user_id=user_id,
            session_id=session_id,
        )

    async def upsert_message(
        self,
        user_id: str,
        session_id: str,
        msg: Msg,
    ) -> None:
        """Persist a message to the session's message list."""
        key = self._message_key(user_id, session_id)
        last_raw = await self._client.lindex(key, -1)
        if last_raw:
            last_msg = Msg.model_validate_json(last_raw)
            if last_msg.id == msg.id:
                await self._client.lset(key, -1, msg.model_dump_json())
                await self._refresh_key_ttl(key)
                return
        await self._client.rpush(key, msg.model_dump_json())
        await self._refresh_key_ttl(key)

    async def get_message(
        self,
        user_id: str,
        session_id: str,
        message_id: str,
    ) -> Msg | None:
        """Fetch a single message by id from the session's message list."""
        key = self._message_key(user_id, session_id)
        length = await self._client.llen(key)
        for i in range(length - 1, -1, -1):
            raw = await self._client.lindex(key, i)
            if raw:
                msg = Msg.model_validate_json(raw)
                if msg.id == message_id:
                    return msg
        return None

    async def list_messages(
        self,
        user_id: str,
        session_id: str,
        offset: int = 0,
        limit: int = 50,
    ) -> list[Msg]:
        """Return messages for a session with pagination."""
        key = self._message_key(user_id, session_id)
        raw_list = await self._client.lrange(key, offset, offset + limit - 1)
        return [Msg.model_validate_json(raw) for raw in raw_list]

    async def replace_messages(
        self,
        user_id: str,
        session_id: str,
        messages: list[Msg],
    ) -> None:
        """Replace the full persisted message list for a session."""
        key = self._message_key(user_id, session_id)
        await self._client.delete(key)
        if not messages:
            return
        await self._client.rpush(
            key,
            *[message.model_dump_json() for message in messages],
        )
        await self._refresh_key_ttl(key)

    # ------------------------------------------------------------------
    # Team persistence
    # ------------------------------------------------------------------

    async def upsert_team(
        self,
        user_id: str,
        record: TeamRecord,
    ) -> TeamRecord:
        """Persist a team record and register it in the user's team index.

        Args:
            user_id (`str`):
                The owner user id. Used to scope both the record key and
                the per-user team index.
            record (`TeamRecord`):
                The team record to persist. Its ``id`` is used as the
                primary key; an existing record with the same id is
                overwritten. ``updated_at`` is refreshed to ``datetime.now()``
                before writing.

        Returns:
            `TeamRecord`:
                The stored record (with refreshed ``updated_at``).
        """
        record.updated_at = datetime.now()
        key = self._key(
            self.key_config.team,
            user_id=user_id,
            team_id=record.id,
        )
        index_key = self._key(self.key_config.team_index, user_id=user_id)
        await self._set_with_ttl(key, record.model_dump_json())
        await self._client.sadd(index_key, record.id)
        return record

    async def get_team(
        self,
        user_id: str,
        team_id: str,
    ) -> TeamRecord | None:
        """Fetch a single team record by id.

        Args:
            user_id (`str`):
                The owner user id.
            team_id (`str`):
                The team id to look up.

        Returns:
            `TeamRecord | None`:
                The record, or ``None`` if no record exists at the
                ``(user_id, team_id)`` key (e.g. expired or never created).
        """
        key = self._key(
            self.key_config.team,
            user_id=user_id,
            team_id=team_id,
        )
        raw = await self._client.get(key)
        if not raw:
            return None
        return TeamRecord.model_validate_json(raw)

    async def list_teams(self, user_id: str) -> list[TeamRecord]:
        """Return all team records belonging to the given user.

        Reads the per-user team index (a Redis Set of team ids) and fetches
        each record individually. Records whose keys have expired or been
        deleted externally are silently skipped.

        Args:
            user_id (`str`):
                The owner user id whose teams to list.

        Returns:
            `list[TeamRecord]`:
                All team records for the user, in arbitrary order (the
                index is a Set).
        """
        index_key = self._key(self.key_config.team_index, user_id=user_id)
        ids = await self._client.smembers(index_key)
        records: list[TeamRecord] = []
        for team_id in ids:
            raw = await self._client.get(
                self._key(
                    self.key_config.team,
                    user_id=user_id,
                    team_id=team_id,
                ),
            )
            if raw:
                records.append(TeamRecord.model_validate_json(raw))
        return records

    async def set_session_team_id(
        self,
        user_id: str,
        session_id: str,
        team_id: str | None,
    ) -> None:
        """Set or clear ``team_id`` on an existing session record.

        Bypasses :meth:`upsert_session` because that method does not
        allow writing ``team_id`` (which is a relation column the
        application normally only mutates via team operations).
        Idempotent: a no-op if the session does not exist or already
        holds the given value.

        Args:
            user_id (`str`):
                The owner user id.
            session_id (`str`):
                The session whose ``team_id`` should be updated.
            team_id (`str | None`):
                The new value. ``None`` detaches the session from any
                team (used by :meth:`delete_team` and by the team
                service when a session leaves a team).
        """
        key = self._key(
            self.key_config.session,
            user_id=user_id,
            session_id=session_id,
        )
        raw = await self._client.get(key)
        if not raw:
            return
        record = SessionRecord.model_validate_json(raw)
        if record.team_id == team_id:
            return
        record.team_id = team_id
        record.updated_at = datetime.now()
        await self._set_with_ttl(key, record.model_dump_json())

    async def delete_team(self, user_id: str, team_id: str) -> bool:
        """Delete a team record and cascade-delete all of its workers.

        Cascade order (mirrors what SQL's ``ON DELETE CASCADE`` would do
        for the same set of foreign keys):

        1. For each ``member_id`` in :attr:`TeamData.member_ids`, call
           :meth:`delete_agent`. Each call cascades the worker's single
           session via the existing agent-cascade logic.
        2. Clear ``team_id`` on the leader session (referenced by
           :attr:`TeamRecord.session_id`) — semantically equivalent to
           ``ON DELETE SET NULL`` for that direction of the relationship.
           Idempotent if the session has already been deleted (no-op).
        3. Delete the :class:`TeamRecord` key and remove it from the
           per-user team index.

        The cascade is best-effort: Redis has no cross-key transaction,
        so a process crash mid-cascade may leave residue. Each step is
        idempotent so retries are safe.

        Args:
            user_id (`str`):
                The owner user id.
            team_id (`str`):
                The id of the team to delete.

        Returns:
            `bool`:
                ``True`` if the team record existed and was deleted,
                ``False`` if no record was found at the
                ``(user_id, team_id)`` key.
        """
        team = await self.get_team(user_id, team_id)
        if team is None:
            # Make sure the index is also clean if the record vanished
            # for any reason.
            index_key = self._key(self.key_config.team_index, user_id=user_id)
            await self._client.srem(index_key, team_id)
            return False

        # Cascade: delete each worker agent (which cascades its session)
        for member_id in team.data.member_ids:
            await self.delete_agent(user_id, member_id)

        # Clear team_id on the leader session (idempotent)
        await self.set_session_team_id(user_id, team.session_id, None)

        # Delete the TeamRecord key + index entry
        key = self._key(
            self.key_config.team,
            user_id=user_id,
            team_id=team_id,
        )
        existed = await self._client.delete(key)
        index_key = self._key(self.key_config.team_index, user_id=user_id)
        await self._client.srem(index_key, team_id)
        return bool(existed)
