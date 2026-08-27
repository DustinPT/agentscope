# -*- coding: utf-8 -*-
"""The lifespan of the agent service."""
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncGenerator

from ._manager import (
    BackgroundTaskManager,
    CancelDispatcher,
    ChatRunRegistry,
    SchedulerManager,
    SubAgentReaper,
    WakeupDispatcher,
)
from ._service import ChannelService, ChatService, SessionService

if TYPE_CHECKING:
    from fastapi import FastAPI
else:
    FastAPI = Any


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage startup and shutdown of all application-wide resources.

    Every resource with a lifecycle is an async context manager and is
    entered through a single :class:`AsyncExitStack`. The stack tears
    them down in reverse order on shutdown — including when an entry
    later in the sequence raises during startup, so no resource leaks
    on partial failure.

    Service-layer ``ChatService`` and ``SessionService`` have no
    lifecycle of their own and are constructed inline.
    """
    storage = app.state.storage
    message_bus = app.state.message_bus
    workspace_manager = app.state.workspace_manager

    async with AsyncExitStack() as stack:
        await stack.enter_async_context(storage)
        await stack.enter_async_context(message_bus)
        await stack.enter_async_context(workspace_manager)

        bg_manager = await stack.enter_async_context(BackgroundTaskManager())
        app.state.background_task_manager = bg_manager

        # Per-process registry of in-flight chat-run asyncio tasks.
        # Entered before the wake-up + cancel dispatchers so they can
        # share the same registry; exited last so its shutdown can
        # cancel any leftover runs after the dispatchers stop.
        chat_run_registry = await stack.enter_async_context(ChatRunRegistry())
        app.state.chat_run_registry = chat_run_registry

        # Scheduler is independent of ChatService now (its fire path
        # pushes to inbox + enqueues wakeup via the bus), so we build it
        # before ChatService and inject it via the constructor.
        scheduler = await stack.enter_async_context(
            SchedulerManager(
                storage=storage,
                message_bus=message_bus,
            ),
        )
        app.state.scheduler_manager = scheduler

        channel_type_registry = app.state.channel_type_registry
        channel_clients = None
        if channel_type_registry:
            from .channel import (
                ChannelClients,
                ChannelGateway,
                ChannelLifecycleDispatcher,
            )

            channel_clients = await stack.enter_async_context(
                ChannelClients(
                    storage=storage,
                    message_bus=message_bus,
                    type_registry=channel_type_registry,
                ),
            )
            app.state.channel_service = ChannelService(
                storage=storage,
                message_bus=message_bus,
                type_registry=channel_type_registry,
            )
            await stack.enter_async_context(
                ChannelLifecycleDispatcher(
                    storage=storage,
                    message_bus=message_bus,
                    type_registry=channel_type_registry,
                    gateway=ChannelGateway(
                        storage=storage,
                        message_bus=message_bus,
                        workspace_manager=workspace_manager,
                    ),
                ).lifespan(),
            )
        app.state.channel_clients = channel_clients

        chat_service = ChatService(
            storage=storage,
            workspace_manager=workspace_manager,
            agent_asset_store=app.state.agent_asset_store,
            attachment_store=app.state.attachment_store,
            scheduler_manager=scheduler,
            background_task_manager=bg_manager,
            message_bus=message_bus,
            chat_run_registry=chat_run_registry,
            extra_agent_middlewares=app.state.extra_agent_middlewares,
            extra_agent_tools=app.state.extra_agent_tools,
            custom_subagent_templates=app.state.custom_subagent_templates,
            custom_agent_cls=app.state.custom_agent_cls,
            channel_clients=channel_clients,
        )
        app.state.chat_service = chat_service

        app.state.session_service = SessionService(
            storage=storage,
            message_bus=message_bus,
            workspace_manager=workspace_manager,
            attachment_store=app.state.attachment_store,
            chat_service=chat_service,
            chat_run_registry=chat_run_registry,
        )

        # Dispatchers need live references somewhere, or they would be
        # garbage-collected; the AsyncExitStack holds those references
        # for us, so we don't need local bindings or app.state slots.
        await stack.enter_async_context(
            WakeupDispatcher(
                message_bus=message_bus,
                storage=storage,
                chat_service=chat_service,
                chat_run_registry=chat_run_registry,
            ),
        )
        await stack.enter_async_context(
            SubAgentReaper(
                storage=storage,
                message_bus=message_bus,
            ),
        )
        await stack.enter_async_context(
            CancelDispatcher(
                message_bus=message_bus,
                registry=chat_run_registry,
                bg_manager=bg_manager,
            ),
        )

        yield
