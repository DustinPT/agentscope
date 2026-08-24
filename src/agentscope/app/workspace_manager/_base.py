# -*- coding: utf-8 -*-
"""Workspace manager implementations."""

import asyncio
import hashlib
import uuid
from abc import ABC, abstractmethod
from collections import defaultdict
from enum import StrEnum
from typing import TYPE_CHECKING, Self

from ...mcp import MCPClient
from ...workspace import WorkspaceBase
from ..storage import AgentMCPAsset, AgentSkillAsset

if TYPE_CHECKING:
    from ..storage import StorageBase


class IsolationPolicy(StrEnum):
    """Workspace isolation grain for
    :meth:`WorkspaceManagerBase.assign_workspace_id`.
    """

    PER_SESSION = "per_session"
    PER_AGENT = "per_agent"
    PER_USER = "per_user"


class WorkspaceManagerBase(ABC):
    """Abstract base for workspace managers.

    Subclasses are expected to be used as async context managers — entering
    the context activates any background machinery the subclass needs (e.g.
    a TTL sweeper task) and exiting it tears that machinery down and closes
    every cached workspace via :meth:`close_all`.

    The default ``__aenter__`` / ``__aexit__`` cover the common case where a
    subclass has no background machinery: enter is a no-op, exit just calls
    :meth:`close_all`. Subclasses that own background tasks should override
    both.
    """

    def __init__(
        self,
        *,
        isolation: IsolationPolicy = IsolationPolicy.PER_AGENT,
    ) -> None:
        """Bind the isolation policy for :meth:`assign_workspace_id`."""
        self._isolation: IsolationPolicy = isolation
        self._storage: "StorageBase | None" = None
        self._bind_locks: defaultdict[
            tuple[str, str],
            asyncio.Lock,
        ] = defaultdict(asyncio.Lock)
        self._reserved: dict[tuple[str, str], str] = {}

    def bind_storage(self, storage: "StorageBase") -> None:
        """Hand the manager the backend holding workspace bindings."""
        self._storage = storage

    async def assign_workspace_id(
        self,
        *,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> str:
        """Mint a workspace id under :attr:`_isolation`."""
        del session_id

        if self._isolation is IsolationPolicy.PER_USER:
            return hashlib.blake2b(
                f"user::{user_id}".encode("utf-8"),
                digest_size=8,
            ).hexdigest()

        if self._isolation is IsolationPolicy.PER_SESSION:
            return await self._mint_workspace_id()

        if self._storage is None:
            return hashlib.blake2b(
                f"{user_id}::{agent_id}".encode("utf-8"),
                digest_size=8,
            ).hexdigest()

        async with self._bind_locks[(user_id, agent_id)]:
            for record in await self._storage.list_sessions(
                user_id,
                agent_id,
            ):
                if record.config.workspace_id:
                    self._reserved.pop((user_id, agent_id), None)
                    return record.config.workspace_id
            reserved = self._reserved.get((user_id, agent_id))
            if reserved:
                return reserved
            workspace_id = await self._mint_workspace_id()
            self._reserved[(user_id, agent_id)] = workspace_id
            return workspace_id

    async def _mint_workspace_id(self) -> str:
        """Produce an id for a workspace nobody holds yet."""
        return uuid.uuid4().hex

    @abstractmethod
    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str | None,
        agent_mcps: list[MCPClient] | None = None,
        agent_mcp_assets: list[AgentMCPAsset] | None = None,
        agent_skill_assets: list[AgentSkillAsset] | None = None,
    ) -> WorkspaceBase:
        """Return an initialized workspace.

        Args:
            user_id (`str`):
                The user id.
            agent_id (`str`):
                The agent id.
            session_id (`str`):
                The session id.
            workspace_id (`str | None`):
                The workspace id (reconnection credential). ``None``
                triggers :meth:`assign_workspace_id`.
        """

    @abstractmethod
    async def close(self, workspace_id: str) -> None:
        """Close and evict a single workspace from the cache."""

    @abstractmethod
    async def close_all(self) -> None:
        """Close every cached workspace.

        Pure "close all currently tracked workspaces" semantics — does not
        imply the manager itself is being torn down. Use ``async with`` (or
        :meth:`__aexit__` directly) for full manager shutdown.
        """

    async def __aenter__(self) -> Self:
        """Enter the manager's lifetime. Default is a no-op."""
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Exit the manager's lifetime — closes all cached workspaces."""
        await self.close_all()
