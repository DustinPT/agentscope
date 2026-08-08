# -*- coding: utf-8 -*-
"""Agent router — CRUD endpoints for agent configurations."""
from datetime import datetime
import json
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse

from ...agent import ContextConfig, ReActConfig
from ..deps import (
    get_agent_asset_store,
    get_current_user_id,
    get_session_service,
    get_storage,
)
from ._schema import (
    AgentSchemaResponse,
    AgentComposeConfig,
    AgentComposeResponse,
    AgentComposeUpdateConfig,
    AgentPackageImportResponse,
    ListAgentsResponse,
    CreateAgentRequest,
    CreateAgentResponse,
    UpdateAgentRequest,
)
from .._service import AgentAssetStore
from .._service import SessionService
from .._service._agent_package_import import (
    import_agent_package_from_upload as service_import_agent_package_from_upload,
)
from ..storage import (
    AgentData,
    AgentMCPAsset,
    AgentRecord,
    AgentSkillAsset,
    StorageBase,
)


async def _ensure_credential_exists(
    storage: StorageBase,
    user_id: str,
    credential_id: str | None,
) -> None:
    """Validate that a referenced credential belongs to the user."""
    if credential_id is None:
        return
    credentials = await storage.list_credentials(user_id)
    if not any(c.id == credential_id for c in credentials):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Credential '{credential_id}' not found.",
        )


async def _validate_subagent_config(
    storage: StorageBase,
    user_id: str,
    *,
    self_agent_id: str | None,
    allow_subagent_calls: bool,
    allowed_subagent_ids: list[str],
    default_credential_id: str | None,
) -> None:
    """Validate sub-agent configuration against storage."""
    await _ensure_credential_exists(storage, user_id, default_credential_id)

    if self_agent_id is not None and self_agent_id in allowed_subagent_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An agent cannot allow itself as a sub-agent target.",
        )

    for target_agent_id in allowed_subagent_ids:
        target = await storage.get_agent(user_id, target_agent_id)
        if target is None or target.user_id != user_id or target.source != "user":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Allowed sub-agent '{target_agent_id}' not found.",
            )

    if not allow_subagent_calls and allowed_subagent_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "allowed_subagent_ids must be empty when "
                "allow_subagent_calls is false."
            ),
        )

async def _get_agent_or_404(
    storage: StorageBase,
    user_id: str,
    agent_id: str,
) -> AgentRecord:
    """Load an agent owned by the user or raise 404."""
    agent = await storage.get_agent(user_id, agent_id)
    if agent is None or agent.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found.",
        )
    return agent


def _build_agent_data(
    body: CreateAgentRequest,
    *,
    mcp_assets: list[AgentMCPAsset] | None = None,
    skills: list[AgentSkillAsset] | None = None,
    existing_id: str | None = None,
) -> AgentData:
    """Convert request payload into persisted agent data."""
    payload = body.model_dump(mode="python")
    if existing_id is not None:
        payload["id"] = existing_id
    payload["mcp_assets"] = mcp_assets or []
    payload["skills"] = skills or []
    return AgentData.model_validate(payload)


async def _validate_agent_body(
    storage: StorageBase,
    user_id: str,
    *,
    self_agent_id: str | None,
    body: CreateAgentRequest,
) -> None:
    """Run shared validation for create/compose request bodies."""
    await _validate_subagent_config(
        storage,
        user_id,
        self_agent_id=self_agent_id,
        allow_subagent_calls=body.allow_subagent_calls,
        allowed_subagent_ids=body.allowed_subagent_ids,
        default_credential_id=(
            body.default_chat_model_config.credential_id
            if body.default_chat_model_config is not None
            else None
        ),
    )


def _ensure_unique_skill_names(skills: list[AgentSkillAsset]) -> None:
    """Reject duplicated skill names within one agent config."""
    names = [skill.name for skill in skills]
    duplicates = {name for name in names if names.count(name) > 1}
    if duplicates:
        dup = sorted(duplicates)[0]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Duplicate skill name '{dup}'.",
        )


