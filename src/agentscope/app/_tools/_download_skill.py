# -*- coding: utf-8 -*-
"""Builtin tool for downloading a skill into the current workspace."""

from __future__ import annotations

import json
from typing import Any

from pydantic import Field

from ...permission import PermissionBehavior, PermissionContext, PermissionDecision
from ...tool import ParamsBase
from .._service._skill_library import SkillLibraryService
from ._session_tool_base import _SessionToolBase


class _DownloadSkillParams(ParamsBase):
    """The params of the download skill tool."""

    skill: str = Field(
        description="Exact skill name to download and make available in the current workspace.",
        min_length=1,
    )


class DownloadSkill(_SessionToolBase):
    """Download a user-owned skill to the current workspace skills directory."""

    name = "DownloadSkill"
    description = (
        "Download a skill by its exact name so you can read and use it in "
        "the current workspace. Use this after you have identified the "
        "right skill, typically from SearchSkills. The result includes the "
        "local path to the downloaded SKILL.md file."
    )
    input_schema: dict[str, Any] = _DownloadSkillParams.model_json_schema()
    is_read_only = False

    def __init__(
        self,
        *,
        skill_library_service: SkillLibraryService,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._skill_library_service = skill_library_service

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        del tool_input, context
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="Downloading owned skills is allowed.",
        )

    async def call(
        self,
        skill: str,
    ):
        record, local_skill_dir = await self._skill_library_service.get_skill_local_dir(
            user_id=self._user_id,
            skill_name=skill,
        )
        backend = self._workspace.get_backend()
        skills_root = backend.join_path(self._workspace.workdir, "skills")
        target_dir = backend.join_path(skills_root, record.name)
        skill_md_path = backend.join_path(target_dir, "SKILL.md")
        metadata_path = backend.join_path(
            skills_root,
            f".{record.name}.json",
        )

        cached = False
        if await backend.file_exists(metadata_path) and await backend.file_exists(skill_md_path):
            try:
                metadata = json.loads(
                    (await backend.read_file(metadata_path)).decode("utf-8"),
                )
                cached = metadata.get("content_hash") == record.content_hash
            except Exception:
                cached = False

        await backend.ensure_dir(skills_root)
        if not cached:
            if await backend.file_exists(target_dir):
                await backend.delete_path(target_dir)
            await backend.upload_directory(local_skill_dir, target_dir)
            metadata_payload = {
                "name": record.name,
                "content_hash": record.content_hash,
                "downloaded_at": record.updated_at.isoformat(),
            }
            await backend.write_file(
                metadata_path,
                json.dumps(
                    metadata_payload,
                    ensure_ascii=False,
                    indent=2,
                ).encode("utf-8"),
            )

        return self._result(
            {
                "skill": record.name,
                "skill_md_path": skill_md_path,
            },
        )
