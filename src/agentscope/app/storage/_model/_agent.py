# -*- coding: utf-8 -*-
"""The agent storage class."""
import uuid
from typing import Literal

from pydantic import Field, BaseModel

from ._base import _RecordBase
from ._session import ChatModelConfig
from ....agent import ContextConfig, ReActConfig


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
            "Preferred chat model for this agent when it is executed as a "
            "sub-agent. Falls back to the caller session's model when unset."
        ),
        title="Default Sub-Agent Model",
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