def _ensure_unique_mcp_names(
    mcps: list,
    mcp_assets: list[AgentMCPAsset],
) -> None:
    """Reject duplicated MCP names across JSON and asset-based MCPs."""
    names = [mcp.name for mcp in mcps] + [asset.name for asset in mcp_assets]
    duplicates = {name for name in names if names.count(name) > 1}
    if duplicates:
        dup = sorted(duplicates)[0]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Duplicate MCP name '{dup}'.",
        )


def _parse_compose_model(
    raw_config: str,
    model_cls: type[AgentComposeConfig] | type[AgentComposeUpdateConfig],
) -> AgentComposeConfig | AgentComposeUpdateConfig:
    """Parse the JSON config field from compose multipart requests."""
    try:
        payload = json.loads(raw_config)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid compose config JSON: {exc}",
        ) from exc
    return model_cls.model_validate(payload)


agent_router = APIRouter(
    tags=["agent"],
    prefix="/agent",
    responses={404: {"description": "Not found"}},
)


@agent_router.get(
    "/schema",
    response_model=AgentSchemaResponse,
    summary="Get JSON Schema fragments for the agent form",
)
async def get_agent_schema() -> AgentSchemaResponse:
    """Return the JSON Schema fragments used by the frontend to render
    the agent create / edit forms.

    The frontend uses three sections — identity, context config, and
    react config — so we return them as separate self-contained schemas
    rather than a single ``AgentData`` schema with ``$ref``s.

    Returns:
        `AgentSchemaResponse`:
            Schemas for the three form sections.
    """
    # Slice ``AgentData``'s schema down to the identity-relevant fields.
    # Going through ``AgentData.model_json_schema()`` (rather than building
    # a dict by hand) keeps Pydantic as the single source of truth for
    # defaults, titles, descriptions, and the ``format: textarea`` hint.
    agent_schema = AgentData.model_json_schema()
    identity_keys = ("name", "description", "system_prompt")
    identity = {
        "type": "object",
        "title": "Identity",
        "properties": {
            k: v
            for k, v in agent_schema.get("properties", {}).items()
            if k in identity_keys
        },
        "required": [
            r for r in agent_schema.get("required", []) if r in identity_keys
        ],
    }

    context_schema = ContextConfig.model_json_schema()
    # ``summary_schema`` holds a Pydantic JSON Schema describing how the
    # compression model should structure its output. The end-user is not
    # expected to edit it from the form, so we hide it.
    context_schema.get("properties", {}).pop("summary_schema", None)

    subagent_keys = (
        "default_chat_model_config",
        "allow_subagent_calls",
        "allowed_subagent_ids",
    )
    subagent_config = {
        "type": "object",
        "title": "Sub-Agent Config",
        "properties": {
            "default_chat_model_config": {
                "type": "object",
                "title": "Agent Default Model",
                "description": (
                    "Default model for this agent. Used for new sessions "
                    "when the caller does not provide a model, and for "
                    "sub-agent runs. Leave empty to inherit the caller "
                    "session's model during sub-agent runs."
                ),
                "properties": {
                    "type": {
                        "type": "string",
                        "title": "Provider Type",
                    },
                    "credential_id": {
                        "type": "string",
                        "title": "Credential Id",
                    },
                    "model": {
                        "type": "string",
                        "title": "Model",
                    },
                },
            },
            "allow_subagent_calls": (
                agent_schema.get("properties", {})
                .get("allow_subagent_calls", {})
            ),
            "allowed_subagent_ids": {
                "type": "array",
                "title": "Allowed Sub-Agents",
                "description": (
                    "Managed agent ids this agent may call as sub-agents."
                ),
                "items": {"type": "string"},
            },
        },
        "required": [
            r for r in agent_schema.get("required", []) if r in subagent_keys
        ],
    }

    return AgentSchemaResponse(
        identity=identity,
        context_config=context_schema,
        react_config=ReActConfig.model_json_schema(),
        subagent_config=subagent_config,
    )


