# -*- coding: utf-8 -*-
"""Centralised registry of message-bus key/namespace conventions used
by application-layer services.

:class:`~agentscope.app.message_bus.MessageBus` itself stays
domain-agnostic — it exposes only generic primitives
(``publish`` / ``subscribe`` / ``queue_*`` / ``log_*`` / ``registry_*``
/ ``acquire_lock``). All business-specific key formats live here so
they can be audited, migrated, and (eventually) ported off from the
current scattered ``_BASE_…_KEY`` constants on ``MessageBus``.

Add new business keys here as needed. As legacy keys are migrated off
``MessageBus``, they should move into this class as well.
"""

from typing import Final


class MessageBusKeys:  # pylint: disable=too-many-public-methods
    """Application-layer key conventions for the message bus.

    A flat registry of key/namespace builders — it grows one method per
    business key, so the public-method count is expected to be high.
    """

    WAKEUP_KIND_WAKE: Final = "wake"
    WAKEUP_KIND_RESUME: Final = "resume"
    WAKEUP_KIND_MESSAGE: Final = "message"

    _PROJECTION_NS = "agentscope:session:projection:{sid}"

    @classmethod
    def projection_namespace(cls, target_session_id: str) -> str:
        """Return the registry namespace for a session's projections."""
        return cls._PROJECTION_NS.format(sid=target_session_id)

    @staticmethod
    def projection_field(kind: str, entry_id: str) -> str:
        """Return the hash field key for a single projected entry."""
        return f"{kind}:{entry_id}"

    @staticmethod
    def projection_field_prefix(kind: str) -> str:
        """Return the field-key prefix that identifies one feed."""
        return f"{kind}:"

    _SESSION_EVENTS = "agentscope:session:events:{sid}"

    SESSION_REPLAY_MAX_LEN = 1000

    @classmethod
    def session_events(cls, session_id: str) -> str:
        """Replay log + live pub/sub channel key for a session."""
        return cls._SESSION_EVENTS.format(sid=session_id)

    _SESSION_LOCK = "agentscope:session:lock:{sid}"

    SESSION_RUN_TTL_SECS = 600

    @classmethod
    def session_lock(cls, session_id: str) -> str:
        """Per-session distributed-lock key."""
        return cls._SESSION_LOCK.format(sid=session_id)

    _INBOX = "agentscope:inbox:{sid}"
    _INBOX_LOCK = "agentscope:inbox:lock:{sid}"
    _INBOX_CONSUMER = "agentscope:inbox:consumer:{sid}"

    INBOX_LOCK_TTL_SECS = 30
    INBOX_CONSUMER_FIELD = "running"

    @classmethod
    def inbox(cls, session_id: str) -> str:
        """Per-session inbox drain-queue key."""
        return cls._INBOX.format(sid=session_id)

    @classmethod
    def inbox_lock(cls, session_id: str) -> str:
        """Per-session lock serialising inbox hand-off."""
        return cls._INBOX_LOCK.format(sid=session_id)

    @classmethod
    def inbox_consumer(cls, session_id: str) -> str:
        """Per-session registry recording whether a run is consuming it."""
        return cls._INBOX_CONSUMER.format(sid=session_id)

    _WAKEUP_QUEUE = "agentscope:wakeups"
    _WAKEUP_SIGNAL = "agentscope:wakeup_signal"

    @classmethod
    def wakeup_queue(cls) -> str:
        """Shared run-trigger queue key."""
        return cls._WAKEUP_QUEUE

    @classmethod
    def wakeup_signal(cls) -> str:
        """Shared signal channel that nudges dispatchers to drain."""
        return cls._WAKEUP_SIGNAL

    _SESSION_CANCEL = "agentscope:session:cancel"
    _TASK_CANCEL = "agentscope:task:cancel"
    _SESSION_INTERRUPT = "agentscope:session:interrupt"

    @classmethod
    def session_cancel_channel(cls) -> str:
        """Global session-cancel broadcast channel."""
        return cls._SESSION_CANCEL

    @classmethod
    def task_cancel_channel(cls) -> str:
        """Single-task cancel broadcast channel."""
        return cls._TASK_CANCEL

    @classmethod
    def session_interrupt_channel(cls) -> str:
        """Global session-interrupt broadcast channel."""
        return cls._SESSION_INTERRUPT

    _BG_TASKS = "agentscope:bg_tasks:{sid}"

    BG_TASKS_TTL_SECS = 86400

    @classmethod
    def bg_tasks(cls, session_id: str) -> str:
        """Per-session background task registry key."""
        return cls._BG_TASKS.format(sid=session_id)

    _INDEX_TASKS_QUEUE = "agentscope:index:tasks"
    _INDEX_TASKS_SIGNAL = "agentscope:index:tasks:wake"

    @classmethod
    def index_tasks_queue(cls) -> str:
        """Shared, durable index-task queue."""
        return cls._INDEX_TASKS_QUEUE

    @classmethod
    def index_tasks_signal(cls) -> str:
        """Shared pub/sub channel for index-task consumers."""
        return cls._INDEX_TASKS_SIGNAL

    _CHANNEL_LIFECYCLE = "agentscope:channel:lifecycle"
    _CHANNEL_LIVENESS = "agentscope:channel:liveness:{cid}"
    _CHANNEL_MEDIA = "agentscope:channel:media:{cid}:{chat}:{uid}"
    _CHANNEL_SEEN_CHATS = "agentscope:channel:seen_chats:{cid}"

    @classmethod
    def channel_lifecycle(cls) -> str:
        """Pub/sub channel that nudges channel reconciliation."""
        return cls._CHANNEL_LIFECYCLE

    @classmethod
    def channel_liveness(cls, channel_id: str) -> str:
        """Per-channel per-node status heartbeat namespace."""
        return cls._CHANNEL_LIVENESS.format(cid=channel_id)

    @classmethod
    def channel_media_buffer(
        cls,
        channel_id: str,
        chat_id: str,
        user_id: str,
    ) -> str:
        """Queue key buffering media until the next text message."""
        return cls._CHANNEL_MEDIA.format(
            cid=channel_id,
            chat=chat_id,
            uid=user_id,
        )

    @classmethod
    def channel_seen_chats(cls, channel_id: str) -> str:
        """Registry namespace of chat_ids the bot has been messaged in."""
        return cls._CHANNEL_SEEN_CHATS.format(cid=channel_id)
