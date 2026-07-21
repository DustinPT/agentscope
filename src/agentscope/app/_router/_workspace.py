# -*- coding: utf-8 -*-
"""Workspace router — manage MCP clients and skills on a workspace."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from .._service._agent_asset_store import AgentAssetStore
from .._service._workspace_seed import sync_workspace_state
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


class AddSkillRequest(BaseModel):
    """The request to add skill."""

    skill_path: str


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
    workspace = await workspace_manager.get_workspace(
        user_id,
        agent_id,
        session_id,
        session_record.config.workspace_id,
        default_mcps=agent_record.data.mcps,
        skill_assets=agent_record.data.skills,
    )
    return agent_record, workspace


async def _persist_agent(
    storage: StorageBase,
    user_id: str,
    agent: AgentRecord,
) -> AgentRecord:
    """Persist the updated agent record and return it."""
    updated = agent.model_copy(update={"updated_at": datetime.now()})
    await storage.upsert_agent(user_id, updated)
    return updated


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
) -> list[MCPClientStatus]:
    """Return all MCP clients with live tool list and health status."""
    _, workspace = await _resolve_agent_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
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


@workspace_router.post("/mcp", status_code=status.HTTP_201_CREATED)
async def add_mcp(
    mcp: MCPClient,
    agent_id: str = Query(...),
    session_id: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
) -> None:
    """Add an MCP client to the agent config and sync this workspace."""
    agent, workspace = await _resolve_agent_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )
    if any(existing.name == mcp.name for existing in agent.data.mcps):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'MCP server "{mcp.name}" already exists.',
        )
    updated_agent = agent.model_copy(
        update={
            "data": agent.data.model_copy(
                update={"mcps": [*agent.data.mcps, mcp]},
            ),
        },
    )
    updated_agent = await _persist_agent(storage, user_id, updated_agent)
    await sync_workspace_state(
        workspace,
        expected_mcps=updated_agent.data.mcps,
        expected_skills=updated_agent.data.skills,
    )


@workspace_router.delete(
    "/mcp/{mcp_name}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_mcp(
    mcp_name: str,
    agent_id: str = Query(...),
    session_id: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
) -> None:
    """Remove an MCP client from the agent config and sync this workspace."""
    agent, workspace = await _resolve_agent_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )
    remaining = [mcp for mcp in agent.data.mcps if mcp.name != mcp_name]
    updated_agent = agent.model_copy(
        update={"data": agent.data.model_copy(update={"mcps": remaining})},
    )
    updated_agent = await _persist_agent(storage, user_id, updated_agent)
    await sync_workspace_state(
        workspace,
        expected_mcps=updated_agent.data.mcps,
        expected_skills=updated_agent.data.skills,
    )


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
) -> list[Skill]:
    """Return all skills available in the session's workspace."""
    _, workspace = await _resolve_agent_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )
    return await workspace.list_skills()


@workspace_router.post("/skill", status_code=status.HTTP_201_CREATED)
async def add_skill(
    body: AddSkillRequest,
    agent_id: str = Query(...),
    session_id: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    asset_store: AgentAssetStore = Depends(get_agent_asset_store),
) -> None:
    """Import a skill directory into agent config and sync this workspace."""
    agent, workspace = await _resolve_agent_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )
    asset = await asset_store.import_skill_dir(user_id, agent_id, body.skill_path)
    if any(skill.name == asset.name for skill in agent.data.skills):
        await asset_store.delete_skills(user_id, agent_id, [asset.name])
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Skill '{asset.name}' already exists.",
        )
    updated_agent = agent.model_copy(
        update={
            "data": agent.data.model_copy(
                update={"skills": [*agent.data.skills, asset]},
            ),
        },
    )
    updated_agent = await _persist_agent(storage, user_id, updated_agent)
    await sync_workspace_state(
        workspace,
        expected_mcps=updated_agent.data.mcps,
        expected_skills=updated_agent.data.skills,
    )


@workspace_router.delete(
    "/skill/{skill_name}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_skill(
    skill_name: str,
    agent_id: str = Query(...),
    session_id: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
    asset_store: AgentAssetStore = Depends(get_agent_asset_store),
) -> None:
    """Remove a skill from agent config and sync this workspace."""
    agent, workspace = await _resolve_agent_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )
    remaining = [skill for skill in agent.data.skills if skill.name != skill_name]
    updated_agent = agent.model_copy(
        update={"data": agent.data.model_copy(update={"skills": remaining})},
    )
    updated_agent = await _persist_agent(storage, user_id, updated_agent)
    await asset_store.delete_skills(user_id, agent_id, [skill_name])
    await sync_workspace_state(
        workspace,
        expected_mcps=updated_agent.data.mcps,
        expected_skills=updated_agent.data.skills,
    )
