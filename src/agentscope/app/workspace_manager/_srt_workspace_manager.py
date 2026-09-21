# -*- coding: utf-8 -*-
"""Workspace manager for SRT-backed local workspaces."""

from __future__ import annotations

import asyncio
import importlib.resources as resources
import json
import os
import sys
import time
from typing import Any

from ..._logging import logger
from ...mcp import MCPClient
from ...workspace import AgentWorkspaceView
from ...workspace._srt._paths import (
    active_virtualenv_dir,
    runtime_module_search_paths,
)
from ...workspace._srt._srt_workspace import SRTWorkspace
from .._service._workspace_seed import sync_workspace_state
from ..storage import (
    AgentMCPAsset,
    AgentSkillAsset,
    SandboxGrantResourceType,
    SandboxOperation,
    SandboxPermissionGrant,
    merge_sandbox_grants,
)
from ._base import IsolationPolicy
from ._local_workspace_manager import LocalWorkspaceManager


class SRTWorkspaceManager(LocalWorkspaceManager):
    """Manage SRTWorkspace instances with LocalWorkspaceManager semantics."""

    def __init__(
        self,
        basedir: str,
        *,
        isolation: IsolationPolicy = IsolationPolicy.PER_AGENT,
        default_srt_settings_path: str | None = None,
        srt_executable: str = "srt",
        default_mcps: list | None = None,
        skill_paths: list[str] | None = None,
        ttl: float = 3600.0,
        service_host: str = "127.0.0.1",
        startup_timeout: float = 30.0,
    ) -> None:
        super().__init__(
            basedir=basedir,
            isolation=isolation,
            default_mcps=default_mcps,
            skill_paths=skill_paths,
            ttl=ttl,
        )
        self._default_srt_settings_path = (
            os.path.abspath(default_srt_settings_path)
            if default_srt_settings_path is not None
            else None
        )
        self._srt_executable = srt_executable
        self._service_host = service_host
        self._startup_timeout = startup_timeout

    def _settings_path_for(self, workspace_id: str, user_id: str) -> str:
        """Return the derived SRT settings path for one workspace."""
        return os.path.join(self._basedir, user_id, workspace_id, ".srt-settings.json")

    async def _ensure_workspace_layout(
        self,
        workspace_id: str,
        user_id: str,
        agent_id: str,
    ) -> tuple[str, str]:
        """Ensure one workspace has both workdir and fresh derived SRT settings."""
        user_dir = os.path.join(self._basedir, user_id)
        workdir = os.path.join(user_dir, workspace_id)
        settings_path = self._settings_path_for(workspace_id, user_id)
        os.makedirs(workdir, exist_ok=True)
        settings = await self._build_expected_settings(
            workspace_id=workspace_id,
            user_dir=user_dir,
            user_id=user_id,
            agent_id=agent_id,
        )
        _write_derived_srt_settings(
            target_path=settings_path,
            config=settings,
        )
        return workdir, settings_path

    async def _build_expected_settings(
        self,
        *,
        workspace_id: str,
        user_dir: str,
        user_id: str,
        agent_id: str,
    ) -> dict[str, Any]:
        """Build the current derived SRT settings for one workspace."""
        return await _build_derived_srt_settings(
            template_path=self._default_srt_settings_path,
            user_dir=user_dir,
            storage=self._storage,
            user_id=user_id,
            agent_id=agent_id,
            workspace_id=workspace_id,
        )

    async def _workspace_requires_rebuild(
        self,
        *,
        workspace_id: str,
        user_id: str,
        agent_id: str,
    ) -> bool:
        """Return whether the on-disk settings differ from expected state."""
        user_dir = os.path.join(self._basedir, user_id)
        settings_path = self._settings_path_for(workspace_id, user_id)
        expected = await self._build_expected_settings(
            workspace_id=workspace_id,
            user_dir=user_dir,
            user_id=user_id,
            agent_id=agent_id,
        )
        current = _load_existing_srt_settings(settings_path)
        return current != expected

    async def _build_workspace(
        self,
        workspace_id: str,
        user_id: str,
        agent_id: str,
    ) -> SRTWorkspace:
        """Construct one SRTWorkspace for an existing or newly materialized workdir."""
        workdir, settings_path = await self._ensure_workspace_layout(
            workspace_id,
            user_id,
            agent_id,
        )
        return SRTWorkspace(
            workspace_id=workspace_id,
            workdir=workdir,
            srt_settings_path=settings_path,
            srt_executable=self._srt_executable,
            service_host=self._service_host,
            startup_timeout=self._startup_timeout,
        )

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str | None,
        agent_mcps: list[MCPClient] | None = None,
        agent_mcp_assets: list[AgentMCPAsset] | None = None,
        agent_skill_assets: list[AgentSkillAsset] | None = None,
    ) -> AgentWorkspaceView:
        """Return an initialized SRT workspace, rebuilding on cache miss."""
        if workspace_id is None:
            workspace_id = await self.assign_workspace_id(
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
            )

        async with self._lock:
            now = time.monotonic()
            expired = self._pop_expired(now)
            rebuild: list[SRTWorkspace] = []
            cached = self._cache.get(workspace_id)
            if cached is not None:
                ws, _ = cached
                if await self._workspace_requires_rebuild(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    agent_id=agent_id,
                ):
                    self._cache.pop(workspace_id, None)
                    rebuild.append(ws)
                    hit = None
                else:
                    self._cache[workspace_id] = (ws, now)
                    hit = ws
            else:
                hit = None

        stale = [*expired, *rebuild]
        if stale:
            await asyncio.gather(
                *(self._safe_close(ws) for ws in stale),
                return_exceptions=True,
            )

        if hit is not None:
            view = AgentWorkspaceView(hit, agent_id)
            await sync_workspace_state(
                view,
                expected_mcps=agent_mcps or [],
                expected_mcp_assets=agent_mcp_assets or [],
                expected_skills=agent_skill_assets or [],
                manager_default_mcps=self._default_mcps,
                manager_skill_paths=self._skill_paths,
            )
            return view

        async with self._lock:
            cached = self._cache.get(workspace_id)
            if cached is not None:
                ws, _ = cached
                if await self._workspace_requires_rebuild(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    agent_id=agent_id,
                ):
                    self._cache.pop(workspace_id, None)
                    await self._safe_close(ws)
                else:
                    self._cache[workspace_id] = (ws, time.monotonic())
                    view = AgentWorkspaceView(ws, agent_id)
                    await sync_workspace_state(
                        view,
                        expected_mcps=agent_mcps or [],
                        expected_mcp_assets=agent_mcp_assets or [],
                        expected_skills=agent_skill_assets or [],
                        manager_default_mcps=self._default_mcps,
                        manager_skill_paths=self._skill_paths,
                    )
                    return view

            ws = await self._build_workspace(workspace_id, user_id, agent_id)
            await ws.initialize()
            view = AgentWorkspaceView(ws, agent_id)
            await sync_workspace_state(
                view,
                expected_mcps=agent_mcps or [],
                expected_mcp_assets=agent_mcp_assets or [],
                expected_skills=agent_skill_assets or [],
                manager_default_mcps=self._default_mcps,
                manager_skill_paths=self._skill_paths,
            )
            self._cache[workspace_id] = (ws, time.monotonic())
            return view

    @staticmethod
    async def _safe_close(ws: SRTWorkspace) -> None:
        """Close one workspace while swallowing shutdown exceptions."""
        try:
            await ws.close()
        except Exception:
            logger.exception(
                "Failed to close SRTWorkspace %s",
                ws.workspace_id,
            )


