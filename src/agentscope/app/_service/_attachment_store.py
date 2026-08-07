# -*- coding: utf-8 -*-
"""Workspace-backed attachment storage for chat messages."""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import re
from copy import deepcopy
from pathlib import Path, PurePath
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlparse, urlunparse

from fastapi import Request

from ...formatter import FormatterBase
from ...message import (
    Base64Source,
    DataBlock,
    HintBlock,
    Msg,
    TextBlock,
    ToolResultBlock,
    URLSource,
)
from ...workspace import WorkspaceBase


class AttachmentStore:
    """Persist chat attachments inside the current workspace."""

    _SCHEME = "attachment"
    _ATTACHMENT_ROOT = "attachments"
    _DEFAULT_FILE_STEM = "attachment"
    _INVALID_FILENAME_PATTERN = re.compile(r"[\x00-\x1f\x7f]+")

    def _build_attachment_uri(
        self,
        *,
        session_id: str,
        message_id: str,
        attachment_name: str,
    ) -> str:
        encoded_name = quote(attachment_name, safe="")
        return f"{self._SCHEME}://{session_id}/{message_id}/{encoded_name}"

    def _parse_attachment_uri(
        self,
        url: str,
    ) -> tuple[str, str, str] | None:
        parsed = urlparse(url)
        if parsed.scheme != self._SCHEME:
            return None
        path_parts = [part for part in parsed.path.split("/") if part]
        if not parsed.netloc or len(path_parts) != 2:
            return None
        return parsed.netloc, path_parts[0], unquote(path_parts[1])

    def _build_public_download_path(
        self,
        *,
        session_id: str,
        message_id: str,
        attachment_name: str,
        user_id: str | None = None,
    ) -> str:
        encoded_name = quote(attachment_name, safe="")
        path = f"/attachments/{session_id}/{message_id}/{encoded_name}"
        if not user_id:
            return path
        return f"{path}?{urlencode({'user_id': user_id})}"

    @staticmethod
    def _append_user_id_query(url: str, user_id: str | None) -> str:
        if not user_id:
            return url
        parsed = urlparse(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["user_id"] = user_id
        return urlunparse(
            parsed._replace(query=urlencode(query)),
        )

    @staticmethod
    def guess_media_type(
        filename: str,
        fallback: str = "application/octet-stream",
    ) -> str:
        """Guess content type from filename."""
        guessed, _ = mimetypes.guess_type(filename)
        return guessed or fallback

    @staticmethod
    def _session_root(
        workspace: WorkspaceBase,
    ) -> str:
        return workspace.get_backend().join_path(
            workspace.workdir,
            "sessions",
        )

    def _message_attachment_root(
        self,
        workspace: WorkspaceBase,
        *,
        session_id: str,
        message_id: str,
    ) -> str:
        backend = workspace.get_backend()
        return backend.join_path(
            self._session_root(workspace),
            session_id,
            self._ATTACHMENT_ROOT,
            message_id,
        )

    def _attachment_path(
        self,
        workspace: WorkspaceBase,
        *,
        session_id: str,
        message_id: str,
        attachment_name: str,
    ) -> str:
        backend = workspace.get_backend()
        return backend.join_path(
            self._message_attachment_root(
                workspace,
                session_id=session_id,
                message_id=message_id,
            ),
            attachment_name,
        )

    @staticmethod
    def _decode_base64_data(data: str) -> bytes:
        return base64.b64decode(data)

    @staticmethod
    def _extract_local_file_path(url: str) -> str | None:
        parsed = urlparse(url)
        if parsed.scheme != "file":
            return None
        path = unquote(parsed.path or "")
        if parsed.netloc and parsed.netloc != "localhost":
            path = f"//{parsed.netloc}{path}"
        return path or None

    @staticmethod
    def _file_uri(path: str) -> str:
        return Path(path).as_uri()

    def _sanitize_filename(
        self,
        name: str | None,
        *,
        media_type: str,
    ) -> str:
        raw_name = (name or "").replace("\\", "/").split("/")[-1]
        raw_name = self._INVALID_FILENAME_PATTERN.sub("_", raw_name).strip()
        raw_name = raw_name.strip(". ")

        if raw_name in {"", ".", ".."}:
            extension = mimetypes.guess_extension(media_type or "") or ".bin"
            return f"{self._DEFAULT_FILE_STEM}{extension}"

        stem = PurePath(raw_name).stem.strip() or self._DEFAULT_FILE_STEM
        suffix = PurePath(raw_name).suffix
        if not suffix:
            suffix = mimetypes.guess_extension(media_type or "") or ".bin"
        return f"{stem}{suffix}"

    async def _load_local_file_bytes(
        self,
        path: str,
    ) -> bytes:
        return await asyncio.to_thread(Path(path).read_bytes)

    async def _select_stored_name(
        self,
        workspace: WorkspaceBase,
        *,
        session_id: str,
        message_id: str,
        preferred_name: str,
        payload: bytes,
    ) -> str:
        backend = workspace.get_backend()
        stem = PurePath(preferred_name).stem or self._DEFAULT_FILE_STEM
        suffix = PurePath(preferred_name).suffix

        candidate = preferred_name
        index = 1
        while True:
            candidate_path = self._attachment_path(
                workspace,
                session_id=session_id,
                message_id=message_id,
                attachment_name=candidate,
            )
            if not await backend.file_exists(candidate_path):
                return candidate
            existing = await backend.read_file(candidate_path)
            if existing == payload:
                return candidate
            index += 1
            candidate = f"{stem}-{index}{suffix}"

    async def _persist_data_block(
        self,
        block: DataBlock,
        *,
        workspace: WorkspaceBase,
        session_id: str,
        message_id: str,
    ) -> DataBlock:
        source = block.source
        local_path: str | None = None
        if isinstance(source, URLSource):
            parsed_ref = self._parse_attachment_uri(str(source.url))
            if parsed_ref is not None:
                return block
            local_path = self._extract_local_file_path(str(source.url))
            if local_path is None:
                return block
            payload = await self._load_local_file_bytes(local_path)
        else:
            payload = self._decode_base64_data(source.data)

        preferred_name = self._sanitize_filename(
            block.name or (Path(local_path).name if local_path else None),
            media_type=block.source.media_type,
        )
        stored_name = await self._select_stored_name(
            workspace,
            session_id=session_id,
            message_id=message_id,
            preferred_name=preferred_name,
            payload=payload,
        )
        backend = workspace.get_backend()
        await backend.ensure_dir(
            self._message_attachment_root(
                workspace,
                session_id=session_id,
                message_id=message_id,
            ),
        )
        await backend.write_file(
            self._attachment_path(
                workspace,
                session_id=session_id,
                message_id=message_id,
                attachment_name=stored_name,
            ),
            payload,
        )
        return block.model_copy(
            update={
                "source": URLSource(
                    url=self._build_attachment_uri(
                        session_id=session_id,
                        message_id=message_id,
                        attachment_name=stored_name,
                    ),
                    media_type=block.source.media_type,
                ),
            },
            deep=True,
        )

    async def _transform_message_blocks(
        self,
        message: Msg,
        *,
        transform_data_block,
    ) -> Msg:
        content: list[Any] = []
        for block in message.content:
            if isinstance(block, DataBlock):
                content.append(
                    await transform_data_block(block, in_tool_result=False),
                )
                continue
            if isinstance(block, HintBlock) and isinstance(block.hint, list):
                hint_content: list[TextBlock | DataBlock] = []
                for hint_block in block.hint:
                    if isinstance(hint_block, DataBlock):
                        hint_content.append(
                            await transform_data_block(
                                hint_block,
                                in_tool_result=False,
                            ),
                        )
                    else:
                        hint_content.append(hint_block)
                content.append(
                    block.model_copy(update={"hint": hint_content}, deep=True),
                )
                continue
            if isinstance(block, ToolResultBlock) and isinstance(
                block.output,
                list,
            ):
                output: list[TextBlock | DataBlock] = []
                for output_block in block.output:
                    if isinstance(output_block, DataBlock):
                        output.append(
                            await transform_data_block(
                                output_block,
                                in_tool_result=True,
                            ),
                        )
                    else:
                        output.append(output_block)
                content.append(
                    block.model_copy(update={"output": output}, deep=True),
                )
                continue
            content.append(block)
        return message.model_copy(update={"content": content}, deep=True)

    async def persist_message_attachments(
        self,
        message: Msg,
        *,
        workspace: WorkspaceBase,
        session_id: str,
    ) -> Msg:
        """Persist message attachments to workspace and store references."""
        return await self._transform_message_blocks(
            message,
            transform_data_block=lambda block, **_: self._persist_data_block(
                block,
                workspace=workspace,
                session_id=session_id,
                message_id=message.id,
            ),
        )

    async def resolve_attachment_path(
        self,
        workspace: WorkspaceBase,
        *,
        session_id: str,
        message_id: str,
        attachment_name: str,
    ) -> str:
        """Return the absolute workspace path of an attachment."""
        return self._attachment_path(
            workspace,
            session_id=session_id,
            message_id=message_id,
            attachment_name=attachment_name,
        )

    async def read_attachment_bytes(
        self,
        workspace: WorkspaceBase,
        *,
        session_id: str,
        message_id: str,
        attachment_name: str,
    ) -> bytes:
        """Read attachment bytes from workspace."""
        return await workspace.get_backend().read_file(
            self._attachment_path(
                workspace,
                session_id=session_id,
                message_id=message_id,
                attachment_name=attachment_name,
            ),
        )

    async def _materialize_data_block_for_model(
        self,
        block: DataBlock,
        *,
        workspace: WorkspaceBase,
        formatter: FormatterBase | None,
        in_tool_result: bool,
    ) -> DataBlock:
        source = block.source
        if not isinstance(source, URLSource):
            return block
        parsed_ref = self._parse_attachment_uri(str(source.url))
        if parsed_ref is None:
            return block

        session_id, message_id, attachment_name = parsed_ref
        path = await self.resolve_attachment_path(
            workspace,
            session_id=session_id,
            message_id=message_id,
            attachment_name=attachment_name,
        )
        prefer_binary = False
        if formatter is not None:
            if in_tool_result:
                prefer_binary = (
                    formatter.supports_tool_result_media(
                        block.source.media_type,
                    )
                    or formatter.supports_input_media(
                        block.source.media_type,
                    )
                )
            else:
                prefer_binary = formatter.supports_input_media(
                    block.source.media_type,
                )
        if prefer_binary:
            payload = await self.read_attachment_bytes(
                workspace,
                session_id=session_id,
                message_id=message_id,
                attachment_name=attachment_name,
            )
            return block.model_copy(
                update={
                    "source": Base64Source(
                        data=base64.b64encode(payload).decode("ascii"),
                        media_type=block.source.media_type,
                    ),
                },
                deep=True,
            )
        return block.model_copy(
            update={
                "source": URLSource(
                    url=self._file_uri(path),
                    media_type=block.source.media_type,
                ),
            },
            deep=True,
        )

    async def materialize_messages_for_model(
        self,
        messages: list[Msg],
        *,
        workspace: WorkspaceBase,
        formatter: FormatterBase | None,
    ) -> list[Msg]:
        """Expand attachment references for one model call."""
        materialized: list[Msg] = []
        for message in messages:
            materialized.append(
                await self._transform_message_blocks(
                    deepcopy(message),
                    transform_data_block=lambda block, *, in_tool_result: self._materialize_data_block_for_model(
                        block,
                        workspace=workspace,
                        formatter=formatter,
                        in_tool_result=in_tool_result,
                    ),
                ),
            )
        return materialized

    def hydrate_message_for_public(
        self,
        message: Msg,
        *,
        request: Request | None = None,
        user_id: str | None = None,
    ) -> Msg:
        """Replace internal attachment references with public URLs."""
        resolved_user_id = user_id
        if resolved_user_id is None and request is not None:
            resolved_user_id = request.headers.get("x-user-id") or None

        def _hydrate_block(block: DataBlock) -> DataBlock:
            source = block.source
            if not isinstance(source, URLSource):
                return block
            parsed_ref = self._parse_attachment_uri(str(source.url))
            if parsed_ref is None:
                return block
            session_id, message_id, attachment_name = parsed_ref
            path = self._build_public_download_path(
                session_id=session_id,
                message_id=message_id,
                attachment_name=attachment_name,
                user_id=resolved_user_id,
            )
            if request is not None:
                public_url = self._append_user_id_query(
                    str(
                        request.url_for(
                        "download_attachment",
                        session_id=session_id,
                        message_id=message_id,
                        attachment_name=attachment_name,
                        ),
                    ),
                    resolved_user_id,
                )
            else:
                public_url = path
            return block.model_copy(
                update={
                    "name": attachment_name,
                    "source": URLSource(
                        url=public_url,
                        media_type=source.media_type,
                    ),
                },
                deep=True,
            )

        content: list[Any] = []
        for block in message.content:
            if isinstance(block, DataBlock):
                content.append(_hydrate_block(block))
                continue
            if isinstance(block, HintBlock) and isinstance(block.hint, list):
                hint = [
                    _hydrate_block(sub_block)
                    if isinstance(sub_block, DataBlock)
                    else sub_block
                    for sub_block in block.hint
                ]
                content.append(block.model_copy(update={"hint": hint}, deep=True))
                continue
            if isinstance(block, ToolResultBlock) and isinstance(
                block.output,
                list,
            ):
                output = [
                    _hydrate_block(sub_block)
                    if isinstance(sub_block, DataBlock)
                    else sub_block
                    for sub_block in block.output
                ]
                content.append(
                    block.model_copy(update={"output": output}, deep=True),
                )
                continue
            content.append(block)
        return message.model_copy(update={"content": content}, deep=True)

    async def prune_session_attachments(
        self,
        workspace: WorkspaceBase,
        *,
        session_id: str,
        kept_message_ids: set[str],
    ) -> None:
        """Delete attachment directories that no longer belong to messages."""
        backend = workspace.get_backend()
        attachments_root = backend.join_path(
            self._session_root(workspace),
            session_id,
            self._ATTACHMENT_ROOT,
        )
        if not await backend.is_dir(attachments_root):
            return
        for entry in await backend.list_dir(attachments_root):
            if entry in kept_message_ids:
                continue
            await backend.exec_shell(
                ["rm", "-rf", backend.join_path(attachments_root, entry)],
            )

    async def delete_session_attachments(
        self,
        workspace: WorkspaceBase,
        *,
        session_id: str,
    ) -> None:
        """Delete every attachment for a session."""
        backend = workspace.get_backend()
        attachments_root = backend.join_path(
            self._session_root(workspace),
            session_id,
            self._ATTACHMENT_ROOT,
        )
        if not await backend.is_dir(attachments_root):
            return
        await backend.exec_shell(["rm", "-rf", attachments_root])
