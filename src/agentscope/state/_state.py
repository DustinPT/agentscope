# -*- coding: utf-8 -*-
"""The agent state class."""
import hashlib
import uuid
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, model_validator

from ._task import Task
from ..message import TextBlock, DataBlock, Msg
from ..permission import PermissionContext

if TYPE_CHECKING:
    from ..tool._builtin._backend import BackendBase


class FileVersionCacheEntry(BaseModel):
    """The cached proof for a file version known to the model."""

    file_path: str
    mtime_ns: int
    size_bytes: int
    sha256: str
    bytes: float
    source_kind: str

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_read_cache(
        cls,
        data: object,
    ) -> object:
        """Migrate legacy read-file cache entries to the new schema."""
        if not isinstance(data, dict):
            return data

        if {
            "file_path",
            "mtime_ns",
            "size_bytes",
            "sha256",
            "bytes",
            "source_kind",
        }.issubset(data):
            return data

        legacy_lines = data.get("lines")
        legacy_file_path = data.get("file_path")
        legacy_updated_at = data.get("updated_at")
        legacy_bytes = data.get("bytes")
        if not isinstance(legacy_lines, list) or not isinstance(
            legacy_file_path,
            str,
        ):
            return data

        joined_content = "".join(
            line for line in legacy_lines if isinstance(line, str)
        )
        content_bytes = joined_content.encode("utf-8")

        mtime_ns = 0
        if isinstance(legacy_updated_at, int | float):
            mtime_ns = int(legacy_updated_at * 1_000_000_000)

        size_bytes = len(content_bytes)
        cache_bytes = (
            float(legacy_bytes)
            if isinstance(legacy_bytes, int | float)
            else size_bytes / 1024
        )

        return {
            "file_path": legacy_file_path,
            "mtime_ns": mtime_ns,
            "size_bytes": size_bytes,
            "sha256": hashlib.sha256(content_bytes).hexdigest(),
            "bytes": cache_bytes,
            "source_kind": "read",
        }


class ToolRuntimeContext(BaseModel):
    """Lightweight runtime-only context injected before tool execution."""

    session_id: str
    """The current session id."""

    workspace_id: str
    """The workspace id bound to the current session."""

    workdir: str
    """The agent-visible workspace root directory."""

    current_tool_call_id: str | None = None
    """The current tool call id being executed, if any."""


class WaitNewMessagesCursor(BaseModel):
    """Incremental cursor tracked for one managed session wait loop."""

    last_message_id: str | None = None
    """The last fully consumed message id for the target session."""

    last_block_id: str | None = None
    """The last fully consumed block id inside ``last_message_id``."""