async def _build_derived_srt_settings(
    *,
    template_path: str | None,
    user_dir: str,
    storage: Any,
    user_id: str,
    agent_id: str,
    workspace_id: str,
) -> dict[str, Any]:
    """Build one workspace-specific SRT settings config from template + grants."""
    config = _load_srt_template_config(template_path)

    network = config.setdefault("network", {})
    filesystem = config.setdefault("filesystem", {})

    filesystem["allowRead"] = _append_unique_value(
        filesystem.get("allowRead"),
        user_dir,
    )
    filesystem["allowWrite"] = _append_unique_value(
        filesystem.get("allowWrite"),
        user_dir,
    )
    venv_dir = active_virtualenv_dir()
    if venv_dir is not None:
        filesystem["allowRead"] = _append_unique_value(
            filesystem.get("allowRead"),
            venv_dir,
        )
    for path in runtime_module_search_paths(
        "agentscope.workspace._srt._local_runtime_service",
    ):
        filesystem["allowRead"] = _append_unique_value(
            filesystem.get("allowRead"),
            path,
        )
    network["allowLocalBinding"] = True

    grants = await _load_sandbox_grants(
        storage=storage,
        user_id=user_id,
        agent_id=agent_id,
        workspace_id=workspace_id,
    )
    for grant in grants:
        if grant.resource_type == SandboxGrantResourceType.DOMAIN:
            if SandboxOperation.CONNECT in grant.operations:
                network["allowedDomains"] = _append_unique_value(
                    network.get("allowedDomains"),
                    grant.pattern,
                )
            continue

        if SandboxOperation.READ in grant.operations:
            filesystem["allowRead"] = _append_unique_value(
                filesystem.get("allowRead"),
                grant.pattern,
            )
        if SandboxOperation.WRITE in grant.operations:
            filesystem["allowWrite"] = _append_unique_value(
                filesystem.get("allowWrite"),
                grant.pattern,
            )

    return config


