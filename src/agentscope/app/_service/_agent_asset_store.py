# -*- coding: utf-8 -*-
"""Managed skill asset storage for agent-level workspace configuration."""

import asyncio
import hashlib
import io
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import frontmatter
from fastapi import HTTPException, UploadFile, status

from ..storage import AgentSkillAsset


@dataclass
class StagedSkillAsset:
    """Temporary extracted skill awaiting commit."""

    name: str
    description: str
    archive_name: str
    temp_dir: str
    content_hash: str


class AgentAssetStore:
    """Manage staged and committed agent skill assets on local disk."""

    def __init__(self, root_dir: str) -> None:
        self._root_dir = os.path.abspath(root_dir)
        self._staging_dir = os.path.join(self._root_dir, ".staging")

    @property
    def root_dir(self) -> str:
        """Return the absolute root directory."""
        return self._root_dir

    def _agent_dir(self, user_id: str, agent_id: str) -> str:
        return os.path.join(self._root_dir, user_id, agent_id)

    @staticmethod
    def _assert_safe_zip(members: list[zipfile.ZipInfo]) -> None:
        for member in members:
            path = member.filename
            if not path or path.endswith("/"):
                continue
            normalized = Path(path)
            if normalized.is_absolute() or ".." in normalized.parts:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid ZIP entry path: {path}",
                )

    @staticmethod
    def _find_skill_root(extract_dir: str) -> str:
        candidates: list[str] = []
        for root, _dirs, files in os.walk(extract_dir):
            if "SKILL.md" in files:
                candidates.append(root)
        if not candidates:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Skill ZIP must contain a SKILL.md file.",
            )
        if len(candidates) > 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Skill ZIP must contain exactly one skill root.",
            )
        return candidates[0]

    @staticmethod
    def _read_skill_metadata(skill_dir: str) -> tuple[str, str, str]:
        skill_md_path = os.path.join(skill_dir, "SKILL.md")
        with open(skill_md_path, "r", encoding="utf-8") as f:
            raw = f.read()
        doc = frontmatter.loads(raw)
        name = doc.get("name")
        description = doc.get("description")
        if not name or not description:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="SKILL.md must include both 'name' and 'description'.",
            )
        return str(name), str(description), hashlib.sha256(
            raw.encode("utf-8"),
        ).hexdigest()

    async def stage_skill_zip(
        self,
        file: UploadFile,
    ) -> StagedSkillAsset:
        """Validate and extract an uploaded ZIP file into a staging area."""
        if not file.filename or not file.filename.lower().endswith(".zip"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only .zip skill packages are supported.",
            )

        payload = await file.read()
        if not payload:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded skill package is empty.",
            )

        def _stage() -> StagedSkillAsset:
            os.makedirs(self._staging_dir, exist_ok=True)
            staging_root = tempfile.mkdtemp(
                prefix="skill_",
                dir=self._staging_dir,
            )
            try:
                with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                    self._assert_safe_zip(zf.infolist())
                    zf.extractall(staging_root)
                skill_root = self._find_skill_root(staging_root)
                name, description, content_hash = self._read_skill_metadata(
                    skill_root,
                )
                return StagedSkillAsset(
                    name=name,
                    description=description,
                    archive_name=file.filename or f"{name}.zip",
                    temp_dir=skill_root,
                    content_hash=content_hash,
                )
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

    async def commit_staged_skills(
        self,
        user_id: str,
        agent_id: str,
        staged: list[StagedSkillAsset],
    ) -> list[AgentSkillAsset]:
        """Move staged skills into the managed agent asset directory."""

        def _commit() -> list[AgentSkillAsset]:
            agent_dir = self._agent_dir(user_id, agent_id)
            os.makedirs(agent_dir, exist_ok=True)
            committed: list[AgentSkillAsset] = []
            for item in staged:
                target_dir = os.path.join(agent_dir, item.name)
                if os.path.exists(target_dir):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Skill '{item.name}' already exists.",
                    )
                shutil.move(item.temp_dir, target_dir)
                committed.append(
                    AgentSkillAsset(
                        name=item.name,
                        description=item.description,
                        archive_name=item.archive_name,
                        dir=target_dir,
                        content_hash=item.content_hash,
                    ),
                )
            return committed

        return await asyncio.to_thread(_commit)

    async def cleanup_staged_skills(
        self,
        staged: list[StagedSkillAsset],
    ) -> None:
        """Remove staging directories for uncommitted skill uploads."""

        def _cleanup() -> None:
            for item in staged:
                shutil.rmtree(
                    os.path.dirname(item.temp_dir),
                    ignore_errors=True,
                )

        await asyncio.to_thread(_cleanup)

    async def import_skill_dir(
        self,
        user_id: str,
        agent_id: str,
        skill_path: str,
    ) -> AgentSkillAsset:
        """Copy an existing local skill directory into managed storage."""

        def _import() -> AgentSkillAsset:
            source_dir = os.path.abspath(skill_path)
            if not os.path.isdir(source_dir):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Skill path does not exist: {skill_path}",
                )
            name, description, content_hash = self._read_skill_metadata(source_dir)
            target_dir = os.path.join(self._agent_dir(user_id, agent_id), name)
            if os.path.exists(target_dir):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Skill '{name}' already exists.",
                )
            os.makedirs(os.path.dirname(target_dir), exist_ok=True)
            shutil.copytree(source_dir, target_dir)
            return AgentSkillAsset(
                name=name,
                description=description,
                archive_name=f"{name}.zip",
                dir=target_dir,
                content_hash=content_hash,
            )

        return await asyncio.to_thread(_import)

    async def delete_skills(
        self,
        user_id: str,
        agent_id: str,
        skill_names: list[str],
    ) -> None:
        """Delete committed skill directories by name."""

        def _delete() -> None:
            agent_dir = self._agent_dir(user_id, agent_id)
            for skill_name in skill_names:
                shutil.rmtree(
                    os.path.join(agent_dir, skill_name),
                    ignore_errors=True,
                )

        await asyncio.to_thread(_delete)

    async def zip_skill(
        self,
        user_id: str,
        agent_id: str,
        skill_name: str,
    ) -> tuple[str, bytes]:
        """Create a ZIP archive from a committed skill directory."""

        def _zip() -> tuple[str, bytes]:
            skill_dir = os.path.join(self._agent_dir(user_id, agent_id), skill_name)
            if not os.path.isdir(skill_dir):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Skill '{skill_name}' not found.",
                )
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for root, _dirs, files in os.walk(skill_dir):
                    for file_name in files:
                        abs_path = os.path.join(root, file_name)
                        rel_path = os.path.relpath(abs_path, skill_dir)
                        zf.write(abs_path, arcname=os.path.join(skill_name, rel_path))
            return f"{skill_name}.zip", buf.getvalue()

        return await asyncio.to_thread(_zip)
