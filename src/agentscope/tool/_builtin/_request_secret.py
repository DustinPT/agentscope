# -*- coding: utf-8 -*-
"""Builtin external tool for securely collecting secrets."""
from typing import Any

from pydantic import BaseModel, Field, model_validator

from .._base import ToolBase
from ...permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)

_HEADER_MAX_CHARS = 12


class RequestSecretField(BaseModel):
    """One secret field requested from the user."""

    key: str = Field(
        min_length=1,
        description=(
            "The environment variable name that the submitted secret should "
            "bind to for later Bash calls, for example `GITHUB_TOKEN`."
        ),
    )
    label: str = Field(
        min_length=1,
        description="The human-readable field label shown to the user.",
    )
    description: str | None = Field(
        default=None,
        description="Optional helper text shown below the field.",
    )
    placeholder: str | None = Field(
        default=None,
        description="Optional placeholder shown in the input box.",
    )
    required: bool = Field(
        default=True,
        description="Whether the user must provide a value for this field.",
    )


class RequestSecretParams(BaseModel):
    """Parameters accepted by the RequestSecret tool."""

    title: str = Field(
        default="Secret input required",
        description="Short title shown in the external secret collection UI.",
    )
    description: str | None = Field(
        default=None,
        description="Optional explanatory text shown above the secret form.",
    )
    header: str | None = Field(
        default=None,
        max_length=_HEADER_MAX_CHARS,
        description=(
            "Optional very short label displayed as a chip/tag in UIs that "
            f"support it (max {_HEADER_MAX_CHARS} chars)."
        ),
    )
    fields: list[RequestSecretField] = Field(
        min_length=1,
        max_length=8,
        description="The secret fields that the user should fill in.",
    )
    ttl_minutes: int = Field(
        default=30,
        ge=1,
        le=24 * 60,
        description=(
            "How long the submitted secrets should stay bound to the current "
            "session's Bash environment."
        ),
    )

    @model_validator(mode="after")
    def _validate_keys(self) -> "RequestSecretParams":
        """Ensure env var bindings are unique within one request."""
        keys = [field.key for field in self.fields]
        if len(keys) != len(set(keys)):
            raise ValueError("secret field keys must be unique")
        return self


class RequestSecretResultMetadata(BaseModel):
    """Safe metadata returned to the model after secret submission."""

    bound_env_vars: list[str] = Field(
        default_factory=list,
        description="Environment variable names bound for this session.",
    )
    binding_scope: str = Field(
        default="session",
        description="The binding scope for the submitted secrets.",
    )
    expires_at: str | None = Field(
        default=None,
        description="ISO 8601 expiration time for the current binding.",
    )


class RequestSecret(ToolBase):
    """Collect secrets through an external UI without exposing values."""

    name: str = "RequestSecret"
    description: str = """Requests sensitive values such as API keys, passwords, tokens, or other secrets from the user through an external secure form.

Use this tool when the agent needs secret input that should not be written into the conversation context. After the user submits the form, the service stores the secrets out-of-band and binds them to later Bash calls as environment variables for the current session.

Important rules:
- Use this only for secret or sensitive values.
- Bind each field key to the exact environment variable name the command expects.
- Do not ask the user to paste secrets into normal chat messages.
- The tool result only confirms which environment variables were bound; it does not return secret values."""
    input_schema: dict[str, Any] = RequestSecretParams.model_json_schema()
    metadata_schema: dict[str, Any] | None = (
        RequestSecretResultMetadata.model_json_schema()
    )
    is_read_only: bool = False
    is_state_injected: bool = False
    is_concurrency_safe: bool = True
    is_external_tool: bool = True

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Requesting secrets never needs an extra confirmation step."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="The RequestSecret tool is always permitted.",
        )