async def _load_sandbox_grants(
    *,
    storage: Any,
    user_id: str,
    agent_id: str,
    workspace_id: str,
) -> list[SandboxPermissionGrant]:
    """Load and merge sandbox grants from user, agent, and workspace scopes."""
    if storage is None:
        return []

    user_record = await storage.get_sandbox_permissions_for_user(user_id)
    agent_record = await storage.get_sandbox_permissions_for_agent(
        user_id,
        agent_id,
    )
    workspace_record = await storage.get_sandbox_permissions_for_workspace(
        user_id,
        workspace_id,
    )
    return merge_sandbox_grants(
        user_record.grants if user_record is not None else [],
        agent_record.grants if agent_record is not None else [],
        workspace_record.grants if workspace_record is not None else [],
    )


def _write_derived_srt_settings(
    *,
    target_path: str,
    config: dict[str, Any],
) -> None:
    """Write one workspace-specific SRT settings file."""
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    with open(target_path, "w", encoding="utf-8") as file_obj:
        json.dump(config, file_obj, indent=2, ensure_ascii=False)


def _load_existing_srt_settings(path: str) -> dict[str, Any] | None:
    """Load one existing derived SRT settings file if present."""
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as file_obj:
        return json.load(file_obj)


def _load_srt_template_config(template_path: str | None) -> dict[str, Any]:
    """Load one SRT template config from a user path or built-in default."""
    if template_path is not None:
        with open(template_path, encoding="utf-8") as file_obj:
            return json.load(file_obj)

    resource_name = _platform_default_srt_settings_name()
    package_root = "agentscope.workspace._srt"
    resource = resources.files(package_root).joinpath(resource_name)
    with resource.open("r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def _platform_default_srt_settings_name() -> str:
    """Return the packaged default SRT settings filename for this platform."""
    if sys.platform == "darwin":
        return "default_srt_settings_macos.json"
    if sys.platform.startswith("linux"):
        return "default_srt_settings_linux.json"
    if sys.platform == "win32":
        return "default_srt_settings_windows.json"
    raise RuntimeError(f"Unsupported platform for built-in SRT settings: {sys.platform}")


def _append_unique_value(existing: Any, value: str) -> list[str]:
    """Append one string to a JSON list field while preserving order."""
    values = [item for item in (existing or []) if isinstance(item, str)]
    if value not in values:
        values.append(value)
    return values
