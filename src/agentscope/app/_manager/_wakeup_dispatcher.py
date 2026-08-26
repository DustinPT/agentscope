# -*- coding: utf-8 -*-
"""Single per-process dispatcher for cross-session wake-ups.

One asyncio task per process. Subscribes to the shared wake-up signal
channel and drains the durable wake-up queue on each signal. For each
queued entry whose session is idle, spawns a background
:meth:`ChatService.run` call through the shared
:class:`ChatRunRegistry`, so the spawned task can be looked up and
cancelled by :class:`CancelDispatcher`.

All bus keys live on the :class:`MessageBus` base class (see
``enqueue_wakeup``, ``dequeue_wakeups``, ``subscribe_wakeup_signal``,
``session_is_running``), so this file has no hard-coded key strings.
"""
import asyncio
from typing import TYPE_CHECKING, Self

from .._bus_ops import enqueue_run_trigger
from ..message_bus import MessageBusKeys
from ..._logging import logger
from ...event import (
    EventType,
    ExternalExecutionResultEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from ...message import Msg

if TYPE_CHECKING:
    from ..message_bus import MessageBus
    from ..storage import StorageBase
    from .._service import ChatService
    from ._chat_run_registry import ChatRunRegistry


class WakeupDispatcher:
    """One asyncio task per process, draining the shared wake-up queue.

    Args:
        message_bus (`MessageBus`):
            Application message bus. Used for signal subscription,
            queue drain, and ``session_is_running`` checks.
        storage (`StorageBase`):
            Persistent storage backend. Consulted before spawning a
            run so wake-ups whose target session has been deleted are
            dropped instead of crashing :class:`ChatService.run`.
        chat_service (`ChatService`):
            Drives the actual chat run when waking an idle session.
        chat_run_registry (`ChatRunRegistry`):
            Per-process registry that holds the spawned task handle so
            it can be located by :class:`CancelDispatcher`.
    """

    _RECONNECT_DELAY_SECS = 1.0
    _RESUME_RETRY_BACKOFF_SECS = 0.1

    def __init__(
        self,
        message_bus: "MessageBus",
        storage: "StorageBase",
        chat_service: "ChatService",
        chat_run_registry: "ChatRunRegistry",
    ) -> None:
        """Bind dependencies.

        Args:
            message_bus (`MessageBus`):
                Application message bus.
            storage (`StorageBase`):
                Persistent storage backend.
            chat_service (`ChatService`):
                Drives idle-session wake-ups via :meth:`ChatService.run`.
            chat_run_registry (`ChatRunRegistry`):
                Shared chat-run registry to spawn into.
        """
        self._bus = message_bus
        self._storage = storage
        self._chat_service = chat_service
        self._registry = chat_run_registry
        self._task: asyncio.Task | None = None
        self._retry_tasks: set[asyncio.Task] = set()

    async def __aenter__(self) -> Self:
        """Start the dispatcher loop and wait until its bus
        subscription is live.

        Also performs an initial drain right after subscription so
        wake-ups produced while this process was down (durable in
        the queue) are picked up immediately on startup.

        Returns:
            `Self`: This dispatcher instance.
        """
        ready = asyncio.Event()
        self._task = asyncio.create_task(
            self._loop(ready),
            name="wakeup-dispatcher",
        )
        await ready.wait()
        await self._drain_and_dispatch()
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Cancel the dispatcher loop on context exit."""
        retries = list(self._retry_tasks)
        for retry in retries:
            retry.cancel()
        for retry in retries:
            try:
                await retry
            except asyncio.CancelledError:
                pass
        self._retry_tasks.clear()
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _loop(self, ready: asyncio.Event) -> None:
        """Long-lived loop: subscribe to the signal channel and drain
        the queue on every received signal.

        When the Redis pub/sub subscription drops unexpectedly, keep
        retrying with a short backoff instead of exiting permanently.
        After a successful re-subscribe, immediately drain the durable
        wake-up queue so wake-ups queued during the disconnect are
        recovered even if their transient signal was missed.

        Args:
            ready (`asyncio.Event`):
                Signalled after the underlying SUBSCRIBE completes.
                :meth:`start` blocks on this so callers can publish a
                wake-up immediately after start without racing.
        """
        attempt = 0
        while True:
            subscription_restored = False

            def _on_ready() -> None:
                nonlocal attempt, subscription_restored
                if not ready.is_set():
                    ready.set()
                    return
                subscription_restored = True
                logger.info(
                    "WakeupDispatcher: wake-up signal subscription "
                    "restored after %d failed attempt(s); draining "
                    "queued wake-ups.",
                    attempt,
                )
                asyncio.create_task(
                    self._drain_and_dispatch(),
                    name="wakeup-dispatcher-recovery-drain",
                )

            try:
                if attempt > 0:
                    logger.info(
                        "WakeupDispatcher: re-subscribing to wake-up "
                        "signal channel (attempt %d).",
                        attempt + 1,
                    )
                async for _signal in self._bus.subscribe(
                    MessageBusKeys.wakeup_signal(),
                    on_ready=_on_ready,
                ):
                    if subscription_restored:
                        attempt = 0
                        subscription_restored = False
                    await self._drain_and_dispatch()

                attempt += 1
                logger.warning(
                    "WakeupDispatcher: wake-up signal subscription "
                    "ended unexpectedly without an exception "
                    "(attempt %d); reconnecting in %.1f seconds.",
                    attempt,
                    self._RECONNECT_DELAY_SECS,
                )
            except asyncio.CancelledError:
                logger.info(
                    "WakeupDispatcher: subscription loop cancelled; "
                    "stopping dispatcher.",
                )
                raise
            except Exception:  # pylint: disable=broad-except
                attempt += 1
                logger.exception(
                    "WakeupDispatcher: wake-up signal subscription "
                    "failed (attempt %d); reconnecting in %.1f seconds.",
                    attempt,
                    self._RECONNECT_DELAY_SECS,
                )

            await asyncio.sleep(self._RECONNECT_DELAY_SECS)

    async def _drain_and_dispatch(self) -> None:
        """Read up to a batch of wake-up entries and dispatch them."""
        try:
            entries = [
                payload
                for _entry_id, payload in await self._bus.queue_drain(
                    MessageBusKeys.wakeup_queue(),
                    max_count=64,
                )
            ]
        except Exception:  # pylint: disable=broad-except
            logger.exception("WakeupDispatcher: dequeue_wakeups failed.")
            return

        for payload in entries:
            try:
                user_id = payload["user_id"]
                session_id = payload["session_id"]
                agent_id = payload["agent_id"]
                kind = payload.get("kind", "wake")
            except (KeyError, TypeError):
                logger.warning(
                    "WakeupDispatcher: skipping malformed wake-up entry %r",
                    payload,
                )
                continue

            try:
                input_msg = self._deserialize_input(
                    kind=kind,
                    payload=payload.get("input"),
                )
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "WakeupDispatcher: dropping wake-up for session %s due to "
                    "invalid input payload: %s",
                    session_id,
                    exc,
                )
                continue

            if await self._bus.is_locked(MessageBusKeys.session_lock(session_id)):
                if kind != MessageBusKeys.WAKEUP_KIND_WAKE:
                    self._schedule_retry(
                        user_id=user_id,
                        session_id=session_id,
                        agent_id=agent_id,
                        kind=kind,
                        input_msg=input_msg,
                    )
                continue

            # Orphan guard: the wake-up queue is unaware of session
            # lifecycle. A wake-up enqueued before the session was
            # deleted (e.g. by a BG task completion callback or a
            # schedule trigger) will still arrive here. Drop it
            # rather than letting ChatService.run crash on a missing
            # storage record.
            session = await self._storage.get_session_meta(user_id, session_id)
            if session is None or session.agent_id != agent_id:
                logger.warning(
                    "WakeupDispatcher: dropping wake-up for session %s "
                    "(agent %s, user %s) — session no longer exists in "
                    "storage; the wake-up was likely enqueued before "
                    "the session was deleted.",
                    session_id,
                    agent_id,
                    user_id,
                )
                continue

            try:
                self._registry.spawn(
                    self._chat_service.run(
                        user_id=user_id,
                        session_id=session_id,
                        agent_id=agent_id,
                          input_msg=input_msg,
                    ),
                    session_id=session_id,
                    name=f"wakeup-run:{session_id}",
                )
            except RuntimeError:
                # Another spawn won the race for this session in this
                # process; the existing run will drain the inbox.
                logger.debug(
                    "WakeupDispatcher: skipping wake-up for session %s; "
                    "a local run is already registered.",
                    session_id,
                )

    def _schedule_retry(
        self,
        *,
        user_id: str,
        session_id: str,
        agent_id: str,
        kind: str,
        input_msg: Msg
        | UserConfirmResultEvent
        | ExternalExecutionResultEvent
        | UserInterruptEvent
        | None,
    ) -> None:
        async def _retry() -> None:
            await asyncio.sleep(self._RESUME_RETRY_BACKOFF_SECS)
            await enqueue_run_trigger(
                self._bus,
                user_id=user_id,
                session_id=session_id,
                agent_id=agent_id,
                kind=kind,
                inputs=input_msg,
            )

        task = asyncio.create_task(
            _retry(),
            name=f"wakeup-retry:{session_id}",
        )
        self._retry_tasks.add(task)
        task.add_done_callback(self._retry_tasks.discard)

    @staticmethod
    def _deserialize_input(
        *,
        kind: str,
        payload: dict | None,
    ) -> Msg | UserConfirmResultEvent | ExternalExecutionResultEvent | UserInterruptEvent | None:
        """Deserialize a wake-up queue payload into the chat-service input."""
        if kind == "wake":
            return None
        if kind != "resume":
            raise ValueError(f"Unsupported wake-up kind: {kind}")
        if payload is None:
            raise ValueError("Resume wake-up payload is missing its input.")
        event_type = payload.get("type")
        if event_type == EventType.USER_CONFIRM_RESULT:
            return UserConfirmResultEvent.model_validate(payload)
        if event_type == EventType.EXTERNAL_EXECUTION_RESULT:
            return ExternalExecutionResultEvent.model_validate(payload)
        if event_type == EventType.USER_INTERRUPT:
            return UserInterruptEvent.model_validate(payload)
        return Msg.model_validate(payload)
