# -*- coding: utf-8 -*-
"""Builtin tool for importing an agent package from the current workspace."""
from __future__ import annotations

import os
from typing import Any

from pydantic import Field

from ...message import ToolResultState
from ...tool import ParamsBase
from ._session_tool_base import _SessionToolBase
from .._service._agent_package_import import import_agent_package_from_upload


class _ImportAgentPackageParams(ParamsBase):
    """Parameters for :class:`ImportAgentPackage`."""

    package_path: str = Field(
        description=(
            "安装包在当前工作区中的路径。可以是安装包目录，也可以是 ZIP 文件。"
        ),
    )


class ImportAgentPackage(_SessionToolBase):
    """Import one agent package from the current workspace."""

    name = "ImportAgentPackage"
    description = (
        "Import an agent package from the current workspace path. "
        "Use this after generating or updating an agent package bundle."
    )
    input_schema: dict[str, Any] = _ImportAgentPackageParams.model_json_schema()
    is_read_only = False

    async def call(
        self,
        package_path: str,
    ):
        if self._agent_asset_store is None:
            return self._result(
                {"error": "Agent asset store is not configured."},
                state=ToolResultState.ERROR,
            )

        localized_tmp = None
        zip_tmp = None
        upload = None
        try:
            local_path, localized_tmp = await self._materialize_workspace_path(
                package_path,
            )
            if os.path.isdir(local_path):
                local_path, zip_tmp = self._zip_local_directory(
                    local_path,
                    prefix="agentscope-package-zip-",
                )
            elif not local_path.lower().endswith(".zip"):
                return self._result(
                    {
                        "error": (
                            "Package path must point to a package directory "
                            "or a .zip file."
                        ),
                    },
                    state=ToolResultState.ERROR,
                )

            upload = self._build_upload_file(local_path)
            response = await import_agent_package_from_upload(
                package_file=upload,
                user_id=self._user_id,
                storage=self._storage,
                asset_store=self._agent_asset_store,
            )
            payload = response.model_dump(mode="json")
            payload["package_path"] = package_path
            payload["source_workspace_id"] = self._workspace_id
            return self._result(payload)
        except Exception as exc:  # noqa: BLE001
            return self._result(
                {
                    "error": str(exc),
                    "package_path": package_path,
                },
                state=ToolResultState.ERROR,
            )
        finally:
            if upload is not None:
                await upload.close()
            if zip_tmp is not None:
                zip_tmp.cleanup()
            if localized_tmp is not None:
                localized_tmp.cleanup()
