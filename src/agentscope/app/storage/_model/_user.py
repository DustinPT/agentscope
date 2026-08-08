# -*- coding: utf-8 -*-
"""The user record for storage."""

from pydantic import BaseModel, ConfigDict, Field

from ._base import _RecordBase
from ._session import ChatModelConfig


class GlobalDefaultModels(BaseModel):
    """User-level default model configuration grouped by task category."""

    model_config = ConfigDict(extra="forbid")

    visual_engineering: ChatModelConfig | None = Field(
        default=None,
        description="Default model for frontend, UI, CSS, and design work.",
    )
    ultrabrain: ChatModelConfig | None = Field(
        default=None,
        description="Default model for maximum reasoning tasks.",
    )
    deep: ChatModelConfig | None = Field(
        default=None,
        description="Default model for deep coding and complex logic tasks.",
    )
    artistry: ChatModelConfig | None = Field(
        default=None,
        description="Default model for creative and novel approaches.",
    )
    quick: ChatModelConfig | None = Field(
        default=None,
        description="Default model for simple and fast tasks.",
    )
    unspecified_low: ChatModelConfig | None = Field(
        default=None,
        description="Default model for general standard work.",
    )
    unspecified_high: ChatModelConfig | None = Field(
        default=None,
        description="Default model for general complex work.",
    )
    writing: ChatModelConfig | None = Field(
        default=None,
        description="Default model for text, docs, and prose tasks.",
    )


class UserRecord(_RecordBase):
    """The persisted per-user settings record."""

    user_id: str = Field(description="Owner user id.")
    global_default_models: GlobalDefaultModels = Field(
        default_factory=GlobalDefaultModels,
        description="User-level default model configuration by task category.",
    )
