# -*- coding: utf-8 -*-
"""Builtin tool for sending messages to another managed session."""
from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any
from xml.sax.saxutils import quoteattr

from pydantic import Field

from .._bus_ops import deliver_to_inbox
from ..storage import SessionRecord, SessionSource
from ...message import (
    DataBlock,
    HintBlock,
    TextBlock,
    ToolResultState,
    URLSource,
)
from ...tool import ParamsBase
from ._session_tool_base import _SessionToolBase


class _SendSessionHintParams(ParamsBase):
    """Parameters for :class:`SendSessionHint`."""

    content: str = Field(
        description="Message text to send to the target session.",
    )
    target_session_id: str = Field(
        description=(
            "Target session id that should receive this message and any "
            "attached files."
        ),
    )
    attachment_paths: list[str] = Field(
        default_factory=list,
        description=(
            "Workspace file paths to include as attachments. Each path must "
            "point to a file inside the current workspace."
        ),
    )


class SendSessionHint(_SessionToolBase):
    """Send a message, optionally with file attachments, to a session."""

    name = "SendSessionHint"
    description = (
        "Send a message to another managed session. You must provide the "
        "target session id. Optionally attach files by passing workspace "
        "file paths."
    )
    input_schema: dict[str, Any] = _SendSessionHintParams.model_json_schema()
    is_read_only = False

    def _build_message_tag(
        self,
        session_record: SessionRecord,
    ) -> str:
        """Return the opening XML tag for one session message."""
        attrs: dict[str, str] = {
            "from_session_id": self._session_id,
            "from_session_source": session_record.source.value,
        }
        if (
            session_record.source == SessionSource.SCHEDULE
            and session_record.source_schedule_id
        ):
            attrs["schedule_id"] = session_record.source_schedule_id

        attr_text = " ".join(
            f"{key}={quoteattr(value)}"
            for key, value in attrs.items()
        )
        return f"<session-hint {attr_text}>"

    async def _build_attachment_block(
        self,
        path: str,
    ) -> tuple[DataBlock, str]:
        absolute_path, temp_path = await self._copy_workspace_file_to_temp_file(
            path,
        )
        media_type, _encoding = mimetypes.guess_type(absolute_path)
        return (
            DataBlock(
                source=URLSource(
                    url=Path(temp_path).as_uri(),
                    media_type=media_type or "application/octet-stream",
                ),
                name=Path(absolute_path).name,
            ),
            temp_path,
        )

    async def call(
        self,
        content: str,
        target_session_id: str,
        attachment_paths: list[str] | None = None,
    ):
        attachment_paths = attachment_paths or []
        created_temp_paths: list[str] = []

        current_session = await self._storage.get_session_meta(
            self._user_id,
            self._session_id,
        )
        if current_session is None:
            return self._result(
                {
                    "error": (
                        f"Current session '{self._session_id}' was not found."
                    ),
                    "target_session_id": target_session_id,
                },
                state=ToolResultState.ERROR,
            )

        target_session = await self._storage.get_session_meta(
            self._user_id,
            target_session_id,
        )
        if target_session is None:
            return self._result(
                {
                    "error": f"Target session '{target_session_id}' was not found.",
                    "target_session_id": target_session_id,
                },
                state=ToolResultState.ERROR,
            )

        try:
            attachment_blocks: list[DataBlock] = []
            for path in attachment_paths:
                block, temp_path = await self._build_attachment_block(path)
                attachment_blocks.append(block)
                created_temp_paths.append(temp_path)

            opening = self._build_message_tag(current_session)
            if attachment_blocks:
                hint_content: str | list[TextBlock | DataBlock] = []
                if content:
                    hint_content.append(
                        TextBlock(text=f"{opening}\n{content}"),
                    )
                else:
                    hint_content.append(TextBlock(text=opening))
                hint_content.extend(attachment_blocks)
                hint_content.append(TextBlock(text="\n</session-hint>"))
            else:
                hint_content = (
                    f"{opening}\n"
                    f"{content}\n"
                    f"</session-hint>"
                )

            hint = HintBlock(
                hint=hint_content,
                source=json.dumps(
                    {
                        "label": "session",
                        "sublabel": "message",
                        "from_session_id": self._session_id,
                        "from_session_source": current_session.source.value,
                        "schedule_id": current_session.source_schedule_id,
                        "state": "delivered",
                    },
                    ensure_ascii=False,
                ),
            )
            await deliver_to_inbox(
                self._message_bus,
                user_id=self._user_id,
                session_id=target_session.id,
                agent_id=target_session.agent_id,
                payload=hint.model_dump(mode="json"),
            )
        except Exception as exc:  # noqa: BLE001
            for temp_path in created_temp_paths:
                try:
                    Path(temp_path).unlink()
                except OSError:
                    pass
            return self._result(
                {
                    "error": str(exc),
                    "target_session_id": target_session_id,
                    "attachment_paths": attachment_paths,
                },
                state=ToolResultState.ERROR,
            )

        return self._result(
            {
                "status": "delivered",
                "target_session_id": target_session_id,
                "attachment_count": len(attachment_paths),
            },
        )
