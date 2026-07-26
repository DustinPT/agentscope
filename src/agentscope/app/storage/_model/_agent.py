# -*- coding: utf-8 -*-
"""The agent storage class."""
import uuid
from typing import Literal

from pydantic import Field, BaseModel

from ._base import _RecordBase
from ._session import ChatModelConfig
from ....agent import ContextConfig, ReActConfig
from ....mcp import MCPClient


class AgentSkillAsset(BaseModel):
    """Persisted metadata for a managed agent skill asset."""

    name: str = Field(description="Stable skill name used as the business key.")
    description: str = Field(description="Skill description parsed from SKILL.md.")
    archive_name: str = Field(description="Original uploaded ZIP filename.")
    dir: str = Field(
        description="Skill directory stored relative to the managed asset root.",
    )
    content_hash: str = Field(
        description="Content hash computed from the skill's SKILL.md file.",
    )


class AgentMCPAsset(BaseModel):
    """Persisted metadata for a managed agent MCP asset."""

    name: str = Field(description="Stable MCP name used as the business key.")
    archive_name: str = Field(description="Original uploaded ZIP filename.")
    dir: str = Field(
        description="MCP directory stored relative to the managed asset root.",
    )
    content_hash: str = Field(
        description="Content hash computed from the MCP package metadata.",
    )
    client: MCPClient = Field(
        description="Normalized MCP client configuration parsed from mcp.json.",
    )


class AgentData(BaseModel):
    """The agent data model."""

    id: str = Field(
        description="Unique agent id",
        default_factory=lambda: uuid.uuid4().hex,
    )
    """The agent id."""

    name: str = Field(
        description="The name of the agent.",
        title="Name",
    )

    description: str = Field(
        default="",
        description=(
            "A short capability summary describing what the agent is good at "
            "and when it should be called."
        ),
        title="Description",
        json_schema_extra={"format": "textarea"},
    )

    system_prompt: str = Field(
        default="You're a helpful assistant.",
        description="The system prompt for the agent.",
        title="System Prompt",
        # Hint for schema-driven UI renderers; see ``ContextConfig`` for
        # the same pattern on long-form prompts.
        json_schema_extra={"format": "textarea"},
    )

    context_config: ContextConfig = Field(
        description="The context config for the agent.",
        title="Context Config",
    )

    react_config: ReActConfig = Field(
        description="The react config for the agent.",
        title="React Config",
    )

    default_chat_model_config: ChatModelConfig | None = Field(
        default=None,
        description=(
            "Default chat model for this agent. Used for new sessions when "
            "the caller does not provide a model, and for sub-agent runs "
            "before falling back to the caller session's model."
        ),
        title="Agent Default Model",
    )

    allow_subagent_calls: bool = Field(
        default=False,
        description="Whether this agent may call other managed agents.",
        title="Allow Sub-Agent Calls",
    )

    allowed_subagent_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Managed agent ids this agent may call as sub-agents. "
            "Ignored when sub-agent calling is disabled."
        ),
        title="Allowed Sub-Agents",
    )

    mcps: list[MCPClient] = Field(
        default_factory=list,
        description="Workspace MCP servers shared by this agent.",
        title="MCP Servers",
    )

    mcp_assets: list[AgentMCPAsset] = Field(
        default_factory=list,
        description="Managed MCP packages shared by this agent.",
        title="MCP Assets",
    )

    skills: list[AgentSkillAsset] = Field(
        default_factory=list,
        description="Workspace skills shared by this agent.",
        title="Skills",
    )


class AgentRecord(_RecordBase):
    """The agent ORM model."""

    user_id: str
    """The user id"""

    source: Literal["user", "team"] = "user"
    """How this agent was created.

    - ``"user"``: created directly by the user (default). Can have multiple
      sessions and is listed in the user's regular agent list.
    - ``"team"``: spawned as a team worker by another agent's
      ``create_team`` / ``team_add_member`` tool. Has exactly one session.
      Team membership itself is session-level and stored on
      :class:`SessionRecord.team_id`.
    """

    data: AgentData
    """The agent data"""
