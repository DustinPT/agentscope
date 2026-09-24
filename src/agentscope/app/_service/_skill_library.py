# -*- coding: utf-8 -*-
"""User-scoped skill library backed by storage-managed Redis JSON records."""

from __future__ import annotations

import asyncio
import io
import mimetypes
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import frontmatter
from fastapi import HTTPException, UploadFile, status

from ..._utils._fs import _hash_directory
from ...embedding import EmbeddingModelBase
from ..storage import SkillLibraryFileRecord, SkillLibraryRecord, StorageBase
from ._agent_asset_store import AgentAssetStore
from ._skill_reranker import SkillRerankerBase


@dataclass(slots=True)
class SkillSearchHit:
    """One reranked skill search result."""

    record: SkillLibraryRecord
    score: float | None = None


class SkillLibraryService:
    """Manage uploaded skills while delegating persistence to storage."""

    def __init__(
        self,
        *,
        root_dir: str,
        storage: StorageBase,
        embedding_model: EmbeddingModelBase[Any],
        skill_reranker: SkillRerankerBase | None = None,
    ) -> None:
        self._root_dir = os.path.abspath(root_dir)
        self._storage = storage
        self._embedding_model = embedding_model
        self._skill_reranker = skill_reranker

    @property
    def root_dir(self) -> str:
        """Return the managed root directory for persisted skill files."""
        return self._root_dir

    def _user_root(self, user_id: str) -> str:
        return os.path.join(self._root_dir, user_id)

    def _skill_dir(self, user_id: str, skill_name: str) -> str:
        return os.path.join(self._user_root(user_id), skill_name)

    def _relative_dir(self, user_id: str, skill_name: str) -> str:
        return os.path.relpath(
            self._skill_dir(user_id, skill_name),
            self._root_dir,
        ).replace(os.sep, "/")

    async def _embed_text(self, text: str) -> list[float]:
        """Generate one embedding vector for storage-managed search."""
        response = await self._embedding_model([text])
        if not response.embeddings:
            raise RuntimeError("Embedding model returned no vectors.")
        return [float(value) for value in response.embeddings[0]]

    @staticmethod
    def _build_rerank_document(record: SkillLibraryRecord) -> str:
        """Build one lightweight skill document for reranking."""
        return (
            f"Skill name: {record.name}\n"
            f"Skill description: {record.description}"
        )

    def _search_candidate_limit(self, limit: int) -> int:
        """Expand recall when reranking is enabled."""
        if self._skill_reranker is None:
            return limit
        return min(max(limit * 5, 20), 50)

    @staticmethod
    def _read_skill_markdown(skill_dir: str) -> str:
        skill_md_path = os.path.join(skill_dir, "SKILL.md")
        with open(skill_md_path, "r", encoding="utf-8") as file_obj:
            return file_obj.read()

    @staticmethod
    def _list_files(skill_dir: str) -> list[SkillLibraryFileRecord]:
        manifest: list[SkillLibraryFileRecord] = []
        for root, _dirs, files in os.walk(skill_dir):
            files.sort()
            for file_name in files:
                abs_path = os.path.join(root, file_name)
                rel_path = os.path.relpath(abs_path, skill_dir).replace(os.sep, "/")
                mime_type, _ = mimetypes.guess_type(file_name)
                manifest.append(
                    SkillLibraryFileRecord(
                        path=rel_path,
                        size_bytes=os.path.getsize(abs_path),
                        mime_type=mime_type,
                    ),
                )
        manifest.sort(key=lambda item: item.path)
        return manifest

    @staticmethod
    def _validate_skill_dir_name(skill_dir: str, expected_name: str) -> None:
        dir_name = os.path.basename(os.path.abspath(skill_dir))
        if dir_name != expected_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Skill directory name must match the name declared in "
                    f"SKILL.md. Directory '{dir_name}' declares '{expected_name}'."
                ),
            )

    @staticmethod
    def _read_skill_frontmatter(markdown: str) -> tuple[str, str]:
        doc = frontmatter.loads(markdown)
        name = doc.get("name")
        description = doc.get("description")
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="SKILL.md must include a non-empty 'name'.",
            )
        if not isinstance(description, str):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="SKILL.md must include a 'description'.",
            )
        return name.strip(), description.strip()

    async def _stage_skill_zip(self, file: UploadFile) -> tuple[str, str, str, str]:
        """Validate and extract one uploaded skill ZIP."""
        if not file.filename or not file.filename.lower().endswith(".zip"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only ZIP skill packages are supported.",
            )

        payload = await file.read()
        if not payload:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded skill package is empty.",
            )

        def _stage() -> tuple[str, str, str, str]:
            staging_root = tempfile.mkdtemp(prefix="skill_library_")
            try:
                with zipfile.ZipFile(io.BytesIO(payload)) as zip_file:
                    AgentAssetStore._assert_safe_zip(zip_file.infolist())
                    zip_file.extractall(staging_root)
                skill_root = AgentAssetStore._find_skill_root(staging_root)
                markdown = self._read_skill_markdown(skill_root)
                name, description = self._read_skill_frontmatter(markdown)
                self._validate_skill_dir_name(skill_root, name)
                return skill_root, name, description, markdown
            except zipfile.BadZipFile as exc:
                shutil.rmtree(staging_root, ignore_errors=True)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid ZIP archive: {exc}",
                ) from exc
            except Exception:
                shutil.rmtree(staging_root, ignore_errors=True)
                raise

        return await asyncio.to_thread(_stage)

    async def get_skill(self, user_id: str, skill_name: str) -> SkillLibraryRecord:
        """Load one owned skill record or raise 404."""
        record = await self._storage.get_skill_by_name(user_id, skill_name)
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Skill '{skill_name}' not found.",
            )
        return record

    async def upload_skill(
        self,
        *,
        user_id: str,
        file: UploadFile,
    ) -> SkillLibraryRecord:
        """Upload one ZIP skill package into the managed user library."""
        skill_root, name, description, markdown = await self._stage_skill_zip(file)
        existing = await self._storage.get_skill_by_name(user_id, name)
        if existing is not None:
            shutil.rmtree(os.path.dirname(skill_root), ignore_errors=True)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Skill '{name}' already exists.",
            )

        target_dir = self._skill_dir(user_id, name)
        os.makedirs(self._user_root(user_id), exist_ok=True)
        if os.path.exists(target_dir):
            shutil.rmtree(os.path.dirname(skill_root), ignore_errors=True)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Skill '{name}' already exists.",
            )

        manifest = await asyncio.to_thread(self._list_files, skill_root)
        content_hash = await asyncio.to_thread(_hash_directory, skill_root)
        embedding = await self._embed_text(description)
        await asyncio.to_thread(shutil.move, skill_root, target_dir)
        await asyncio.to_thread(
            shutil.rmtree,
            os.path.dirname(skill_root),
            True,
        )
        record = SkillLibraryRecord(
            user_id=user_id,
            name=name,
            description=description,
            archive_name=file.filename or f"{name}.zip",
            dir=self._relative_dir(user_id, name),
            content_hash=content_hash,
            skill_markdown=markdown,
            file_manifest=manifest,
            embedding=embedding,
        )
        return await self._storage.upsert_skill(user_id, record)

    async def list_skills(
        self,
        *,
        user_id: str,
        keyword: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[SkillLibraryRecord], int]:
        """List user-owned skills via the storage list API."""
        return await self._storage.list_skills(
            user_id=user_id,
            keyword=keyword,
            limit=limit,
            offset=offset,
        )

    async def delete_skill(self, *, user_id: str, skill_name: str) -> None:
        """Delete one owned skill and its managed file directory."""
        record = await self.get_skill(user_id, skill_name)
        deleted = await self._storage.delete_skill(user_id, skill_name)
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Skill '{skill_name}' not found.",
            )
        skill_dir = self._skill_dir(user_id, record.name)
        await asyncio.to_thread(shutil.rmtree, skill_dir, True)
        user_root = self._user_root(user_id)
        if os.path.isdir(user_root) and not os.listdir(user_root):
            await asyncio.to_thread(shutil.rmtree, user_root, True)

    async def search_skills(
        self,
        *,
        user_id: str,
        query: str,
        limit: int = 5,
        min_score: float | None = None,
    ) -> list[SkillSearchHit]:
        """Return reranked top-N skills for tool consumption."""
        normalized_query = query.strip()
        if not normalized_query:
            return []

        candidates = await self._storage.search_skills(
            user_id=user_id,
            query=normalized_query,
            limit=self._search_candidate_limit(limit),
        )
        if not candidates:
            return []

        if self._skill_reranker is None:
            return [
                SkillSearchHit(record=record)
                for record in candidates[:limit]
            ]

        documents = [
            self._build_rerank_document(record)
            for record in candidates
        ]
        scores = await self._skill_reranker.rank(
            query=normalized_query,
            documents=documents,
        )
        hits = [
            SkillSearchHit(record=record, score=score)
            for record, score in zip(candidates, scores, strict=True)
        ]
        hits.sort(
            key=lambda item: item.score if item.score is not None else float("-inf"),
            reverse=True,
        )
        if min_score is not None:
            hits = [
                item
                for item in hits
                if item.score is not None and item.score > min_score
            ]
        return hits[:limit]

    def _resolve_relative_path(
        self,
        *,
        skill_dir: str,
        relative_path: str,
    ) -> str:
        cleaned = (relative_path or "").strip().replace("\\", "/")
        normalized = os.path.normpath(cleaned) if cleaned else "."
        if normalized in {".", ""}:
            return skill_dir
        absolute = os.path.abspath(os.path.join(skill_dir, normalized))
        boundary = os.path.abspath(skill_dir)
        if absolute != boundary and not absolute.startswith(boundary + os.sep):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Path must stay inside the skill package.",
            )
        return absolute

    async def list_skill_files(
        self,
        *,
        user_id: str,
        skill_name: str,
        relative_path: str = "",
    ) -> list[dict[str, Any]]:
        """List one directory inside the stored skill package."""
        record = await self.get_skill(user_id, skill_name)
        skill_dir = self._skill_dir(user_id, record.name)
        target_dir = self._resolve_relative_path(
            skill_dir=skill_dir,
            relative_path=relative_path,
        )
        if not os.path.exists(target_dir):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Skill file path not found.",
            )
        if not os.path.isdir(target_dir):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The requested path is not a directory.",
            )

        def _list() -> list[dict[str, Any]]:
            items: list[dict[str, Any]] = []
            for entry in sorted(
                os.scandir(target_dir),
                key=lambda item: (not item.is_dir(), item.name.lower()),
            ):
                rel_path = os.path.relpath(entry.path, skill_dir).replace(os.sep, "/")
                stat_result = entry.stat()
                mime_type = None if entry.is_dir() else mimetypes.guess_type(entry.name)[0]
                items.append(
                    {
                        "name": entry.name,
                        "path": "" if rel_path == "." else rel_path,
                        "is_dir": entry.is_dir(),
                        "mime_type": mime_type,
                        "size_bytes": None if entry.is_dir() else stat_result.st_size,
                        "mtime": stat_result.st_mtime,
                    },
                )
            return items

        return await asyncio.to_thread(_list)

    async def read_skill_file(
        self,
        *,
        user_id: str,
        skill_name: str,
        relative_path: str,
    ) -> tuple[str, bytes]:
        """Read one file within a stored skill package."""
        record = await self.get_skill(user_id, skill_name)
        skill_dir = self._skill_dir(user_id, record.name)
        target_path = self._resolve_relative_path(
            skill_dir=skill_dir,
            relative_path=relative_path,
        )
        if not os.path.isfile(target_path):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Skill file not found.",
            )
        return target_path, await asyncio.to_thread(Path(target_path).read_bytes)

    async def zip_skill(
        self,
        *,
        user_id: str,
        skill_name: str,
    ) -> tuple[str, bytes]:
        """Create a ZIP archive for one stored skill package."""
        record = await self.get_skill(user_id, skill_name)
        skill_dir = self._skill_dir(user_id, record.name)

        def _zip() -> tuple[str, bytes]:
            buf = io.BytesIO()
            with zipfile.ZipFile(
                buf,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                strict_timestamps=False,
            ) as zip_file:
                for root, _dirs, files in os.walk(skill_dir):
                    files.sort()
                    for file_name in files:
                        abs_path = os.path.join(root, file_name)
                        rel_path = os.path.relpath(abs_path, skill_dir)
                        zip_file.write(
                            abs_path,
                            arcname=os.path.join(record.name, rel_path),
                        )
            return f"{record.name}.zip", buf.getvalue()

        return await asyncio.to_thread(_zip)

    async def get_skill_local_dir(
        self,
        *,
        user_id: str,
        skill_name: str,
    ) -> tuple[SkillLibraryRecord, str]:
        """Return one skill record and its managed local directory."""
        record = await self.get_skill(user_id, skill_name)
        return record, self._skill_dir(user_id, record.name)
