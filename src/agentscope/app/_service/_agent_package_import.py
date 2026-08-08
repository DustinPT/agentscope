# -*- coding: utf-8 -*-
"""Shared service helpers for agent package import."""
from __future__ import annotations

from datetime import datetime
import os

from fastapi import HTTPException, UploadFile, status

from ...agent import ContextConfig, ReActConfig
from .._models._agent_package import (
    AgentPackageImportResponse,
    AgentPackageImportResult,
)
from ..storage import (
    AgentData,
    AgentMCPAsset,
    AgentRecord,
    AgentSkillAsset,
    StorageBase,
)
from ._agent_asset_store import AgentAssetStore


def _build_package_agent_data(
    *,
    agent_id: str,
    name: str,
    description: str,
    system_prompt: str,
    allowed_subagent_ids: list[str],
    mcp_assets: list[AgentMCPAsset],
    skills: list[AgentSkillAsset],
    existing: AgentRecord | None,
) -> AgentData:
    """Build persisted agent data for one imported package agent."""
    if existing is not None:
        payload: dict = existing.data.model_dump(mode="python")
    else:
        payload = AgentData(
            id=agent_id,
            name=name,
            description=description,
            system_prompt=system_prompt,
            context_config=ContextConfig(),
            react_config=ReActConfig(),
        ).model_dump(mode="python")
    payload.update(
        {
            "id": agent_id,
            "name": name,
            "description": description,
            "system_prompt": system_prompt,
            "allow_subagent_calls": bool(allowed_subagent_ids),
            "allowed_subagent_ids": allowed_subagent_ids,
            "mcps": [],
            "mcp_assets": mcp_assets,
            "skills": skills,
        },
    )
    return AgentData.model_validate(payload)


async def _import_package_agent_assets(
    *,
    asset_store: AgentAssetStore,
    user_id: str,
    agent_id: str,
    skill_dirs: list[str],
    mcp_dirs: list[str],
    existing: AgentRecord | None,
) -> tuple[list[AgentSkillAsset], list[AgentMCPAsset]]:
    """Stage and commit all managed assets for one imported package agent."""
    staged_skills = []
    staged_mcps = []
    try:
        for skill_dir in skill_dirs:
            staged_skills.append(await asset_store.stage_skill_dir(skill_dir))
        for mcp_dir in mcp_dirs:
            staged_mcps.append(await asset_store.stage_mcp_dir(mcp_dir))

        existing_skill_names = (
            {skill.name for skill in existing.data.skills}
            if existing is not None
            else set()
        )
        skill_root_dir = asset_store._agent_skill_root_dir(user_id, agent_id)
        if os.path.isdir(skill_root_dir):
            existing_skill_names.update(
                {
                    entry
                    for entry in os.listdir(skill_root_dir)
                    if os.path.isdir(os.path.join(skill_root_dir, entry))
                },
            )
        existing_mcp_names = (
            {asset.name for asset in existing.data.mcp_assets}
            if existing is not None
            else set()
        )
        mcp_root_dir = asset_store._agent_mcp_dir(user_id, agent_id)
        if os.path.isdir(mcp_root_dir):
            existing_mcp_names.update(
                {
                    entry
                    for entry in os.listdir(mcp_root_dir)
                    if os.path.isdir(os.path.join(mcp_root_dir, entry))
                },
            )
        committed_skills = await asset_store.commit_staged_skills(
            user_id,
            agent_id,
            staged_skills,
            replace_names=existing_skill_names,
        )
        committed_mcps = await asset_store.commit_staged_mcps(
            user_id,
            agent_id,
            staged_mcps,
            replace_names=existing_mcp_names,
        )
        removed_skill_names = sorted(
            existing_skill_names - {skill.name for skill in committed_skills},
        )
        removed_mcp_names = sorted(
            existing_mcp_names - {asset.name for asset in committed_mcps},
        )
        if removed_skill_names:
            await asset_store.delete_skills(user_id, agent_id, removed_skill_names)
        if removed_mcp_names:
            await asset_store.delete_mcps(user_id, agent_id, removed_mcp_names)
        return committed_skills, committed_mcps
    finally:
        if staged_skills:
            await asset_store.cleanup_staged_skills(staged_skills)
        if staged_mcps:
            await asset_store.cleanup_staged_mcps(staged_mcps)


async def import_agent_package_from_upload(
    *,
    package_file: UploadFile,
    user_id: str,
    storage: StorageBase,
    asset_store: AgentAssetStore,
) -> AgentPackageImportResponse:
    """Create or update multiple agents from one uploaded package."""
    staged_package = await asset_store.stage_agent_package_zip(package_file)
    try:
        existing_by_id: dict[str, AgentRecord | None] = {}
        for staged_agent in staged_package.agents:
            existing_by_id[staged_agent.agent_id] = await storage.get_agent(
                user_id,
                staged_agent.agent_id,
            )

        results: list[AgentPackageImportResult] = []
        created_count = 0
        updated_count = 0
        for staged_agent in staged_package.agents:
            existing = existing_by_id[staged_agent.agent_id]
            committed_skills, committed_mcps = await _import_package_agent_assets(
                asset_store=asset_store,
                user_id=user_id,
                agent_id=staged_agent.agent_id,
                skill_dirs=staged_agent.skill_dirs,
                mcp_dirs=staged_agent.mcp_dirs,
                existing=existing,
            )
            data = _build_package_agent_data(
                agent_id=staged_agent.agent_id,
                name=staged_agent.name,
                description=staged_agent.description,
                system_prompt=staged_agent.system_prompt,
                allowed_subagent_ids=staged_agent.allowed_subagent_ids,
                skills=committed_skills,
                mcp_assets=committed_mcps,
                existing=existing,
            )
            record_id = staged_agent.agent_id
            if existing is None:
                record = AgentRecord(
                    id=record_id,
                    user_id=user_id,
                    data=data,
                )
                action = "created"
                created_count += 1
            else:
                if existing.user_id != user_id:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Agent '{record_id}' not found.",
                    )
                record = existing.model_copy(
                    update={"data": data, "updated_at": datetime.now()},
                )
                action = "updated"
                updated_count += 1
            await storage.upsert_agent(user_id, record)
            results.append(
                AgentPackageImportResult(
                    agent_id=record.id,
                    action=action,
                    agent=record,
                ),
            )

        return AgentPackageImportResponse(
            main_agent_id=staged_package.main_agent_id,
            results=results,
            created_count=created_count,
            updated_count=updated_count,
        )
    finally:
        await asset_store.cleanup_staged_agent_package(staged_package)
