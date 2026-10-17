# -*- coding: utf-8 -*-
"""User-scoped knowledge-base service backed by the local KB HTTP API."""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import HTTPException, status


@dataclass(slots=True)
class KnowledgeBaseRef:
    """One ensured knowledge-base reference for a user."""

    id: int
    code: str
    name: str
    mode: str
    is_active: bool


@dataclass(slots=True)
class KnowledgeBaseFileResolution:
    """One resolved file record in the current user's knowledge base."""

    file_id: int
    file_path: str
    kb_id: int
    kb_code: str
    chunk_count: int | None = None


@dataclass(slots=True)
class KnowledgeBaseChunk:
    """One normalized text chunk returned by the KB API."""

    file_id: int
    file_path: str
    kb_id: int
    kb_code: str
    chunk_index: int
    content: str


class KnowledgeBaseService:
    """Wrap the local knowledge-base service with user-scoped helpers."""

    _INDEX_WAIT_POLL_INTERVAL_SECONDS = 1.0
    _INDEX_WAIT_TIMEOUT_SECONDS = 300.0

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float = 30.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            trust_env=False,
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP client if this service owns it."""
        if self._owns_client:
            await self._http.aclose()

    @staticmethod
    def _kb_code_for_user(user_id: str) -> str:
        """Build a stable user-scoped KB code within API constraints."""
        digest = hashlib.sha1(user_id.encode("utf-8")).hexdigest()[:16]
        return f"as_{digest}"

    @staticmethod
    def _kb_name_for_user(user_id: str) -> str:
        """Build a readable KB name for the user."""
        return f"agentscope_{user_id}"[:255]

    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        """Extract a readable error message from one HTTP response."""
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            detail = payload.get("detail")
            if isinstance(detail, str) and detail.strip():
                return detail.strip()
        text = response.text.strip()
        if text:
            return text
        return f"Knowledge-base API request failed with status {response.status_code}."

    @classmethod
    def _raise_api_error(cls, response: httpx.Response) -> None:
        """Raise an HTTPException that preserves useful upstream semantics."""
        status_code = response.status_code
        if status.HTTP_400_BAD_REQUEST <= status_code < status.HTTP_500_INTERNAL_SERVER_ERROR:
            raise HTTPException(
                status_code=status_code,
                detail=cls._error_detail(response),
            )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=cls._error_detail(response),
        )

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        expected_statuses: tuple[int, ...] = (200,),
        **kwargs: Any,
    ) -> Any:
        """Send one JSON request and raise a FastAPI-style error on failure."""
        response = await self._http.request(method, path, **kwargs)
        if response.status_code not in expected_statuses:
            self._raise_api_error(response)
        if response.status_code == status.HTTP_204_NO_CONTENT:
            return None
        return response.json()

    async def _get_knowledge_base_by_code(
        self,
        kb_code: str,
    ) -> KnowledgeBaseRef | None:
        """Return one KB by code or ``None`` when it does not exist."""
        response = await self._http.get(f"/api/v1/knowledge-bases/by-code/{kb_code}")
        if response.status_code == status.HTTP_404_NOT_FOUND:
            return None
        if response.status_code != status.HTTP_200_OK:
            self._raise_api_error(response)
        return self._to_kb_ref(response.json())

    @staticmethod
    def _to_kb_ref(payload: dict[str, Any]) -> KnowledgeBaseRef:
        """Convert one API payload into a KB reference."""
        return KnowledgeBaseRef(
            id=int(payload["id"]),
            code=str(payload["code"]),
            name=str(payload["name"]),
            mode=str(payload["mode"]),
            is_active=bool(payload["is_active"]),
        )

    async def ensure_user_knowledge_base(self, user_id: str) -> KnowledgeBaseRef:
        """Return a ready-to-use manual KB for the current user."""
        kb_code = self._kb_code_for_user(user_id)
        kb = await self._get_knowledge_base_by_code(kb_code)
        kb_name = self._kb_name_for_user(user_id)
        if kb is None:
            payload = await self._request_json(
                "POST",
                "/api/v1/knowledge-bases",
                expected_statuses=(status.HTTP_201_CREATED,),
                json={
                    "name": kb_name,
                    "code": kb_code,
                    "description": f"Managed knowledge base for user {user_id}",
                    "mode": "manual",
                    "is_active": True,
                },
            )
            return self._to_kb_ref(payload)

        if kb.mode == "manual" and kb.is_active:
            return kb

        payload = await self._request_json(
            "PUT",
            f"/api/v1/knowledge-bases/{kb.id}",
            json={
                "name": kb_name,
                "mode": "manual",
                "is_active": True,
            },
        )
        return self._to_kb_ref(payload)

    async def _resolve_file_by_path(
        self,
        *,
        kb_code: str,
        file_path: str,
    ) -> KnowledgeBaseFileResolution | None:
        """Resolve one file path inside the current KB, if metadata exists."""
        response = await self._http.get(
            "/api/v1/files/by-path",
            params={
                "file_path": file_path,
                "kb_code": kb_code,
            },
        )
        if response.status_code == status.HTTP_404_NOT_FOUND:
            return None
        if response.status_code != status.HTTP_200_OK:
            self._raise_api_error(response)

        payload = response.json()
        if not payload:
            return None

        item = payload[0]
        file_id = item.get("id")
        if not item.get("metadata_exists") or file_id is None:
            return None

        return KnowledgeBaseFileResolution(
            file_id=int(file_id),
            file_path=str(item["file_path"]),
            kb_id=int(item["kb_id"]),
            kb_code=str(item["kb_code"]),
            chunk_count=(
                int(item["chunk_count"])
                if item.get("chunk_count") is not None
                else None
            ),
        )

    async def _get_file_metadata(self, file_id: int) -> dict[str, Any]:
        """Load one file metadata record from the KB API."""
        response = await self._http.get(f"/api/v1/files/{file_id}")
        if response.status_code == status.HTTP_404_NOT_FOUND:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"File {file_id} was not found in the knowledge base.",
            )
        if response.status_code != status.HTTP_200_OK:
            self._raise_api_error(response)
        return response.json()

    @staticmethod
    def _build_wait_summary(metadata: dict[str, Any]) -> dict[str, Any]:
        """Extract only the file-index fields that matter to the model."""
        payload: dict[str, Any] = {
            "status": str(metadata["status"]),
            "extract_status": str(metadata["extract_status"]),
            "index_status": str(metadata["index_status"]),
        }
        if metadata.get("chunk_count") is not None:
            payload["total_chunks"] = int(metadata["chunk_count"])
        if metadata.get("last_error"):
            payload["detail"] = str(metadata["last_error"])
        return payload

    @classmethod
    def _is_index_terminal(cls, metadata: dict[str, Any]) -> bool:
        """Return whether the file indexing lifecycle has reached a terminal state."""
        return str(metadata["index_status"]) in {"success", "failed", "disabled"}

    async def _wait_for_file_index(
        self,
        *,
        file_id: int,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Wait until one file reaches an index terminal state."""
        deadline = time.monotonic() + (
            timeout_seconds
            if timeout_seconds is not None
            else self._INDEX_WAIT_TIMEOUT_SECONDS
        )
        latest = await self._get_file_metadata(file_id)
        while not self._is_index_terminal(latest):
            if time.monotonic() >= deadline:
                raise HTTPException(
                    status_code=status.HTTP_408_REQUEST_TIMEOUT,
                    detail=(
                        f"Timed out while waiting for file {file_id} to finish indexing."
                    ),
                )
            await asyncio.sleep(self._INDEX_WAIT_POLL_INTERVAL_SECONDS)
            latest = await self._get_file_metadata(file_id)
        return latest

    async def _require_user_file_metadata(
        self,
        *,
        user_id: str,
        file_id: int,
    ) -> tuple[KnowledgeBaseRef, dict[str, Any]]:
        """Validate that the file belongs to the current user's KB."""
        kb = await self.ensure_user_knowledge_base(user_id)
        metadata = await self._get_file_metadata(file_id)
        if int(metadata["knowledge_base_id"]) != kb.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"File {file_id} does not belong to the current user's "
                    "knowledge base."
                ),
            )
        return kb, metadata

    @staticmethod
    def _compact_result_item(
        *,
        file_path: str,
        status_value: str,
        file_id: int | None = None,
        detail: str | None = None,
    ) -> dict[str, Any]:
        """Build a compact file-operation result item."""
        payload: dict[str, Any] = {
            "file_path": file_path,
            "status": status_value,
        }
        if file_id is not None:
            payload["file_id"] = file_id
        if detail:
            payload["detail"] = detail
        return payload

    async def add_files(
        self,
        user_id: str,
        file_paths: list[str],
        wait_for_index: bool = False,
    ) -> dict[str, Any]:
        """Add one or more files into the current user's KB."""
        kb = await self.ensure_user_knowledge_base(user_id)
        payload = await self._request_json(
            "POST",
            f"/api/v1/knowledge-bases/{kb.id}/manual-files:batch-create",
            expected_statuses=(status.HTTP_201_CREATED,),
            json={"file_paths": file_paths},
        )
        items = [
            self._compact_result_item(
                file_path=str(item["file_path"]),
                status_value=str(item["status"]),
                file_id=(
                    int(item["file_id"])
                    if item.get("file_id") is not None
                    else None
                ),
                detail=(
                    str(item["detail"])
                    if item.get("detail")
                    else None
                ),
            )
            for item in payload.get("items", [])
        ]
        if wait_for_index:
            for item in items:
                if item["status"] != "created" or "file_id" not in item:
                    continue
                metadata = await self._wait_for_file_index(file_id=int(item["file_id"]))
                item["waited"] = True
                item["indexing"] = self._build_wait_summary(metadata)
        return {
            "kb_code": kb.code,
            "wait_for_index": wait_for_index,
            "created_count": int(payload.get("created_count", 0)),
            "skipped_count": int(payload.get("skipped_count", 0)),
            "failed_count": int(payload.get("failed_count", 0)),
            "items": items,
        }

    async def delete_files(
        self,
        user_id: str,
        file_paths: list[str],
    ) -> dict[str, Any]:
        """Delete one or more file metadata records from the user's KB."""
        kb = await self.ensure_user_knowledge_base(user_id)
        items: list[dict[str, Any]] = []
        deleted_count = 0
        not_found_count = 0

        for file_path in file_paths:
            resolution = await self._resolve_file_by_path(
                kb_code=kb.code,
                file_path=file_path,
            )
            if resolution is None:
                not_found_count += 1
                items.append(
                    self._compact_result_item(
                        file_path=file_path,
                        status_value="not_found",
                        detail=(
                            "The file is not indexed in the current user's "
                            "knowledge base."
                        ),
                    ),
                )
                continue

            await self._request_json(
                "DELETE",
                f"/api/v1/files/{resolution.file_id}",
                expected_statuses=(status.HTTP_204_NO_CONTENT,),
            )
            deleted_count += 1
            items.append(
                self._compact_result_item(
                    file_path=resolution.file_path,
                    status_value="deleted",
                    file_id=resolution.file_id,
                ),
            )

        return {
            "kb_code": kb.code,
            "deleted_count": deleted_count,
            "not_found_count": not_found_count,
            "items": items,
        }

    async def refresh_files(
        self,
        user_id: str,
        file_paths: list[str],
        wait_for_index: bool = False,
    ) -> dict[str, Any]:
        """Immediately rebuild one or more files in the user's KB."""
        kb = await self.ensure_user_knowledge_base(user_id)
        items: list[dict[str, Any]] = []
        queued_count = 0
        not_found_count = 0

        for file_path in file_paths:
            resolution = await self._resolve_file_by_path(
                kb_code=kb.code,
                file_path=file_path,
            )
            if resolution is None:
                not_found_count += 1
                items.append(
                    self._compact_result_item(
                        file_path=file_path,
                        status_value="not_found",
                        detail=(
                            "The file is not indexed in the current user's "
                            "knowledge base."
                        ),
                    ),
                )
                continue

            await self._request_json(
                "POST",
                f"/api/v1/files/{resolution.file_id}/rebuild",
            )
            queued_count += 1
            items.append(
                self._compact_result_item(
                    file_path=resolution.file_path,
                    status_value="queued",
                    file_id=resolution.file_id,
                ),
            )

        if wait_for_index:
            for item in items:
                if item["status"] != "queued" or "file_id" not in item:
                    continue
                metadata = await self._wait_for_file_index(file_id=int(item["file_id"]))
                item["waited"] = True
                item["indexing"] = self._build_wait_summary(metadata)

        return {
            "kb_code": kb.code,
            "wait_for_index": wait_for_index,
            "queued_count": queued_count,
            "not_found_count": not_found_count,
            "items": items,
        }

    async def search(
        self,
        user_id: str,
        query: str,
        top_k: int = 10,
        directory_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        """Search the current user's KB with fixed hybrid-retrieval settings."""
        kb = await self.ensure_user_knowledge_base(user_id)
        payload = await self._request_json(
            "POST",
            "/api/v1/search",
            json={
                "query": query,
                "mode": "hybrid",
                "top_k": top_k,
                "knowledge_base_codes": [kb.code],
                "directory_paths": directory_paths,
                "min_score": None,
                "include_highlighted": False,
            },
        )
        return {
            "kb_code": kb.code,
            "mode": str(payload["mode"]),
            "directory_paths": directory_paths,
            "hits": [
                {
                    "score": float(item["score"]),
                    "file_id": int(item["file_id"]),
                    "file_path": str(item["file_path"]),
                    "total_chunks": (
                        int(item["chunk_count"])
                        if item.get("chunk_count") is not None
                        else None
                    ),
                    "chunk_index": int(item["chunk_index"]),
                    "content": str(item["content"]),
                }
                for item in payload.get("hits", [])
            ],
        }

    async def _get_chunk(
        self,
        *,
        file_id: int,
        chunk_index: int,
    ) -> KnowledgeBaseChunk | None:
        """Load one chunk or return ``None`` when it does not exist."""
        response = await self._http.get(f"/api/v1/chunks/{file_id}/{chunk_index}")
        if response.status_code == status.HTTP_404_NOT_FOUND:
            return None
        if response.status_code != status.HTTP_200_OK:
            self._raise_api_error(response)
        payload = response.json()
        return KnowledgeBaseChunk(
            file_id=int(payload["file_id"]),
            file_path=str(payload["file_path"]),
            kb_id=int(payload["kb_id"]),
            kb_code=str(payload["kb_code"]),
            chunk_index=int(payload["chunk_index"]),
            content=str(payload["content"]),
        )

    async def get_file_chunks(
        self,
        user_id: str,
        file_id: int,
        anchor_chunk_index: int,
        before: int = 1,
        after: int = 1,
    ) -> dict[str, Any]:
        """Return one chunk plus optional neighboring chunks for context."""
        _kb, metadata = await self._require_user_file_metadata(
            user_id=user_id,
            file_id=file_id,
        )
        chunk_count = metadata.get("chunk_count")
        if chunk_count is None or int(chunk_count) <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The file does not have indexed chunks yet.",
            )

        total_chunks = int(chunk_count)
        if anchor_chunk_index < 0 or anchor_chunk_index >= total_chunks:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Chunk index {anchor_chunk_index} is out of range for "
                    f"file {file_id}."
                ),
            )

        start = max(0, anchor_chunk_index - before)
        end = min(total_chunks - 1, anchor_chunk_index + after)
        chunks: list[dict[str, Any]] = []
        for chunk_index in range(start, end + 1):
            chunk = await self._get_chunk(
                file_id=file_id,
                chunk_index=chunk_index,
            )
            if chunk is None:
                continue
            chunks.append(
                {
                    "chunk_index": chunk.chunk_index,
                    "content": chunk.content,
                },
            )

        if not chunks:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No chunk content was found for file {file_id}.",
            )

        return {
            "file_id": file_id,
            "file_path": str(metadata["file_path"]),
            "anchor_chunk_index": anchor_chunk_index,
            "returned_range": {
                "start": start,
                "end": end,
                "total_chunks": total_chunks,
            },
            "chunks": chunks,
        }
