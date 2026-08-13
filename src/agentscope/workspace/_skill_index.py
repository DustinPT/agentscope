# -*- coding: utf-8 -*-
"""Shared .skills index management for workspace implementations."""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import TypedDict

import frontmatter

from .._logging import logger
from ..skill import Skill
from .._utils._fs import _hash_directory

_SKILLS_FILE_VERSION = 2


class _SkillEntry(TypedDict):
    """A single entry in the .skills index file."""

    hash: str
    """SHA-256 hash of the full skill directory contents."""
    skill_name: str
    """The name exposed to the agent."""


class _SkillsFile(TypedDict):
    """Schema of the .skills index file stored inside skills_dir."""

    version: int
    """Schema version of the .skills index."""
    skills: dict[str, _SkillEntry]
    """Mapping from directory name to one indexed skill entry."""


def _sanitize_dir_name(name: str) -> str:
    """Sanitize a skill name into a safe directory name."""
    return re.sub(r"[^\w一-鿿-]", "_", name)

def _fresh_skills_file() -> _SkillsFile:
    """Return an empty .skills structure."""
    return {
        "version": _SKILLS_FILE_VERSION,
        "skills": {},
    }


def _parse_skills_file(data: object) -> _SkillsFile | None:
    """Validate and normalize one .skills payload."""
    if not isinstance(data, dict):
        return None

    if data.get("version") != _SKILLS_FILE_VERSION:
        return None
    skills = data.get("skills")
    if not isinstance(skills, dict):
        return None

    normalized_skills: dict[str, _SkillEntry] = {}
    for dir_name, entry in skills.items():
        if not isinstance(dir_name, str) or not isinstance(entry, dict):
            return None
        hash_value = entry.get("hash")
        skill_name = entry.get("skill_name")
        if not isinstance(hash_value, str) or not isinstance(skill_name, str):
            return None
        normalized_skills[dir_name] = {
            "hash": hash_value,
            "skill_name": skill_name,
        }

    return {
        "version": _SKILLS_FILE_VERSION,
        "skills": normalized_skills,
    }


def _read_host_skill_frontmatter(
    skill_md_path: str,
) -> tuple[str, str, str] | None:
    """Read and validate one host-side SKILL.md file."""
    if not os.path.isfile(skill_md_path):
        return None

    with open(skill_md_path, "r", encoding="utf-8") as file_obj:
        content_str = file_obj.read()
    content = frontmatter.loads(content_str)
    name = content.get("name")
    description = content.get("description")
    if not name or not description:
        return None
    return str(name), str(description), content_str