class ToolContext(BaseModel):
    """The tool context, e.g. tool cache"""

    max_cache_files: int = Field(default=100, gt=1)
    """The maximum number of cached files."""
    max_cache_bytes: float = Field(default=25000, gt=10000)
    """The maximum size of the accumulated read file cache."""
    read_file_cache: list[FileVersionCacheEntry] = Field(
        default_factory=list,
    )
    """The cache for Read/Write/Edit file tools."""

    activated_groups: list[str] = Field(default_factory=list)
    """The names of the activated tool groups, each group contains a set of
    tools."""

    wait_new_messages_cursors: dict[str, WaitNewMessagesCursor] = Field(
        default_factory=dict,
    )
    """Per-target-session incremental cursors for ``WaitNewMessages``."""

    runtime_context: ToolRuntimeContext | None = Field(
        default=None,
        exclude=True,
    )
    """Runtime-only tool context refreshed by the service layer each run.

    This field is intentionally excluded from default serialization so it does
    not leak into persisted session state or generic snapshots.
    """

    async def get_cache(
        self,
        file_path: str,
    ) -> FileVersionCacheEntry | None:
        """Get cached file version info by path.

        Args:
            file_path: The absolute path of the file.

        Returns:
            The cached entry if present, otherwise None.
        """
        for entry in self.read_file_cache:
            if entry.file_path == file_path:
                return entry
        return None

    async def validate_cached_version(
        self,
        file_path: str,
        backend: "BackendBase",
        cache_entry: FileVersionCacheEntry | None = None,
    ) -> bool:
        """Validate whether the current file still matches cached version.

        Args:
            file_path: The absolute path of the file.
            backend: Workspace-aware file backend.
            cache_entry: Optional cached entry to validate.

        Returns:
            True if the file still matches the cached version, otherwise
            False. When the version no longer matches, the cache entry is
            removed.
        """
        cache_entry = cache_entry or await self.get_cache(file_path)
        if cache_entry is None:
            return False

        try:
            entry = await backend.stat(file_path)
            if entry is None:
                raise FileNotFoundError(file_path)
            current_mtime_ns = (
                int(entry.mtime * 1_000_000_000)
                if entry.mtime is not None
                else 0
            )
            current_size_bytes = (
                entry.size_bytes
                if entry.size_bytes is not None
                else cache_entry.size_bytes
            )
        except Exception:
            self.read_file_cache = [
                entry
                for entry in self.read_file_cache
                if entry.file_path != file_path
            ]
            return False

        if (
            current_mtime_ns != 0
            and current_mtime_ns == cache_entry.mtime_ns
            and current_size_bytes == cache_entry.size_bytes
        ):
            return True

        content = await backend.read_file(file_path)

        content_sha256 = hashlib.sha256(content).hexdigest()
        if content_sha256 == cache_entry.sha256:
            cache_entry.mtime_ns = current_mtime_ns
            cache_entry.size_bytes = current_size_bytes
            cache_entry.bytes = current_size_bytes / 1024
            return True

        self.read_file_cache = [
            entry
            for entry in self.read_file_cache
            if entry.file_path != file_path
        ]
        return False

    async def cache_file_version(
        self,
        file_path: str,
        backend: "BackendBase",
        source_kind: str,
        content: str | bytes | None = None,
    ) -> None:
        """Cache the current file version with LRU eviction.

        Args:
            file_path: The absolute path of the file.
            backend: Workspace-aware file backend.
            source_kind: Which tool produced this version proof.
            content: Optional content bytes or text to hash. When omitted,
                the file is read from disk.
        """
        if content is None:
            try:
                content_bytes = await backend.read_file(file_path)
            except Exception:
                return
        elif isinstance(content, str):
            content_bytes = content.encode("utf-8")
        else:
            content_bytes = content

        try:
            entry = await backend.stat(file_path)
            if entry is None:
                mtime_ns = 0
                size_bytes = len(content_bytes)
            else:
                mtime_ns = (
                    int(entry.mtime * 1_000_000_000)
                    if entry.mtime is not None
                    else 0
                )
                size_bytes = (
                    entry.size_bytes
                    if entry.size_bytes is not None
                    else len(content_bytes)
                )
        except Exception:
            mtime_ns = 0
            size_bytes = len(content_bytes)

        # Calculate size in KB
        new_entry_bytes = size_bytes / 1024

        # Remove existing cache for this file if present
        self.read_file_cache = [
            entry
            for entry in self.read_file_cache
            if entry.file_path != file_path
        ]

        # Evict the oldest entries if exceeding max_cache_files
        while len(self.read_file_cache) >= self.max_cache_files:
            self.read_file_cache.pop(0)

        # Evict the oldest entries if exceeding max_cache_bytes
        current_size = sum(entry.bytes for entry in self.read_file_cache)
        while (
            self.read_file_cache
            and current_size + new_entry_bytes > self.max_cache_bytes
        ):
            removed = self.read_file_cache.pop(0)
            current_size -= removed.bytes

        # Add new entry to the end (most recent)
        self.read_file_cache.append(
            FileVersionCacheEntry(
                file_path=file_path,
                mtime_ns=mtime_ns,
                size_bytes=size_bytes,
                sha256=hashlib.sha256(content_bytes).hexdigest(),
                bytes=new_entry_bytes,
                source_kind=source_kind,
            ),
        )

    async def clean_file_cache(
        self,
        reserved_file_paths: set[str] | None = None,
    ) -> None:
        """Drop read caches whose paths are not in ``reserved_file_paths``.

        Args:
            reserved_file_paths: File paths from Read calls that remain in the
                context. Caches for these files are kept; all others are
                evicted.
        """
        reserved_file_paths = reserved_file_paths or set()

        self.read_file_cache = [
            entry
            for entry in self.read_file_cache
            if entry.file_path in reserved_file_paths
        ]


class TaskContext(BaseModel):
    """The task context."""

    tasks: list[Task] = Field(default_factory=lambda: [])
    """The task context."""


class AgentState(BaseModel):
    """The agent state that should be saved and loaded from storage."""

    session_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    """The session id of the agent. Normally, each session will maintain one
    independent agent state for each agent."""

    summary: str | list[TextBlock | DataBlock] = ""
    """The compressed summary of the context, which will be prepended to the
    context when feed into the LLM."""
    context: list[Msg] = Field(default_factory=list)
    """The uncompressed conversation context, that will be feed into the LLM"""
    reply_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    """The id of the current reply, which is also used as the id of the
    final message of the reply."""
    cur_iter: int = 0
    """The current iteration of the agent's reasoning-acting loop."""

    # =================================================================
    # The permission context
    # =================================================================
    permission_context: PermissionContext = Field(
        default_factory=PermissionContext,
    )
    """The permission context that will be passed to the toolkit to determine
    the tool permissions."""

    # =================================================================
    # The tool context
    # =================================================================
    tool_context: ToolContext = Field(default_factory=ToolContext)

    # =================================================================
    # The tasks context
    # =================================================================
    tasks_context: TaskContext = Field(default_factory=TaskContext)
    """The task context that records the agent tasks."""
