# -*- coding: utf-8 -*-
"""Builtin tool for requesting persisted sandbox access grants."""
from __future__ import annotations

from typing import Any

from pydantic import Field

from ...permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from ...message import ToolResultState
from ...tool import ParamsBase
from ..storage import (
    SandboxGrantResourceType,
    SandboxGrantScope,
    SandboxPermissionRecord,
    merge_sandbox_grants,
    normalize_sandbox_grant,
)
from ._session_tool_base import _SessionToolBase


class _RequestSandboxAccessParams(ParamsBase):
    """Parameters for :class:`RequestSandboxAccess`."""

    resource_type: SandboxGrantResourceType = Field(
        description="Sandbox resource type to authorize.",
    )
    pattern: str = Field(
        description=(
            "Specific domain, directory path, or file path to authorize. "
            "Request only the minimum access needed and avoid wildcards "
            "unless they are truly necessary. For directories, use the "
            "directory path itself without adding a '/**' suffix; the "
            "directory path already covers all descendant files and "
            "subdirectories."
        ),
        min_length=1,
    )
    operations: list[str] = Field(
        default_factory=list,
        description=(
            "Requested operations. Use ['connect'] for domains and "
            "['read'] or ['read', 'write'] for paths."
        ),
    )
    reason: str = Field(
        default="",
        description="Why this sandbox access is needed.",
    )


class RequestSandboxAccess(_SessionToolBase):
    """Persist one sandbox access grant for future workspace rebuilds."""

    name = "RequestSandboxAccess"
    description = (
        "Request user authorization for sandbox access to a specific domain, "
        "directory, or file. Request only the minimum permissions needed: "
        "prefer exact domains, exact directories, and exact files, and avoid "
        "wildcards unless they are truly necessary. For directories, provide "
        "the directory path itself without adding a '/**' suffix because the "
        "directory path already covers all descendant files and "
        "subdirectories. This tool always requires explicit user "
        "confirmation and returns whether access was authorized plus the "
        "granted permission details."
    )
    input_schema: dict[str, Any] = _RequestSandboxAccessParams.model_json_schema()
    is_read_only = False

    def __init__(
        self,
        *,
        sandbox_type: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._sandbox_type = sandbox_type

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Always ask the user before persisting extra sandbox access."""
        del tool_input, context
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message=(
                "Sandbox access changes always require explicit user approval."
            ),
            always_confirm=True,
        )

    async def call(
        self,
        resource_type: SandboxGrantResourceType,
        pattern: str,
        operations: list[str],
        scope: SandboxGrantScope = SandboxGrantScope.WORKSPACE,
        reason: str = "",
    ):
        try:
            grant = normalize_sandbox_grant(
                resource_type=resource_type,
                scope=scope,
                pattern=pattern,
                operations=operations,
                created_by=self._user_id,
            )
        except ValueError as exc:
            return self._result(
                {
                    "authorized": False,
                    "error": str(exc),
                },
                state=ToolResultState.ERROR,
            )

        record = await self._get_permission_record(grant.scope)
        record.grants = merge_sandbox_grants(record.grants, [grant])
        await self._storage.upsert_sandbox_permissions(record)
        return self._result(
            {
                "authorized": True,
                "grant": {
                    "resource_type": grant.resource_type.value,
                    "pattern": grant.pattern,
                    "operations": [
                        operation.value for operation in grant.operations
                    ],
                },
            },
            restart_session=True,
        )

    async def _get_permission_record(
        self,
        scope: SandboxGrantScope,
    ) -> SandboxPermissionRecord:
        """Load one existing record for the target scope or create a new one."""
        if scope == SandboxGrantScope.USER:
            record = await self._storage.get_sandbox_permissions_for_user(
                self._user_id,
            )
            if record is not None:
                return record
            return SandboxPermissionRecord(
                user_id=self._user_id,
                scope=scope,
            )

        if scope == SandboxGrantScope.AGENT:
            record = await self._storage.get_sandbox_permissions_for_agent(
                self._user_id,
                self._agent_id,
            )
            if record is not None:
                return record
            return SandboxPermissionRecord(
                user_id=self._user_id,
                scope=scope,
                agent_id=self._agent_id,
            )

        record = await self._storage.get_sandbox_permissions_for_workspace(
            self._user_id,
            self._workspace_id,
        )
        if record is not None:
            return record
        return SandboxPermissionRecord(
            user_id=self._user_id,
            scope=scope,
            workspace_id=self._workspace_id,
        )
