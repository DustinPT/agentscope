# -*- coding: utf-8 -*-
"""Workspace router — list runtime MCP clients and skills on a workspace."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from .._service._agent_asset_store import AgentAssetStore
from ..deps import (
    get_agent_asset_store,
    get_current_user_id,
    get_storage,
    get_workspace_manager,
)
from ..workspace_manager import WorkspaceManagerBase
from ..storage import AgentRecord, StorageBase
from ...mcp import MCPClient
from ...skill import Skill
from ...workspace import WorkspaceBase

workspace_router = APIRouter(prefix="/workspace", tags=["workspace"])


class ToolInfo(BaseModel):
    """The tool info."""

    name: str
    description: str | None = None


class MCPClientStatus(MCPClient):
    """MCPClient enriched with live tool list and health status."""

    is_healthy: bool = False
    tools: list[ToolInfo] = Field(default_factory=list)


async def _resolve_agent_workspace(
    user_id: str,
    agent_id: str,
    session_id: str,
    storage: StorageBase,
    workspace_manager: WorkspaceManagerBase,
    asset_store: AgentAssetStore,
) -> tuple[AgentRecord, WorkspaceBase]:
    """Resolve the current agent record and synchronized workspace."""
    agent_record = await storage.get_agent(user_id, agent_id)
    if agent_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id!r} not found.",
        )
    session_record = await storage.get_session(user_id, agent_id, session_id)
    if session_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id!r} not found.",
        )
    resolved_mcp_assets = [
        asset.model_copy(update={"dir": asset_store.resolve_dir(asset.dir)})
        for asset in agent_record.data.mcp_assets
    ]
    resolved_skills = [
        skill.model_copy(update={"dir": asset_store.resolve_dir(skill.dir)})
        for skill in agent_record.data.skills
    ]
    workspace = await workspace_manager.get_workspace(
        user_id,
        agent_id,
        session_id,
        session_record.config.workspace_id,
        default_mcps=agent_record.data.mcps,
        mcp_assets=resolved_mcp_assets,
        skill_assets=resolved_skills,
    )
    return agent_record, workspace


# ---------------------------------------------------------------------------
# MCP endpoints
# ---------------------------------------------------------------------------


@workspace_router.get("/mcp")
async def list_mcps(
    agent_id: str = Query(...),
    session_id: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    asset_store: AgentAssetStore = Depends(get_agent_asset_store),
) -> list[MCPClientStatus]:
    """Return all MCP clients with live tool list and health status."""
    _, workspace = await _resolve_agent_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
        asset_store,
    )
    clients = await workspace.list_mcps()

    results = []
    for client in clients:
        base = client.model_dump()
        try:
            mcp_tools = await client.list_tools()
            tools = [
                ToolInfo(name=t.name, description=t.description)
                for t in mcp_tools
            ]
            results.append(
                MCPClientStatus(
                    **base,
                    is_healthy=True,
                    tools=tools,
                ),
            )
        except Exception:
            results.append(
                MCPClientStatus(
                    **base,
                    is_healthy=False,
                ),
            )

    return results


# ---------------------------------------------------------------------------
# Skill endpoints
# ---------------------------------------------------------------------------


@workspace_router.get("/skill")
async def list_skills(
    agent_id: str = Query(...),
    session_id: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    asset_store: AgentAssetStore = Depends(get_agent_asset_store),
) -> list[Skill]:
    """Return all skills available in the session's workspace."""
    _, workspace = await _resolve_agent_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
        asset_store,
    )
    return await workspace.list_skills()