class SkillIndexMixin:
    """Shared skill index logic backed by a workspace backend."""

    async def _load_skills_file(
        self,
        skills_dir: str,
    ) -> tuple[_SkillsFile, bool]:
        """Load the .skills index file and report whether it is usable."""
        backend = self.get_backend()
        path = backend.join_path(skills_dir, ".skills")
        if not await backend.file_exists(path):
            return _fresh_skills_file(), False

        try:
            payload = json.loads(
                (await backend.read_file(path)).decode("utf-8"),
            )
            data = _parse_skills_file(payload)
            if data is None:
                logger.warning(
                    "Invalid .skills format in %s, full skill resync is required.",
                    path,
                )
                return _fresh_skills_file(), False
            return data, True
        except Exception as exc:
            logger.warning("Failed to load .skills from %s: %s", path, exc)
            return _fresh_skills_file(), False

    async def _save_skills_file(
        self,
        skills_dir: str,
        data: _SkillsFile,
    ) -> None:
        """Persist one .skills index file."""
        backend = self.get_backend()
        path = backend.join_path(skills_dir, ".skills")
        try:
            await backend.write_file(
                path,
                json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"),
            )
        except Exception as exc:
            logger.warning("Failed to save .skills to %s: %s", path, exc)

    async def has_valid_skill_index(self) -> bool:
        """Return whether the default agent namespace has a usable .skills."""
        return await self._agent_has_valid_skill_index(self._default_agent_id())

    async def _agent_has_valid_skill_index(self, agent_id: str) -> bool:
        """Return whether one agent namespace has a usable .skills."""
        await self._ensure_agent_dirs(agent_id)
        _, is_valid = await self._load_skills_file(self._agent_skills_dir(agent_id))
        return is_valid

    async def reset_skills_state(self) -> None:
        """Clear skill directories and rewrite a fresh .skills index."""
        await self._reset_agent_skills_state(self._default_agent_id())

    async def _reset_agent_skills_state(self, agent_id: str) -> None:
        """Clear all skills for one agent namespace and recreate .skills."""
        backend = self.get_backend()
        skills_dir = self._agent_skills_dir(agent_id)
        async with self._skill_lock:
            await self._ensure_agent_dirs(agent_id)
            for entry in await backend.scandir(skills_dir):
                await backend.delete_path(
                    backend.join_path(skills_dir, entry.name),
                )
            await self._save_skills_file(skills_dir, _fresh_skills_file())

    async def _validate_skill(
        self,
        skill_path: str,
    ) -> tuple[str, str, str] | None:
        """Validate if a host path contains a valid SKILL.md file."""
        skill_md_path = os.path.join(skill_path, "SKILL.md")
        try:
            result = await asyncio.to_thread(
                _read_host_skill_frontmatter,
                skill_md_path,
            )
        except Exception as exc:
            logger.warning("Failed to validate skill at %s: %s", skill_path, exc)
            return None

        if result is None:
            logger.warning(
                "Invalid skill at %s: SKILL.md missing required fields",
                skill_path,
            )
            return None
        return result

    async def _validate_and_hash_skill(
        self,
        skill_path: str,
    ) -> tuple[str, str, str] | None:
        """Validate a host-local skill and compute its hash."""
        validation_result = await self._validate_skill(skill_path)
        if validation_result is None:
            return None

        skill_name, _description, _skill_md_content = validation_result
        skill_hash = await asyncio.to_thread(_hash_directory, skill_path)
        return skill_path, skill_name, skill_hash

    async def list_skills(self) -> list[Skill]:
        """List skills for the direct-use default agent namespace."""
        return await self._list_agent_skills(self._default_agent_id())

    async def _list_agent_skills(self, agent_id: str) -> list[Skill]:
        """List all indexed skills for one agent namespace."""
        backend = self.get_backend()
        skills_dir = self._agent_skills_dir(agent_id)
        async with self._skill_lock:
            await self._ensure_agent_dirs(agent_id)
            skills_file, is_valid = await self._load_skills_file(skills_dir)
            if not is_valid:
                return []

            tasks = [
                self._load_single_skill(
                    backend.join_path(skills_dir, dir_name),
                    entry["skill_name"],
                    entry["hash"],
                )
                for dir_name, entry in skills_file["skills"].items()
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            skills: list[Skill] = []
            for dir_name, result in zip(skills_file["skills"], results):
                if isinstance(result, Exception):
                    logger.warning(
                        "Failed to load skill from %s: %s",
                        dir_name,
                        result,
                    )
                elif result is not None:
                    skills.append(result)
            return skills

    async def _load_single_skill(
        self,
        skill_dir: str,
        skill_name: str,
        content_hash: str,
    ) -> Skill | None:
        """Load one indexed skill from backend storage."""
        backend = self.get_backend()
        skill_md_path = backend.join_path(skill_dir, "SKILL.md")
        try:
            if not await backend.file_exists(skill_md_path):
                return None

            content = frontmatter.loads(
                (await backend.read_file(skill_md_path)).decode("utf-8"),
            )
            description = content.get("description")
            if not description:
                logger.warning(
                    "SKILL.md in %s is missing 'description'. Skipping.",
                    skill_dir,
                )
                return None

            return Skill(
                name=skill_name,
                description=str(description),
                dir=skill_dir,
                markdown=content.content,
                content_hash=content_hash,
            )
        except Exception as exc:
            logger.warning("Failed to load skill from %s: %s", skill_dir, exc)
            return None

    async def add_skill(self, skill_path: str) -> None:
        """Add a skill to the direct-use default agent namespace."""
        await self._add_agent_skill(self._default_agent_id(), skill_path)

    async def _add_agent_skill(
        self,
        agent_id: str,
        skill_path: str,
    ) -> None:
        """Add one host-local skill directory into the workspace."""
        backend = self.get_backend()
        skills_dir = self._agent_skills_dir(agent_id)
        async with self._skill_lock:
            await self._ensure_agent_dirs(agent_id)

            result = await self._validate_and_hash_skill(skill_path)
            if result is None:
                raise ValueError(
                    f"Invalid skill at {skill_path!r}: missing or malformed "
                    "SKILL.md (requires 'name' and 'description' fields).",
                )

            _, raw_name, skill_hash = result
            skills_file, _ = await self._load_skills_file(skills_dir)
            existing: dict[str, _SkillEntry] = skills_file["skills"]

            existing_hashes = {entry["hash"] for entry in existing.values()}
            if skill_hash in existing_hashes:
                logger.info(
                    "Skill '%s' (hash: %s...) already exists, skipping",
                    raw_name,
                    skill_hash[:8],
                )
                return

            existing_agent_names = {
                entry["skill_name"] for entry in existing.values()
            }
            existing_dir_names = set(existing.keys())

            agent_name = raw_name
            counter = 1
            while agent_name in existing_agent_names:
                agent_name = f"{raw_name} ({counter})"
                counter += 1

            base_dir = _sanitize_dir_name(raw_name)
            dir_name = base_dir
            counter = 1
            while (
                dir_name in existing_dir_names
                or await backend.file_exists(backend.join_path(skills_dir, dir_name))
            ):
                dir_name = f"{base_dir}_{counter}"
                counter += 1

            dest_path = backend.join_path(skills_dir, dir_name)
            await backend.upload_directory(skill_path, dest_path)

            existing[dir_name] = {
                "hash": skill_hash,
                "skill_name": agent_name,
            }
            skills_file["skills"] = existing
            await self._save_skills_file(skills_dir, skills_file)

    async def remove_skill(self, name: str) -> None:
        """Remove a skill from the direct-use default agent namespace."""
        await self._remove_agent_skill(self._default_agent_id(), name)

    async def _remove_agent_skill(
        self,
        agent_id: str,
        name: str,
    ) -> None:
        """Remove one indexed skill by its agent-facing name."""
        backend = self.get_backend()
        skills_dir = self._agent_skills_dir(agent_id)
        async with self._skill_lock:
            await self._ensure_agent_dirs(agent_id)
            skills_file, _ = await self._load_skills_file(skills_dir)
            existing: dict[str, _SkillEntry] = skills_file["skills"]

            target_dir: str | None = None
            for dir_name, entry in existing.items():
                if entry["skill_name"] == name:
                    target_dir = dir_name
                    break

            if target_dir is None:
                logger.warning("Skill %r not found in workspace", name)
                return

            await backend.delete_path(backend.join_path(skills_dir, target_dir))
            del existing[target_dir]
            skills_file["skills"] = existing
            await self._save_skills_file(skills_dir, skills_file)
