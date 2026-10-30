# -*- coding: utf-8 -*-
"""Generic cross-session UI projection primitive."""
import json
from typing import TYPE_CHECKING

from ..message_bus import MessageBusKeys
from .._bus_ops import publish_session_event
from ...event import CustomEvent

if TYPE_CHECKING:
    from ..message_bus import MessageBus


class SessionProjection:
    """Durable per-session store of UI cards projected from elsewhere."""

    def __init__(self, message_bus: "MessageBus") -> None:
        """Bind the shared application message bus."""
        self._bus = message_bus

    async def upsert(
        self,
        target_sid: str,
        kind: str,
        entry_id: str,
        payload: dict,
    ) -> None:
        """Persist or overwrite one projected entry."""
        await self._bus.registry_set(
            MessageBusKeys.projection_namespace(target_sid),
            MessageBusKeys.projection_field(kind, entry_id),
            json.dumps(payload, ensure_ascii=False),
        )

    async def delete(
        self,
        target_sid: str,
        kind: str,
        entry_id: str,
    ) -> None:
        """Remove one projected entry."""
        await self._bus.registry_del(
            MessageBusKeys.projection_namespace(target_sid),
            MessageBusKeys.projection_field(kind, entry_id),
        )

    async def list(
        self,
        target_sid: str,
        kind: str,
    ) -> list[dict]:
        """Return every stored entry in one projection feed."""
        raw = await self._bus.registry_getall(
            MessageBusKeys.projection_namespace(target_sid),
        )
        prefix = MessageBusKeys.projection_field_prefix(kind)
        return [
            json.loads(value)
            for field, value in raw.items()
            if field.startswith(prefix)
        ]

    async def purge(
        self,
        target_sid: str,
        kind: str | None = None,
    ) -> None:
        """Drop projected entries for one target session."""
        if kind is None:
            await self._bus.registry_drop(
                MessageBusKeys.projection_namespace(target_sid),
            )
            return
        raw = await self._bus.registry_getall(
            MessageBusKeys.projection_namespace(target_sid),
        )
        prefix = MessageBusKeys.projection_field_prefix(kind)
        for field in raw:
            if field.startswith(prefix):
                await self._bus.registry_del(
                    MessageBusKeys.projection_namespace(target_sid),
                    field,
                )

    async def publish(
        self,
        target_sid: str,
        event_name: str,
        value: dict,
    ) -> None:
        """Emit a live custom event onto the target session channel."""
        await publish_session_event(
            self._bus,
            target_sid,
            CustomEvent(
                name=event_name,
                value=value,
            ).model_dump(mode="json"),
        )
