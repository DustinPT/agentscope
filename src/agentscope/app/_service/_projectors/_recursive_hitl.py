# -*- coding: utf-8 -*-
"""Project nested-session HITL onto the top-most owning session."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from ...._logging import logger
from ....event import (
    ExternalExecutionResultEvent,
    ReplyEndEvent,
    RequireExternalExecutionEvent,
    RequireUserConfirmEvent,
    UserConfirmResultEvent,
)
from ...storage import SessionSource

if TYPE_CHECKING:
    from ...channel import ChannelClients
    from ...storage import AgentRecord, ScheduleRecord, SessionRecord, StorageBase
    from ....event import AgentEvent
    from .._session_projection import SessionProjection


class RecursiveHitlProjector:
    """Project child/schedule-session HITL onto the resolved top session."""

    KIND = "recursive_hitl"
    EVT_REQUIRE = "projected_hitl_require"
    EVT_RESULT = "projected_hitl_resolved"

    def __init__(
        self,
        storage: "StorageBase",
        channel_clients: "ChannelClients | None" = None,
    ) -> None:
        """Bind storage and optional channel clients for live delivery."""
        self._storage = storage
        self._channel_clients = channel_clients

    @staticmethod
    def entry_id(
        leaf_session_id: str,
        reply_id: str,
    ) -> str:
        """Build the unique id for one projected request."""
        return f"{leaf_session_id}:{reply_id}"

    async def resolve_target_session(
        self,
        user_id: str,
        session_record: "SessionRecord",
    ) -> tuple["SessionRecord | None", list[str], str]:
        """Public wrapper used by delete/reconcile call sites."""
        return await self._resolve_target_session(user_id, session_record)

    async def maybe_project(
        self,
        user_id: str,
        session_record: "SessionRecord",
        agent_record: "AgentRecord",
        event: "AgentEvent",
        projection: "SessionProjection",
    ) -> None:
        """Mirror one nested-session HITL event onto its top session."""
        if not isinstance(
            event,
            (
                RequireUserConfirmEvent,
                RequireExternalExecutionEvent,
                UserConfirmResultEvent,
                ExternalExecutionResultEvent,
                ReplyEndEvent,
            ),
        ):
            return

        target_session, path, projection_source = await self._resolve_target_session(
            user_id,
            session_record,
        )
        if target_session is None or target_session.id == session_record.id:
            return

        entry_id = self.entry_id(session_record.id, event.reply_id)
        if isinstance(
            event,
            (RequireUserConfirmEvent, RequireExternalExecutionEvent),
        ):
            event_payload = event.model_dump(mode="json")
            previous = await self._find_entry(
                projection,
                target_session.id,
                session_record.id,
                event.reply_id,
            )
            merged_tool_calls = self._merge_tool_calls(
                previous,
                event_payload,
            )
            if merged_tool_calls is not None:
                event_payload = {
                    **event_payload,
                    "tool_calls": merged_tool_calls,
                }
            payload = {
                "target_session_id": target_session.id,
                "leaf_session_id": session_record.id,
                "leaf_agent_id": agent_record.id,
                "leaf_agent_name": agent_record.data.name,
                "reply_id": event.reply_id,
                "event_type": (
                    "require_user_confirm"
                    if isinstance(event, RequireUserConfirmEvent)
                    else "require_external_execution"
                ),
                "event": event_payload,
                "created_at": datetime.now().isoformat(),
                "projection_path_session_ids": path,
                "projection_source": projection_source,
                "target_session_source": target_session.source.value,
            }
            await projection.upsert(
                target_session.id,
                self.KIND,
                entry_id,
                payload,
            )
            await projection.publish(
                target_session.id,
                self.EVT_REQUIRE,
                payload,
            )
            if (
                target_session.source == SessionSource.CHANNEL
                and target_session.source_channel_id
                and target_session.source_chat_id
                and self._channel_clients is not None
            ):
                await self._channel_clients.deliver_projected_hitl(
                    channel_id=target_session.source_channel_id,
                    chat_id=target_session.source_chat_id,
                    payload=payload,
                )
            return

        await projection.delete(target_session.id, self.KIND, entry_id)
        await projection.publish(
            target_session.id,
            self.EVT_RESULT,
            {
                "target_session_id": target_session.id,
                "leaf_session_id": session_record.id,
                "reply_id": event.reply_id,
            },
        )

    async def _resolve_target_session(
        self,
        user_id: str,
        session_record: "SessionRecord",
    ) -> tuple["SessionRecord | None", list[str], str]:
        """Resolve the top-most session that should host this HITL."""
        current = session_record
        visited: set[str] = set()
        path = [session_record.id]
        seen_sources: set[str] = set()

        while current is not None and current.id not in visited:
            visited.add(current.id)
            next_session = await self._resolve_next_session(
                user_id,
                current,
                seen_sources,
            )
            if next_session is None:
                return current, path, self._classify_projection_source(seen_sources)
            path.append(next_session.id)
            current = next_session

        if current is not None:
            logger.warning(
                "RecursiveHitlProjector detected session loop at %s",
                current.id,
            )
        return session_record, [session_record.id], "subagent"

    async def _resolve_next_session(
        self,
        user_id: str,
        session_record: "SessionRecord",
        seen_sources: set[str],
    ) -> "SessionRecord | None":
        """Return the next parent/source session in the projection chain."""
        if session_record.parent_session_id:
            seen_sources.add("subagent")
            parent = await self._storage.get_session_meta(
                user_id,
                session_record.parent_session_id,
            )
            return parent

        if (
            session_record.source == SessionSource.SCHEDULE
            and session_record.source_schedule_id is not None
        ):
            schedule = await self._storage.get_schedule(
                user_id,
                session_record.source_schedule_id,
            )
            next_session = await self._resolve_schedule_source_session(
                user_id,
                schedule,
            )
            if next_session is not None:
                seen_sources.add("schedule")
            return next_session

        return None

    async def _resolve_schedule_source_session(
        self,
        user_id: str,
        schedule: "ScheduleRecord | None",
    ) -> "SessionRecord | None":
        """Resolve the originating session referenced by one schedule."""
        if schedule is None or not schedule.data.source_session_id:
            return None
        return await self._storage.get_session_meta(
            user_id,
            schedule.data.source_session_id,
        )

    @staticmethod
    def _classify_projection_source(seen_sources: set[str]) -> str:
        """Summarize the path type for UI/debugging."""
        if seen_sources == {"subagent"}:
            return "subagent"
        if seen_sources == {"schedule"}:
            return "schedule"
        if seen_sources:
            return "mixed"
        return "subagent"

    @classmethod
    async def resolve(
        cls,
        projection: "SessionProjection",
        target_session_id: str,
        reply_id: str,
    ) -> dict | None:
        """Resolve the leaf request mirrored onto one top session."""
        for entry in await projection.list(target_session_id, cls.KIND):
            if entry.get("reply_id") == reply_id:
                return entry
        return None

    @classmethod
    async def _find_entry(
        cls,
        projection: "SessionProjection",
        target_session_id: str,
        leaf_session_id: str,
        reply_id: str,
    ) -> dict | None:
        """Return the stored entry for one leaf reply, if any."""
        for entry in await projection.list(target_session_id, cls.KIND):
            if (
                entry.get("leaf_session_id") == leaf_session_id
                and entry.get("reply_id") == reply_id
            ):
                return entry
        return None

    @staticmethod
    def _merge_tool_calls(
        previous: dict | None,
        incoming: dict,
    ) -> list[dict] | None:
        """Keep still-pending tool calls when a new require overwrites one."""
        incoming_calls = incoming.get("tool_calls") or []
        previous_calls = (
            ((previous or {}).get("event") or {}).get("tool_calls") or []
        )
        if not previous_calls:
            return None
        merged: dict[str, dict] = {
            tool_call["id"]: tool_call
            for tool_call in previous_calls
            if tool_call.get("id")
        }
        for tool_call in incoming_calls:
            if tool_call.get("id"):
                merged[tool_call["id"]] = tool_call
        return list(merged.values())

    @classmethod
    async def forget_answered(
        cls,
        projection: "SessionProjection",
        entry: dict,
        answered_tool_call_ids: set[str],
    ) -> None:
        """Drop answered tool calls from one projected entry.

        Mirrors the WebUI's optimistic removal so a page refresh no
        longer replays a confirmation the user already answered. The
        entry is deleted once no tool call is left waiting.
        """
        if not answered_tool_call_ids:
            return
        target_session_id = entry["target_session_id"]
        entry_id = cls.entry_id(
            entry["leaf_session_id"],
            entry["reply_id"],
        )
        event = entry.get("event") or {}
        remaining = [
            tool_call
            for tool_call in event.get("tool_calls") or []
            if tool_call.get("id") not in answered_tool_call_ids
        ]
        if remaining:
            updated = {**entry, "event": {**event, "tool_calls": remaining}}
            await projection.upsert(
                target_session_id,
                cls.KIND,
                entry_id,
                updated,
            )
            await projection.publish(
                target_session_id,
                cls.EVT_REQUIRE,
                updated,
            )
            return
        await projection.delete(target_session_id, cls.KIND, entry_id)
        await projection.publish(
            target_session_id,
            cls.EVT_RESULT,
            {
                "target_session_id": target_session_id,
                "leaf_session_id": entry["leaf_session_id"],
                "reply_id": entry["reply_id"],
            },
        )

    @classmethod
    async def purge_target(
        cls,
        projection: "SessionProjection",
        target_session_id: str,
    ) -> None:
        """Drop every projected HITL entry hosted on one target session."""
        await projection.purge(target_session_id, cls.KIND)

    @classmethod
    async def drop_leaf(
        cls,
        projection: "SessionProjection",
        target_session_id: str,
        leaf_session_id: str,
    ) -> None:
        """Drop every projected entry that originated from one leaf session."""
        for entry in await projection.list(target_session_id, cls.KIND):
            if entry.get("leaf_session_id") != leaf_session_id:
                continue
            await projection.delete(
                target_session_id,
                cls.KIND,
                cls.entry_id(leaf_session_id, entry["reply_id"]),
            )
