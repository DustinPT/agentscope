# -*- coding: utf-8 -*-
"""Persisted sandbox permission models and normalization helpers."""
from __future__ import annotations

import os
from enum import StrEnum
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from ._base import _RecordBase


class SandboxGrantScope(StrEnum):
    """Storage scope for one sandbox permission record."""

    WORKSPACE = "workspace"
    AGENT = "agent"
    USER = "user"


class SandboxGrantResourceType(StrEnum):
    """Supported sandbox resource types."""

    DOMAIN = "domain"
    PATH = "path"


class SandboxOperation(StrEnum):
    """Supported sandbox operations."""

    CONNECT = "connect"
    READ = "read"
    WRITE = "write"


class SandboxPermissionGrant(BaseModel):
    """One normalized sandbox permission rule."""

    resource_type: SandboxGrantResourceType = Field(
        description="Resource type controlled by this grant.",
    )
    scope: SandboxGrantScope = Field(
        description="Persistence scope of this grant.",
    )
    pattern: str = Field(
        description="Normalized wildcard-capable domain or path pattern.",
        min_length=1,
    )
    operations: list[SandboxOperation] = Field(
        description="Allowed operations for the resource pattern.",
        min_length=1,
    )
    created_by: str | None = Field(
        default=None,
        description="User id that granted or requested this permission.",
    )


class SandboxPermissionRecord(_RecordBase):
    """Persisted sandbox grants for one user / agent / workspace scope."""

    user_id: str = Field(description="Owner user id.")
    scope: SandboxGrantScope = Field(description="Record scope.")
    agent_id: str | None = Field(
        default=None,
        description="Agent id when scope is agent.",
    )
    workspace_id: str | None = Field(
        default=None,
        description="Workspace id when scope is workspace.",
    )
    grants: list[SandboxPermissionGrant] = Field(
        default_factory=list,
        description="Normalized grants stored for this scope.",
    )


def normalize_domain_pattern(pattern: str) -> str:
    """Normalize one domain pattern to host-only lowercase form."""
    value = (pattern or "").strip()
    if not value:
        raise ValueError("Domain pattern must not be empty.")

    parsed = urlsplit(value if "://" in value else f"//{value}")
    host = parsed.netloc or parsed.path
    host = host.split("/")[0].strip().lower()
    if not host:
        raise ValueError("Domain pattern must include a host.")
    if "@" in host:
        host = host.rsplit("@", 1)[-1]
    if ":" in host and not host.startswith("["):
        host = host.split(":", 1)[0]
    if host.startswith("*."):
        suffix = host[2:]
        if not suffix:
            raise ValueError("Wildcard domain pattern must include a suffix.")
        return f"*.{suffix}"
    return host


def normalize_path_pattern(pattern: str) -> str:
    """Normalize one filesystem path pattern while preserving wildcards."""
    value = os.path.expanduser((pattern or "").strip())
    if not value:
        raise ValueError("Path pattern must not be empty.")
    if not os.path.isabs(value):
        value = os.path.abspath(value)
    return os.path.normpath(value)


def normalize_operations(
    resource_type: SandboxGrantResourceType,
    operations: list[str] | list[SandboxOperation] | None,
) -> list[SandboxOperation]:
    """Normalize one operation list for a specific resource type."""
    raw_values = [str(item).strip().lower() for item in (operations or []) if str(item).strip()]
    if not raw_values:
        raw_values = (
            [SandboxOperation.CONNECT.value]
            if resource_type == SandboxGrantResourceType.DOMAIN
            else [SandboxOperation.READ.value]
        )

    normalized: list[SandboxOperation] = []
    for value in raw_values:
        if value == SandboxOperation.CONNECT.value:
            op = SandboxOperation.CONNECT
        elif value == SandboxOperation.READ.value:
            op = SandboxOperation.READ
        elif value == SandboxOperation.WRITE.value:
            op = SandboxOperation.WRITE
        else:
            raise ValueError(f"Unsupported sandbox operation: {value}")
        if op not in normalized:
            normalized.append(op)

    if resource_type == SandboxGrantResourceType.DOMAIN:
        if normalized != [SandboxOperation.CONNECT]:
            raise ValueError("Domain permissions only support the 'connect' operation.")
        return normalized

    if SandboxOperation.CONNECT in normalized:
        raise ValueError("Path permissions do not support the 'connect' operation.")
    if SandboxOperation.WRITE in normalized and SandboxOperation.READ not in normalized:
        normalized.insert(0, SandboxOperation.READ)
    return normalized


def normalize_sandbox_grant(
    *,
    resource_type: SandboxGrantResourceType | str,
    scope: SandboxGrantScope | str,
    pattern: str,
    operations: list[str] | list[SandboxOperation] | None,
    created_by: str | None = None,
) -> SandboxPermissionGrant:
    """Build one normalized sandbox permission grant."""
    normalized_resource_type = SandboxGrantResourceType(resource_type)
    normalized_scope = SandboxGrantScope(scope)
    normalized_pattern = (
        normalize_domain_pattern(pattern)
        if normalized_resource_type == SandboxGrantResourceType.DOMAIN
        else normalize_path_pattern(pattern)
    )
    normalized_operations = normalize_operations(
        normalized_resource_type,
        operations,
    )
    return SandboxPermissionGrant(
        resource_type=normalized_resource_type,
        scope=normalized_scope,
        pattern=normalized_pattern,
        operations=normalized_operations,
        created_by=created_by,
    )


def sandbox_grant_identity(
    grant: SandboxPermissionGrant,
) -> tuple[str, str, tuple[str, ...]]:
    """Return the stable dedupe key for one grant."""
    return (
        grant.resource_type.value,
        grant.pattern,
        tuple(op.value for op in grant.operations),
    )


def merge_sandbox_grants(
    *grant_groups: list[SandboxPermissionGrant],
) -> list[SandboxPermissionGrant]:
    """Merge grant groups in order while preserving first occurrence."""
    merged: list[SandboxPermissionGrant] = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    for group in grant_groups:
        for grant in group:
            identity = sandbox_grant_identity(grant)
            if identity in seen:
                continue
            seen.add(identity)
            merged.append(grant)
    return merged
