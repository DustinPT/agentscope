# -*- coding: utf-8 -*-
"""Schema models for the agent service."""

from ._chat import ChatRequest, ChatTriggerResponse
from ._model import ListModelsResponse, ListModelsRequest
from ._schedule import (
    CreateScheduleRequest,
    CreateScheduleResponse,
    ListSchedulesResponse,
    ScheduleSessionsResponse,
    UpdateScheduleRequest,
)
from ._agent import (
    AgentSchemaResponse,
    ListAgentsResponse,
    AgentMCPAssetListResponse,
    CreateAgentRequest,
    CreateAgentResponse,
    UpdateAgentRequest,
    AgentComposeConfig,
    AgentComposeUpdateConfig,
    AgentComposeResponse,
    AgentSkillListResponse,
)
from ._credential import (
    CreateCredentialRequest,
    CreateCredentialResponse,
    UpdateCredentialRequest,
    ListCredentialsResponse,
    ListCredentialSchemasResponse,
)
from ._session import (
    CreateSessionRequest,
    CreateSessionResponse,
    CancelSessionResponse,
    UpdateSessionRequest,
    ListSessionsResponse,
    ListMessagesResponse,
    SessionView,
    SubAgentSessionView,
    TeamDetailResponse,
    TeamMemberView,
)

__all__ = [
    # Agent
    "AgentSchemaResponse",
    "AgentMCPAssetListResponse",
    "ListAgentsResponse",
    "CreateAgentRequest",
    "CreateAgentResponse",
    "UpdateAgentRequest",
    "AgentComposeConfig",
    "AgentComposeUpdateConfig",
    "AgentComposeResponse",
    "AgentSkillListResponse",
    "ListSchedulesResponse",
    # Chat
    "ChatRequest",
    "ChatTriggerResponse",
    # Credential
    "CreateCredentialRequest",
    "CreateCredentialResponse",
    "UpdateCredentialRequest",
    "ListCredentialsResponse",
    "ListCredentialSchemasResponse",
    # Model
    "ListModelsRequest",
    "ListModelsResponse",
    # Schedule
    "CreateScheduleRequest",
    "CreateScheduleResponse",
    "ListSchedulesResponse",
    "ScheduleSessionsResponse",
    "UpdateScheduleRequest",
    # Session
    "CreateSessionRequest",
    "CreateSessionResponse",
    "CancelSessionResponse",
    "UpdateSessionRequest",
    "ListSessionsResponse",
    "ListMessagesResponse",
    "SessionView",
    "SubAgentSessionView",
    "TeamDetailResponse",
    "TeamMemberView",
]
