# -*- coding: utf-8 -*-
"""Builtin tool for uploading one workspace ZIP into the skill library."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import frontmatter
from fastapi import HTTPException, UploadFile, status
from pydantic import Field

from ...message import TextBlock, ToolResultState
from ...permission import PermissionBehavior, PermissionContext, PermissionDecision
from ...tool import ParamsBase, ToolBase, ToolChunk
from ...tool._builtin._backend import LocalBackend
from ...workspace import WorkspaceBase
from .._service._agent_asset_store import AgentAssetStore
from .._service._skill_library import SkillLibraryService


class _UploadSkillToLibraryParams(ParamsBase):
    """The params of the upload skill tool."""

    skill_path: str = Field(
        description="Path to a .zip skill package inside the current workspace.",
        min_length=1,
    )


class UploadSkillToLibrary(ToolBase):
    """Upload or replace one skill package in the current user's library."""

    name = "UploadSkillToLibrary"
    description = (
        "Upload a skill ZIP from the current workspace into the skill "
        "library. If the library already contains a skill with the same "
        "declared name, replace it with the new package."
    )
    input_schema: dict[str, Any] = _UploadSkillToLibraryParams.model_json_schema()
    is_concurrency_safe: bool = False
    is_read_only = False
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(
        self,
        *,
        workspace: WorkspaceBase,
        user_id: str,
        skill_library_service: SkillLibraryService,
    ) -> None:
        super().__init__()
        self._workspace = workspace
        self._user_id = user_id
        self._skill_library_service = skill_library_service

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        del tool_input, context
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="Managing owned skills is allowed.",
        )

    def _result(
        self,
        payload: dict[str, object],
        *,
        state: ToolResultState = ToolResultState.SUCCESS,
    ) -> ToolChunk:
        """Build a compact JSON result for the agent."""
        return ToolChunk(
            content=[
                TextBlock(
                    text=json.dumps(
                        payload,
                        ensure_ascii=False,
                        indent=2,
                    ),
                ),
            ],
            state=state,
            metadata=payload,
        )

    def _normalize_workspace_path(self, path: str) -> str:
        """Resolve one path against the current workspace and enforce bounds."""
        backend = self._workspace.get_backend()
        normalized_workdir = backend.normpath(self._workspace.workdir)
        absolute = backend.abspath(path, cwd=normalized_workdir)
        if absolute == normalized_workdir:
            return absolute
        boundary = normalized_workdir.rstrip("/\\")
        if absolute.startswith(boundary + "/") or absolute.startswith(
            boundary + "\\",
        ):
            return absolute
        raise ValueError("Path must stay inside the current workspace.")

    async def _materialize_workspace_path(
        self,
        path: str,
    ) -> tuple[str, tempfile.TemporaryDirectory[str] | None]:
        """Copy one workspace file to local disk when needed."""
        backend = self._workspace.get_backend()
        absolute = self._normalize_workspace_path(path)
        if not await backend.file_exists(absolute):
            raise FileNotFoundError(f"Workspace path not found: {path}")
        if await backend.is_dir(absolute):
            raise IsADirectoryError("Skill path must point to a .zip file.")

        if isinstance(backend, LocalBackend):
            return absolute, None

        tmpdir = tempfile.TemporaryDirectory(prefix="agentscope-skill-upload-")
        local_path = os.path.join(tmpdir.name, Path(absolute).name)
        with open(local_path, "wb") as file_obj:
            file_obj.write(await backend.read_file(absolute))
        return local_path, tmpdir

    @staticmethod
    def _build_upload_file(path: str) -> UploadFile:
        """Wrap one local ZIP file as an UploadFile."""
        return UploadFile(
            filename=os.path.basename(path),
            file=open(path, "rb"),
        )

    @staticmethod
    def _read_declared_skill_name(path: str) -> str:
        """Read the declared skill name from one local ZIP package."""
        staging_root = tempfile.mkdtemp(prefix="skill_library_probe_")
        try:
            with zipfile.ZipFile(path) as zip_file:
                AgentAssetStore._assert_safe_zip(zip_file.infolist())
                zip_file.extractall(staging_root)
            skill_root = AgentAssetStore._find_skill_root(staging_root)
            skill_md_path = os.path.join(skill_root, "SKILL.md")
            with open(skill_md_path, "r", encoding="utf-8") as file_obj:
                markdown = file_obj.read()
            name = frontmatter.loads(markdown).get("name")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("SKILL.md must include a non-empty 'name'.")
            return name.strip()
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)

    async def call(
        self,
        skill_path: str,
    ):
        localized_tmp = None
        upload = None
        try:
            local_path, localized_tmp = await self._materialize_workspace_path(
                skill_path,
            )
            if not local_path.lower().endswith(".zip"):
                return self._result(
                    {
                        "error": "Skill path must point to a .zip file.",
                        "skill_path": skill_path,
                    },
                    state=ToolResultState.ERROR,
                )

            skill_name = self._read_declared_skill_name(local_path)
            action = "updated"
            try:
                await self._skill_library_service.get_skill(
                    self._user_id,
                    skill_name,
                )
            except HTTPException as exc:
                if exc.status_code != status.HTTP_404_NOT_FOUND:
                    raise
                action = "created"

            upload = self._build_upload_file(local_path)
            record = await self._skill_library_service.upload_skill(
                user_id=self._user_id,
                file=upload,
                overwrite=True,
            )
            return self._result(
                {
                    "skill": record.name,
                    "description": record.description,
                    "action": action,
                },
            )
        except Exception as exc:  # noqa: BLE001
            return self._result(
                {
                    "error": str(exc),
                    "skill_path": skill_path,
                },
                state=ToolResultState.ERROR,
            )
        finally:
            if upload is not None:
                await upload.close()
            if localized_tmp is not None:
                localized_tmp.cleanup()
