"""Normalization helpers for sandbox-runtime violation records."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

_DENY_REGEX = re.compile(r"deny(?:\(\d+\))?\s+([a-z0-9-]+)\s+(.+)$")
_PROCESS_NAME_REGEX = re.compile(r"^(?P<process>[^()\s]+)\(\d+\)\s+deny")
_TRAILING_REASON_REGEX = re.compile(r"^(?P<target>.+?)\s+\((?P<reason>[^()]+)\)$")
_HOST_PORT_REGEX = re.compile(r"^(?P<host>.+?):(?P<port>\d+)$")
_INTERNAL_SOCKET_REGEX = re.compile(
    r"^/private/tmp/aha_sbox_ipc_[^/]+/(?:log|event)\.sock$",
)


@dataclass(frozen=True, slots=True)
class SandboxViolationRecord:
    """One normalized sandbox violation attached to a tool call."""

    raw_line: str
    resource_type: str
    operation: str
    resource: str
    reason: str
    tool_call_id: str
    tool_name: str
    process_name: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        """Return a JSON-serializable representation."""
        return asdict(self)


def normalize_violations(
    raw_lines: list[str],
    *,
    tool_call_id: str,
    tool_name: str,
) -> list[SandboxViolationRecord]:
    """Normalize raw sandbox-runtime violation lines for hint generation."""
    records: list[SandboxViolationRecord] = []
    seen: set[tuple[str, str, str]] = set()

    for raw_line in raw_lines:
        record = _parse_violation(
            raw_line.strip(),
            tool_call_id=tool_call_id,
            tool_name=tool_name,
        )
        if record is None:
            continue
        dedupe_key = (record.resource_type, record.operation, record.resource)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        records.append(record)

    return records


def _parse_violation(
    raw_line: str,
    *,
    tool_call_id: str,
    tool_name: str,
) -> SandboxViolationRecord | None:
    match = _DENY_REGEX.search(raw_line)
    if not match:
        return None

    kind = match.group(1)
    target = match.group(2).strip()
    target, trailing_reason = _split_trailing_reason(target)
    process_name = _extract_process_name(raw_line)

    if kind.startswith("file-read"):
        if _INTERNAL_SOCKET_REGEX.match(target):
            return None
        return SandboxViolationRecord(
            raw_line=raw_line,
            resource_type="path",
            operation="read",
            resource=_normalize_path_resource(target),
            reason=trailing_reason or kind,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            process_name=process_name,
        )

    if kind.startswith("file-write") or kind in {
        "file-rename",
        "file-unlink",
        "file-remove",
    }:
        if _INTERNAL_SOCKET_REGEX.match(target):
            return None
        return SandboxViolationRecord(
            raw_line=raw_line,
            resource_type="path",
            operation="write",
            resource=_normalize_path_resource(target),
            reason=trailing_reason or kind,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            process_name=process_name,
        )

    if kind in {"network-outbound", "network-bind"}:
        if _INTERNAL_SOCKET_REGEX.match(target):
            return None
        if target.startswith("/"):
            return SandboxViolationRecord(
                raw_line=raw_line,
                resource_type="unix_socket",
                operation="connect",
                resource=_normalize_path_resource(target),
                reason=trailing_reason or kind,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                process_name=process_name,
            )
        host = _extract_host(target)
        return SandboxViolationRecord(
            raw_line=raw_line,
            resource_type="domain",
            operation="connect",
            resource=host,
            reason=trailing_reason or kind,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            process_name=process_name,
        )

    return None


def _split_trailing_reason(target: str) -> tuple[str, str | None]:
    match = _TRAILING_REASON_REGEX.match(target)
    if match is None:
        return target, None
    return match.group("target"), match.group("reason")


def _extract_host(target: str) -> str:
    match = _HOST_PORT_REGEX.match(target)
    if match is None:
        return target
    return match.group("host")


def _extract_process_name(raw_line: str) -> str | None:
    match = _PROCESS_NAME_REGEX.match(raw_line)
    if match is None:
        return None
    return match.group("process")


def _normalize_path_resource(path: str) -> str:
    if path.startswith("/private/tmp/"):
        return path.removeprefix("/private")  # /tmp/...
    if path.startswith("/private/var/tmp/"):
        return path.removeprefix("/private")  # /var/tmp/...
    return path
