# -*- coding: utf-8 -*-
"""Request / response schemas for the agent router."""
from pydantic import BaseModel, Field

from ....agent import ContextConfig, ReActConfig
from ...storage import AgentRecord, ChatModelConfig


class CreateAgentRequest(BaseModel):
    """Request body for creating a new agent."""

    name: str = Field(description="Display name of the agent.")
    description: str = Field(
        default="",
        description=(
            "Capability summary describing what the agent is good at and "
            "when it should be called."
        ),
    )
    system_prompt: str = Field(
        default="You're a helpful assistant.",
        description="Base system prompt fed to the agent.",
    )
    context_config: ContextConfig = Field(
        default_factory=ContextConfig,
        description="Context-window management configuration.",
    )
    react_config: ReActConfig = Field(
        default_factory=ReActConfig,
        description="ReAct loop configuration.",
    )
    default_chat_model_config: ChatModelConfig | None = Field(
        default=None,
        description="Preferred model when this agent runs as a sub-agent.",
    )
    allow_subagent_calls: bool = Field(
        default=False,
        description="Whether this agent may call other managed agents.",
    )
    allowed_subagent_ids: list[str] = Field(
        default_factory=list,
        description="Managed agents this agent may call as sub-agents.",
    )


class CreateAgentResponse(BaseModel):
    """Response body after creating an agent."""

    agent_id: str = Field(description="Server-assigned agent identifier.")


class UpdateAgentRequest(BaseModel):
    """Request body for partially updating an agent.

    Omit any field to keep its current value.
    """

    name: str | None = Field(default=None, description="New display name.")
    description: str | None = Field(
        default=None,
        description=(
            "Updated capability summary describing what the agent is good at "
            "and when it should be called."
        ),
    )
    system_prompt: str | None = Field(
        default=None,
        description="New system prompt.",
    )
    context_config: ContextConfig | None = Field(
        default=None,
        description="New context configuration.",
    )
    react_config: ReActConfig | None = Field(
        default=None,
        description="New ReAct loop configuration.",
    )
    default_chat_model_config: ChatModelConfig | None = Field(
        default=None,
        description="Preferred model when this agent runs as a sub-agent.",
    )
    allow_subagent_calls: bool | None = Field(
        default=None,
        description="Whether this agent may call other managed agents.",
    )
    allowed_subagent_ids: list[str] | None = Field(
        default=None,
        description="Managed agents this agent may call as sub-agents.",
    )


class ListAgentsResponse(BaseModel):
    """Response body for listing agents."""

    agents: list[AgentRecord] = Field(description="Agent records.")
    total: int = Field(description="Total number of agents.")


class AgentSchemaResponse(BaseModel):
    """JSON Schema fragments used by the frontend to render the agent
    create / edit forms.

    Each fragment is a self-contained JSON Schema object so the frontend
    doesn't need to follow ``$ref`` links across fragments. The frontend
    pairs each property with an i18n key derived from its path, so labels
    and descriptions remain localizable independently of the backend.
    """

    identity: dict = Field(
        description=(
            "Schema for the agent's identity fields (``name``, "
            "``system_prompt``)."
        ),
    )
    context_config: dict = Field(
        description="Schema for ``ContextConfig``.",
    )
    react_config: dict = Field(
        description="Schema for ``ReActConfig``.",
    )
    subagent_config: dict = Field(
        description="Schema for sub-agent execution and access control.",
    )