@agent_router.get(
    "/",
    response_model=ListAgentsResponse,
    summary="List all agents",
)
async def list_agents(
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> ListAgentsResponse:
    """Return all agent records belonging to the authenticated user.

    Args:
        user_id (`str`):
            Injected authenticated user ID.
        storage (`StorageBase`):
            Injected storage backend.

    Returns:
        `ListAgentsResponse`:
            All agent records and their total count.
    """
    agents = await storage.list_agents(user_id)
    return ListAgentsResponse(agents=agents, total=len(agents))


@agent_router.post(
    "/",
    response_model=CreateAgentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new agent",
)
async def create_agent(
    body: CreateAgentRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> CreateAgentResponse:
    """Create and persist a new agent configuration.

    Args:
        body (`CreateAgentRequest`):
            Agent configuration to store.
        user_id (`str`):
            Injected authenticated user ID.
        storage (`StorageBase`):
            Injected storage backend.

    Returns:
        `CreateAgentResponse`:
            The server-assigned agent identifier.
    """
    await _validate_agent_body(
        storage,
        user_id,
        self_agent_id=None,
        body=body,
    )

    record = AgentRecord(
        user_id=user_id,
        data=_build_agent_data(body),
    )
    agent_id = await storage.upsert_agent(user_id, record)
    return CreateAgentResponse(agent_id=agent_id)


@agent_router.patch(
    "/{agent_id}",
    response_model=AgentRecord,
    summary="Update an agent",
)
async def update_agent(
    agent_id: str,
    body: UpdateAgentRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> AgentRecord:
    """Partially update an existing agent configuration.

    Only the fields present in the request body are updated; all other fields
    keep their current values.

    Args:
        agent_id (`str`): The agent to update.
        body (`UpdateAgentRequest`): Fields to update.
        user_id (`str`): Injected authenticated user ID.
        storage (`StorageBase`): Injected storage backend.

    Returns:
        `AgentRecord`: The full agent record after the update.

    Raises:
        `HTTPException`: 404 if the agent does not exist or does not belong
            to the authenticated user.
    """
    existing = await _get_agent_or_404(storage, user_id, agent_id)

    updates = body.model_dump(exclude_none=True)
    merged_data = existing.data.model_dump(mode="python")
    merged_data.update(updates)
    updated_data = AgentData.model_validate(merged_data)
    await _validate_agent_body(
        storage,
        user_id,
        self_agent_id=agent_id,
        body=CreateAgentRequest.model_validate(updated_data.model_dump(mode="python")),
    )
    updated_agent = existing.model_copy(
        update={"data": updated_data, "updated_at": datetime.now()},
    )
    await storage.upsert_agent(user_id, updated_agent)
    return updated_agent


@agent_router.post(
    "/compose",
    response_model=AgentComposeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an agent with MCP and skill assets",
)
async def compose_create_agent(
    config: Annotated[str, Form(...)],
    mcp_files: Annotated[list[UploadFile], File()] = [],
    skill_files: Annotated[list[UploadFile], File()] = [],
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    asset_store: AgentAssetStore = Depends(get_agent_asset_store),
) -> AgentComposeResponse:
    """Create an agent atomically from multipart config and skill ZIPs."""
    body = _parse_compose_model(config, AgentComposeConfig)
    await _validate_agent_body(
        storage,
        user_id,
        self_agent_id=None,
        body=body,
    )

    staged_skills: list = []
    staged_mcps: list = []
    try:
        staged_skills = [
            await asset_store.stage_skill_zip(file) for file in skill_files
        ]
        staged_mcps = [await asset_store.stage_mcp_zip(file) for file in mcp_files]
        if staged_skills:
            _ensure_unique_skill_names(
                [
                    AgentSkillAsset(
                        name=item.name,
                        description=item.description,
                        archive_name=item.archive_name,
                        dir="",
                        content_hash=item.content_hash,
                    )
                    for item in staged_skills
                ],
            )
        staged_mcp_assets = [
            AgentMCPAsset(
                name=item.name,
                archive_name=item.archive_name,
                dir="",
                content_hash=item.content_hash,
                client=item.client,
            )
            for item in staged_mcps
        ]
        _ensure_unique_mcp_names(body.mcps, staged_mcp_assets)
        record = AgentRecord(
            user_id=user_id,
            data=_build_agent_data(body),
        )
        committed_mcps = await asset_store.commit_staged_mcps(
            user_id,
            record.id,
            staged_mcps,
        )
        committed_skills = await asset_store.commit_staged_skills(
            user_id,
            record.id,
            staged_skills,
        )
        record.data.mcp_assets = committed_mcps
        record.data.skills = committed_skills
        await storage.upsert_agent(user_id, record)
        return AgentComposeResponse(agent=record)
    finally:
        if staged_mcps:
            await asset_store.cleanup_staged_mcps(staged_mcps)
        if staged_skills:
            await asset_store.cleanup_staged_skills(staged_skills)


@agent_router.put(
    "/{agent_id}/compose",
    response_model=AgentComposeResponse,
    summary="Update an agent with MCP and skill assets",
)
async def compose_update_agent(
    agent_id: str,
    config: Annotated[str, Form(...)],
    mcp_files: Annotated[list[UploadFile], File()] = [],
    skill_files: Annotated[list[UploadFile], File()] = [],
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    asset_store: AgentAssetStore = Depends(get_agent_asset_store),
) -> AgentComposeResponse:
    """Update agent config atomically from multipart config and skill ZIPs."""
    existing = await _get_agent_or_404(storage, user_id, agent_id)
    body = _parse_compose_model(config, AgentComposeUpdateConfig)
    await _validate_agent_body(
        storage,
        user_id,
        self_agent_id=agent_id,
        body=CreateAgentRequest.model_validate(body.model_dump(mode="python")),
    )

    existing_skill_map = {skill.name: skill for skill in existing.data.skills}
    existing_mcp_asset_map = {
        mcp_asset.name: mcp_asset for mcp_asset in existing.data.mcp_assets
    }
    unknown_retained = sorted(
        set(body.retained_skill_names) - set(existing_skill_map),
    )
    if unknown_retained:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown retained skill '{unknown_retained[0]}'.",
        )
    unknown_retained_mcp_assets = sorted(
        set(body.retained_mcp_asset_names) - set(existing_mcp_asset_map),
    )
    if unknown_retained_mcp_assets:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown retained MCP '{unknown_retained_mcp_assets[0]}'.",
        )

    staged_skills: list = []
    staged_mcps: list = []
    try:
        staged_skills = [
            await asset_store.stage_skill_zip(file) for file in skill_files
        ]
        staged_mcps = [await asset_store.stage_mcp_zip(file) for file in mcp_files]

        staged_skill_names = [item.name for item in staged_skills]
        if len(staged_skill_names) != len(set(staged_skill_names)):
            dup = next(
                name
                for name in staged_skill_names
                if staged_skill_names.count(name) > 1
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Duplicate skill name '{dup}'.",
            )
        staged_mcp_names = [item.name for item in staged_mcps]
        if len(staged_mcp_names) != len(set(staged_mcp_names)):
            dup = next(
                name
                for name in staged_mcp_names
                if staged_mcp_names.count(name) > 1
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Duplicate MCP name '{dup}'.",
            )

        skill_conflict = set(staged_skill_names).intersection(body.retained_skill_names)
        if skill_conflict:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Skill '{sorted(skill_conflict)[0]}' already exists.",
            )
        json_mcp_conflict = set(staged_mcp_names).intersection(
            {mcp.name for mcp in body.mcps},
        )
        if json_mcp_conflict:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"MCP '{sorted(json_mcp_conflict)[0]}' already exists.",
            )

        retained_skills = [
            existing_skill_map[name] for name in body.retained_skill_names
        ]
        retained_mcp_assets = [
            existing_mcp_asset_map[name] for name in body.retained_mcp_asset_names
        ]
        replace_names = set(staged_mcp_names).intersection(existing_mcp_asset_map)
        retained_mcp_assets = [
            asset
            for asset in retained_mcp_assets
            if asset.name not in replace_names
        ]

        committed_mcps = await asset_store.commit_staged_mcps(
            user_id,
            agent_id,
            staged_mcps,
            replace_names=replace_names,
        )
        committed_skills = await asset_store.commit_staged_skills(
            user_id,
            agent_id,
            staged_skills,
        )
        final_mcp_assets = retained_mcp_assets + committed_mcps
        final_skills = retained_skills + committed_skills
        _ensure_unique_mcp_names(body.mcps, final_mcp_assets)
        _ensure_unique_skill_names(final_skills)

        updated_data = _build_agent_data(
            CreateAgentRequest.model_validate(body.model_dump(mode="python")),
            mcp_assets=final_mcp_assets,
            skills=final_skills,
            existing_id=existing.data.id,
        )
        updated_agent = existing.model_copy(
            update={"data": updated_data, "updated_at": datetime.now()},
        )
        await storage.upsert_agent(user_id, updated_agent)

        removed_mcp_names = [
            mcp_asset.name
            for mcp_asset in existing.data.mcp_assets
            if (
                mcp_asset.name not in body.retained_mcp_asset_names
                and mcp_asset.name not in staged_mcp_names
            )
        ]
        if removed_mcp_names:
            await asset_store.delete_mcps(user_id, agent_id, removed_mcp_names)
        removed_skill_names = [
            skill.name
            for skill in existing.data.skills
            if skill.name not in body.retained_skill_names
        ]
        if removed_skill_names:
            await asset_store.delete_skills(user_id, agent_id, removed_skill_names)
        return AgentComposeResponse(agent=updated_agent)
    finally:
        if staged_mcps:
            await asset_store.cleanup_staged_mcps(staged_mcps)
        if staged_skills:
            await asset_store.cleanup_staged_skills(staged_skills)


