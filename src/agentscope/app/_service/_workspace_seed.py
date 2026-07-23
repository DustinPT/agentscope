# -*- coding: utf-8 -*-
"""Synchronize workspace runtime state with persisted agent configuration."""

from ...mcp import MCPClient
from ...workspace import WorkspaceBase
from ..storage import AgentMCPAsset, AgentSkillAsset


def _replace_mcp_variables(value: str, mapping: dict[str, str]) -> str:
    """Replace ``{{VAR}}`` placeholders inside one string."""
    result = value
    for key, replacement in mapping.items():
        result = result.replace(f"{{{{{key}}}}}", replacement)
    return result


def _resolve_asset_client(
    asset: AgentMCPAsset,
    workspace_dir: str,
    mcp_dir: str,
) -> MCPClient:
    """Build a runtime MCP client with workspace variables expanded."""
    mapping = {"WORKSPACE_DIR": workspace_dir, "MCP_DIR": mcp_dir}
    payload = asset.client.model_dump(mode="json")
    config = payload["mcp_config"]
    if config["type"] == "stdio_mcp":
        config["command"] = _replace_mcp_variables(config["command"], mapping)
        config["args"] = [
            _replace_mcp_variables(arg, mapping) for arg in (config.get("args") or [])
        ] or None
        config["env"] = {
            key: _replace_mcp_variables(value, mapping)
            for key, value in (config.get("env") or {}).items()
        } or None
        if config.get("cwd") is not None:
            config["cwd"] = _replace_mcp_variables(str(config["cwd"]), mapping)
    return MCPClient.model_validate(payload)


async def sync_workspace_mcp_assets(
    workspace: WorkspaceBase,
    expected_mcp_assets: list[AgentMCPAsset],
) -> tuple[list[MCPClient], set[str]]:
    """Synchronize MCP asset directories and return runtime MCP clients."""
    expected_names = {asset.name for asset in expected_mcp_assets}
    changed_names: set[str] = set()

    for asset in expected_mcp_assets:
        _, changed = await workspace.sync_mcp_asset(
            asset.name,
            asset.dir,
            asset.content_hash,
        )
        if changed:
            changed_names.add(asset.name)

    current_asset_names = await workspace.list_mcp_asset_names()
    for name in current_asset_names:
        if name not in expected_names:
            await workspace.remove_mcp_asset(name)

    runtime_mcps: list[MCPClient] = []
    for asset in expected_mcp_assets:
        runtime_dir = f"{workspace.workdir}/mcps/{asset.name}"
        runtime_mcps.append(
            _resolve_asset_client(
                asset,
                workspace.workdir,
                runtime_dir,
            ),
        )
    return runtime_mcps, changed_names


async def sync_workspace_mcps(
    workspace: WorkspaceBase,
    expected_mcps: list[MCPClient],
    force_reconnect_names: set[str] | None = None,
) -> None:
    """Synchronize workspace MCPs to the expected agent configuration."""
    current_mcps = await workspace.list_mcps()
    expected_map = {mcp.name: mcp for mcp in expected_mcps}
    current_by_name: dict[str, list[MCPClient]] = {}
    force_reconnect_names = force_reconnect_names or set()

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
        if (
            name in force_reconnect_names
            or current.model_dump(mode="json") != expected.model_dump(mode="json")
        ):
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
    expected_mcp_assets: list[AgentMCPAsset],
    expected_skills: list[AgentSkillAsset],
) -> None:
    """Synchronize both MCPs and skills for one workspace instance."""
    asset_mcps, changed_asset_names = await sync_workspace_mcp_assets(
        workspace,
        expected_mcp_assets,
    )
    await sync_workspace_mcps(
        workspace,
        [*expected_mcps, *asset_mcps],
        force_reconnect_names=changed_asset_names,
    )
    await sync_workspace_skills(workspace, expected_skills)
