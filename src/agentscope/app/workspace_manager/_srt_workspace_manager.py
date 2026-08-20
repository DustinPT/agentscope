# -*- coding: utf-8 -*-
"""Workspace manager for SRT-backed local workspaces."""

from __future__ import annotations

import asyncio
import importlib.resources as resources
import json
import os
import sys
import time
import uuid
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
from ..storage import AgentMCPAsset, AgentSkillAsset
from ._local_workspace_manager import LocalWorkspaceManager


class SRTWorkspaceManager(LocalWorkspaceManager):
    """Manage SRTWorkspace instances with LocalWorkspaceManager semantics."""

    def __init__(
        self,
        basedir: str,
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

    def _settings_path_for(self, workspace_id: str) -> str:
        """Return the derived SRT settings path for one workspace."""
        return os.path.join(self._basedir, workspace_id, ".srt-settings.json")

    def _ensure_workspace_layout(self, workspace_id: str) -> tuple[str, str]:
        """Ensure one workspace has both workdir and fresh derived SRT settings."""
        workdir = os.path.join(self._basedir, workspace_id)
        settings_path = self._settings_path_for(workspace_id)
        os.makedirs(workdir, exist_ok=True)
        _write_derived_srt_settings(
            template_path=self._default_srt_settings_path,
            target_path=settings_path,
            workdir=workdir,
        )
        return workdir, settings_path

    def _build_workspace(self, workspace_id: str) -> SRTWorkspace:
        """Construct one SRTWorkspace for an existing or newly materialized workdir."""
        workdir, settings_path = self._ensure_workspace_layout(workspace_id)
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
        workspace_id: str,
        agent_mcps: list[MCPClient] | None = None,
        agent_mcp_assets: list[AgentMCPAsset] | None = None,
        agent_skill_assets: list[AgentSkillAsset] | None = None,
    ) -> AgentWorkspaceView:
        """Return an initialized SRT workspace, rebuilding on cache miss."""
        del user_id, session_id

        async with self._lock:
            now = time.monotonic()
            expired = self._pop_expired(now)
            cached = self._cache.get(workspace_id)
            if cached is not None:
                ws, _ = cached
                self._cache[workspace_id] = (ws, now)
                hit = ws
            else:
                hit = None

        if expired:
            await asyncio.gather(
                *(self._safe_close(ws) for ws in expired),
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

            ws = self._build_workspace(workspace_id)
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

    async def create_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> AgentWorkspaceView:
        """Create a new SRT workspace and persist its derived config."""
        del user_id, session_id

        workspace_id = uuid.uuid4().hex
        ws = self._build_workspace(workspace_id)
        await ws.initialize()
        view = AgentWorkspaceView(ws, agent_id)
        await sync_workspace_state(
            view,
            expected_mcps=[],
            expected_mcp_assets=[],
            expected_skills=[],
            manager_default_mcps=self._default_mcps,
            manager_skill_paths=self._skill_paths,
        )
        async with self._lock:
            self._cache[ws.workspace_id] = (ws, time.monotonic())
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


def _write_derived_srt_settings(
    *,
    template_path: str | None,
    target_path: str,
    workdir: str,
) -> None:
    """Create a workspace-specific SRT settings file from the template."""
    config = _load_srt_template_config(template_path)

    network = config.setdefault("network", {})
    filesystem = config.setdefault("filesystem", {})

    filesystem["allowRead"] = _append_unique_path(
        filesystem.get("allowRead"),
        workdir,
    )
    filesystem["allowWrite"] = _append_unique_path(
        filesystem.get("allowWrite"),
        workdir,
    )
    venv_dir = active_virtualenv_dir()
    if venv_dir is not None:
        filesystem["allowRead"] = _append_unique_path(
            filesystem.get("allowRead"),
            venv_dir,
        )
    for path in runtime_module_search_paths(
        "agentscope.workspace._srt._local_runtime_service",
    ):
        filesystem["allowRead"] = _append_unique_path(
            filesystem.get("allowRead"),
            path,
        )
    network["allowLocalBinding"] = True

    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    with open(target_path, "w", encoding="utf-8") as file_obj:
        json.dump(config, file_obj, indent=2, ensure_ascii=False)


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


def _append_unique_path(existing: Any, path: str) -> list[str]:
    """Append one path to a JSON list field while preserving order."""
    values = [item for item in (existing or []) if isinstance(item, str)]
    if path not in values:
        values.append(path)
    return values
