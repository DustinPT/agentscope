# -*- coding: utf-8 -*-
"""Synchronize workspace runtime state with persisted agent configuration."""

from ...mcp import MCPClient
from ...workspace import WorkspaceBase
from ..storage import AgentSkillAsset


async def sync_workspace_mcps(
    workspace: WorkspaceBase,
    expected_mcps: list[MCPClient],
) -> None:
    """Synchronize workspace MCPs to the expected agent configuration."""
    current_mcps = await workspace.list_mcps()
    expected_map = {mcp.name: mcp for mcp in expected_mcps}
    current_by_name: dict[str, list[MCPClient]] = {}

    for mcp in current_mcps:
        current_by_name.setdefault(mcp.name, []).append(mcp)

    for name, current_group in current_by_name.items():
        expected = expected_map.get(name)
        if expected is None:
            for _ in current_group:
                await workspace.remove_mcp(name)
            continue

        if len(current_group) > 1:
            for _ in range(len(current_group)):
                await workspace.remove_mcp(name)
            await workspace.add_mcp(expected)
            continue

        current = current_group[0]
        if current.model_dump(mode="json") != expected.model_dump(mode="json"):
            await workspace.remove_mcp(name)
            await workspace.add_mcp(expected)

    for name, expected in expected_map.items():
        current = current_by_name.get(name)
        if current is None:
            await workspace.add_mcp(expected)


async def sync_workspace_skills(
    workspace: WorkspaceBase,
    expected_skills: list[AgentSkillAsset],
) -> None:
    """Synchronize workspace skills to the expected agent configuration."""
    current_skills = await workspace.list_skills()
    expected_map = {skill.name: skill for skill in expected_skills}
    current_by_name: dict[str, list] = {}

    for skill in current_skills:
        current_by_name.setdefault(skill.name, []).append(skill)

    for name, current_group in current_by_name.items():
        expected = expected_map.get(name)
        if expected is None:
            for _ in current_group:
                await workspace.remove_skill(name)
            continue

        if len(current_group) > 1:
            for _ in range(len(current_group)):
                await workspace.remove_skill(name)
            await workspace.add_skill(expected.dir)
            continue

        current = current_group[0]
        if current.content_hash != expected.content_hash:
            await workspace.remove_skill(name)
            await workspace.add_skill(expected.dir)

    for name, expected in expected_map.items():
        current = current_by_name.get(name)
        if current is None:
            await workspace.add_skill(expected.dir)


async def sync_workspace_state(
    workspace: WorkspaceBase,
    *,
    expected_mcps: list[MCPClient],
    expected_skills: list[AgentSkillAsset],
) -> None:
    """Synchronize both MCPs and skills for one workspace instance."""
    await sync_workspace_mcps(workspace, expected_mcps)
    await sync_workspace_skills(workspace, expected_skills)
