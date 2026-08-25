# -*- coding: utf-8 -*-
"""Single per-process dispatcher for cancel and interrupt broadcasts."""
import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Self

from ..._logging import logger

if TYPE_CHECKING:
    from ..message_bus import MessageBus
    from ._background_task_manager import BackgroundTaskManager
    from ._chat_run_registry import ChatRunRegistry


class CancelDispatcher:
    """Subscribe to cancel/interrupt channels and fan them into local tasks."""

    _RECONNECT_DELAY_SECS = 1.0

    def __init__(
        self,
        message_bus: "MessageBus",
        registry: "ChatRunRegistry",
        bg_manager: "BackgroundTaskManager",
    ) -> None:
        self._bus = message_bus
        self._registry = registry
        self._bg_manager = bg_manager
        self._tasks: list[asyncio.Task] = []

    async def __aenter__(self) -> Self:
        ready_cancel = asyncio.Event()
        ready_task = asyncio.Event()
        ready_interrupt = asyncio.Event()
        self._tasks = [
            asyncio.create_task(
                self._subscription_loop(
                    loop_name="session cancel",
                    ready=ready_cancel,
                    subscribe_factory=self._bus.session_subscribe_cancel,
                    handler=self._handle_session_cancel,
                ),
                name="cancel-dispatcher:session-cancel",
            ),
            asyncio.create_task(
                self._subscription_loop(
                    loop_name="task cancel",
                    ready=ready_task,
                    subscribe_factory=self._bus.task_subscribe_cancel,
                    handler=self._handle_task_cancel,
                ),
                name="cancel-dispatcher:task-cancel",
            ),
            asyncio.create_task(
                self._subscription_loop(
                    loop_name="session interrupt",
                    ready=ready_interrupt,
                    subscribe_factory=self._bus.session_subscribe_interrupt,
                    handler=self._handle_session_interrupt,
                ),
                name="cancel-dispatcher:session-interrupt",
            ),
        ]
        await asyncio.gather(
            ready_cancel.wait(),
            ready_task.wait(),
            ready_interrupt.wait(),
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if not self._tasks:
            return
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks = []

    async def _subscription_loop(
        self,
        *,
        loop_name: str,
        ready: asyncio.Event,
        subscribe_factory: Callable[..., object],
        handler: Callable[[str], None],
    ) -> None:
        """Subscribe with reconnect and dispatch each string payload."""
        attempt = 0
        while True:
            def _on_ready() -> None:
                nonlocal attempt
                if not ready.is_set():
                    ready.set()
                    return
                logger.info(
                    "CancelDispatcher: %s subscription restored after %d "
                    "failed attempt(s).",
                    loop_name,
                    attempt,
                )

            try:
                if attempt > 0:
                    logger.info(
                        "CancelDispatcher: re-subscribing to %s channel "
                        "(attempt %d).",
                        loop_name,
                        attempt + 1,
                    )
                async for identifier in subscribe_factory(on_ready=_on_ready):
                    if attempt > 0:
                        attempt = 0
                    handler(identifier)

                attempt += 1
                logger.warning(
                    "CancelDispatcher: %s subscription ended unexpectedly "
                    "without an exception (attempt %d); reconnecting in %.1f "
                    "seconds.",
                    loop_name,
                    attempt,
                    self._RECONNECT_DELAY_SECS,
                )
            except asyncio.CancelledError:
                logger.info(
                    "CancelDispatcher: %s loop cancelled; stopping dispatcher.",
                    loop_name,
                )
                raise
            except Exception:  # pylint: disable=broad-except
                attempt += 1
                logger.exception(
                    "CancelDispatcher: %s subscription failed (attempt %d); "
                    "reconnecting in %.1f seconds.",
                    loop_name,
                    attempt,
                    self._RECONNECT_DELAY_SECS,
                )

            await asyncio.sleep(self._RECONNECT_DELAY_SECS)

    def _handle_session_cancel(self, session_id: str) -> None:
        """Hard-cancel local chat run and session-scoped background tasks."""
        task = self._registry.get(session_id)
        if task is not None and not task.done():
            logger.info(
                "CancelDispatcher: cancelling local chat run for session %s",
                session_id,
            )
            task.cancel()

        bg_cancelled = self._bg_manager.cancel_session_tasks(session_id)
        if bg_cancelled:
            logger.info(
                "CancelDispatcher: cancelled %d local BG task(s) for session %s",
                bg_cancelled,
                session_id,
            )

    def _handle_task_cancel(self, task_id: str) -> None:
        """Cancel one local background task if it is registered here."""
        if self._bg_manager.cancel_task(task_id):
            logger.info(
                "CancelDispatcher: cancelled local background task %s",
                task_id,
            )

    def _handle_session_interrupt(self, session_id: str) -> None:
        """Gracefully interrupt a local chat run without touching BG tasks."""
        task = self._registry.get(session_id)
        if task is not None and not task.done():
            logger.info(
                "CancelDispatcher: interrupting local chat run for session %s",
                session_id,
            )
            task.cancel()