@agent_router.post(
    "/package/import",
    response_model=AgentPackageImportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Import an agent package ZIP",
)
async def import_agent_package(
    package_file: Annotated[UploadFile, File(...)],
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    asset_store: AgentAssetStore = Depends(get_agent_asset_store),
) -> AgentPackageImportResponse:
    """Create or update multiple agents from one uploaded package."""
    return await service_import_agent_package_from_upload(
        package_file=package_file,
        user_id=user_id,
        storage=storage,
        asset_store=asset_store,
    )


@agent_router.get(
    "/{agent_id}/skills/{skill_name}/download",
    summary="Download a managed skill ZIP",
)
async def download_skill(
    agent_id: str,
    skill_name: str,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    asset_store: AgentAssetStore = Depends(get_agent_asset_store),
) -> StreamingResponse:
    """Download a committed skill as a ZIP archive."""
    await _get_agent_or_404(storage, user_id, agent_id)
    filename, payload = await asset_store.zip_skill(user_id, agent_id, skill_name)
    return StreamingResponse(
        iter([payload]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@agent_router.delete(
    "/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an agent",
)
async def delete_agent(
    agent_id: str,
    user_id: str = Depends(get_current_user_id),
    session_service: SessionService = Depends(get_session_service),
) -> None:
    """Permanently delete an agent configuration.

    Cascades through every session owned by this agent (and, for team
    leaders, through every worker session) — cancelling any in-flight
    chat run, removing storage records, and purging bus state.

    Args:
        agent_id (`str`): The agent to delete.
        user_id (`str`): Injected authenticated user ID.
        session_service (`SessionService`): Injected session service.

    Raises:
        `HTTPException`: 404 if the agent does not exist or does not belong
            to the authenticated user.
    """
    deleted = await session_service.delete_agent(user_id, agent_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found.",
        )
