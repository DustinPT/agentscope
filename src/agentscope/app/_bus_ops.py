# -*- coding: utf-8 -*-
"""Business-level operations built on top of MessageBus primitives."""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from .message_bus._keys import MessageBusKeys

if TYPE_CHECKING:
    from .message_bus._base import MessageBus

    from agentscope.event import (
        ExternalExecutionResultEvent,
        UserConfirmResultEvent,
        UserInterruptEvent,
    )
    from agentscope.message import Msg


async def publish_session_event(
    bus: "MessageBus",
    session_id: str,
    event: dict,
) -> str:
    """Append an event to replay log + live pub/sub."""
    key = MessageBusKeys.session_events(session_id)
    entry_id = await bus.log_append(
        key,
        event,
        max_len=MessageBusKeys.SESSION_REPLAY_MAX_LEN,
    )
    await bus.publish(key, {**event, "_entry_id": entry_id})
    return entry_id


async def enqueue_run_trigger(
    bus: "MessageBus",
    user_id: str,
    session_id: str,
    agent_id: str,
    *,
    kind: Literal[
        "wake",
        "resume",
        "message",
    ] = MessageBusKeys.WAKEUP_KIND_WAKE,
    inputs: UserConfirmResultEvent
    | ExternalExecutionResultEvent
    | UserInterruptEvent
    | Msg
    | None = None,
) -> None:
    """Enqueue a typed run trigger and signal dispatchers."""
    await bus.queue_push(
        MessageBusKeys.wakeup_queue(),
        {
            "user_id": user_id,
            "session_id": session_id,
            "agent_id": agent_id,
            "kind": kind,
            "input": inputs.model_dump(mode="json") if inputs else None,
        },
    )
    await bus.publish(MessageBusKeys.wakeup_signal(), {})


async def deliver_to_inbox(
    bus: "MessageBus",
    *,
    user_id: str,
    session_id: str,
    agent_id: str,
    payload: dict,
) -> None:
    """Push to session inbox and wake only when no consumer is active."""
    async with bus.acquire_lock(
        MessageBusKeys.inbox_lock(session_id),
        ttl_secs=MessageBusKeys.INBOX_LOCK_TTL_SECS,
    ):
        await bus.queue_push(MessageBusKeys.inbox(session_id), payload)
        consumer = await bus.registry_get(
            MessageBusKeys.inbox_consumer(session_id),
            MessageBusKeys.INBOX_CONSUMER_FIELD,
        )

    if consumer is None:
        await enqueue_run_trigger(
            bus,
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
        )


async def register_inbox_consumer(bus: "MessageBus", session_id: str) -> None:
    """Mark this run as the consumer of one session inbox."""
    await bus.registry_set(
        MessageBusKeys.inbox_consumer(session_id),
        MessageBusKeys.INBOX_CONSUMER_FIELD,
        "1",
        ttl_secs=MessageBusKeys.SESSION_RUN_TTL_SECS,
    )


async def has_pending_inbox_or_release(
    bus: "MessageBus",
    session_id: str,
) -> bool:
    """Report whether inbox still has payloads, releasing consumer if not."""
    inbox = MessageBusKeys.inbox(session_id)
    async with bus.acquire_lock(
        MessageBusKeys.inbox_lock(session_id),
        ttl_secs=MessageBusKeys.INBOX_LOCK_TTL_SECS,
    ):
        payloads: list[dict] = []
        while True:
            batch = await bus.queue_drain(inbox, max_count=100)
            if not batch:
                break
            payloads.extend(payload for _entry_id, payload in batch)

        if not payloads:
            await bus.registry_del(
                MessageBusKeys.inbox_consumer(session_id),
                MessageBusKeys.INBOX_CONSUMER_FIELD,
            )
            return False

        for payload in payloads:
            await bus.queue_push(inbox, payload)
        return True


async def abandon_inbox_consumer(
    bus: "MessageBus",
    *,
    user_id: str,
    session_id: str,
    agent_id: str,
) -> None:
    """Release inbox consumer registration and re-wake if payloads remain."""
    pending = await has_pending_inbox_or_release(bus, session_id)
    if not pending:
        return

    await bus.registry_del(
        MessageBusKeys.inbox_consumer(session_id),
        MessageBusKeys.INBOX_CONSUMER_FIELD,
    )
    await enqueue_run_trigger(
        bus,
        user_id=user_id,
        session_id=session_id,
        agent_id=agent_id,
    )


async def enqueue_index_task(
    bus: "MessageBus",
    user_id: str,
    knowledge_base_id: str,
    document_id: str,
) -> None:
    """Enqueue a knowledge-document indexing task and signal consumers."""
    await bus.queue_push(
        MessageBusKeys.index_tasks_queue(),
        {
            "user_id": user_id,
            "knowledge_base_id": knowledge_base_id,
            "document_id": document_id,
        },
    )
    await bus.publish(MessageBusKeys.index_tasks_signal(), {})
