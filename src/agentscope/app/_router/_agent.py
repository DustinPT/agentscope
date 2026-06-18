# -*- coding: utf-8 -*-
"""Agent router — CRUD endpoints for agent configurations."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status

from ...agent import ContextConfig, ReActConfig
from ..deps import get_current_user_id, get_session_service, get_storage
from ._schema import (
    AgentSchemaResponse,
    ListAgentsResponse,
    CreateAgentRequest,
    CreateAgentResponse,
    UpdateAgentRequest,
)
from .._service import SessionService
from ..storage import StorageBase, AgentData, AgentRecord


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

agent_router = APIRouter(
    prefix="/agent",
    tags=["agent"],
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
    await _validate_subagent_config(
        storage,
        user_id,
        self_agent_id=None,
        allow_subagent_calls=body.allow_subagent_calls,
        allowed_subagent_ids=body.allowed_subagent_ids,
        default_credential_id=(
            body.default_chat_model_config.credential_id
            if body.default_chat_model_config is not None
            else None
        ),
    )

    record = AgentRecord(
        user_id=user_id,
        data=AgentData(
            name=body.name,
            description=body.description,
            system_prompt=body.system_prompt,
            context_config=body.context_config,
            react_config=body.react_config,
            default_chat_model_config=body.default_chat_model_config,
            allow_subagent_calls=body.allow_subagent_calls,
            allowed_subagent_ids=body.allowed_subagent_ids,
        ),
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
    agents = await storage.list_agents(user_id)
    existing = next((a for a in agents if a.id == agent_id), None)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found.",
        )

    updates = body.model_dump(exclude_none=True)
    merged_data = existing.data.model_dump(mode="python")
    merged_data.update(updates)
    updated_data = AgentData.model_validate(merged_data)
    await _validate_subagent_config(
        storage,
        user_id,
        self_agent_id=agent_id,
        allow_subagent_calls=updated_data.allow_subagent_calls,
        allowed_subagent_ids=updated_data.allowed_subagent_ids,
        default_credential_id=(
            updated_data.default_chat_model_config.credential_id
            if updated_data.default_chat_model_config is not None
            else None
        ),
    )
    updated_agent = existing.model_copy(
        update={"data": updated_data, "updated_at": datetime.now()},
    )
    await storage.upsert_agent(user_id, updated_agent)
    return updated_agent


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
