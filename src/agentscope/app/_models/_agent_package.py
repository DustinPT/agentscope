# -*- coding: utf-8 -*-
"""Shared schema models for agent package import."""
from typing import Literal

from pydantic import BaseModel, Field

from ..storage import AgentRecord


class AgentPackageImportResult(BaseModel):
    """One create/update result produced by package import."""

    agent_id: str = Field(description="Imported agent ID.")
    action: Literal["created", "updated"] = Field(
        description="Whether the agent was created or updated.",
    )
    agent: AgentRecord = Field(description="The imported agent record.")


class AgentPackageImportResponse(BaseModel):
    """Response body for agent package import."""

    main_agent_id: str = Field(
        description="Imported main agent ID resolved from config.json.main_agent_slug.",
    )
    results: list[AgentPackageImportResult] = Field(
        description="Per-agent import results.",
    )
    created_count: int = Field(description="Number of created agents.")
    updated_count: int = Field(description="Number of updated agents.")
