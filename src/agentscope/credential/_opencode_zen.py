# -*- coding: utf-8 -*-
"""The OpenCode Zen credential."""
from typing import Literal, Type, TYPE_CHECKING

from pydantic import ConfigDict, Field, SecretStr

from ._base import CredentialBase

if TYPE_CHECKING:
    from ..model import ChatModelBase

_OPENCODE_ZEN_BASE_URL = "https://opencode.ai/zen/v1"


class OpenCodeZenCredential(CredentialBase):
    """The OpenCode Zen credential model."""

    model_config = ConfigDict(
        title="OpenCode Zen API",
    )

    type: Literal["opencode_zen_credential"] = "opencode_zen_credential"
    """The credential type."""

    api_key: SecretStr = Field(
        description="The OpenCode Zen API key.",
    )
    """The API key."""

    base_url: str = Field(
        default=_OPENCODE_ZEN_BASE_URL,
        description="The base URL for the OpenCode Zen API.",
    )
    """The base URL for the OpenCode Zen API."""

    @classmethod
    def get_chat_model_class(cls) -> Type["ChatModelBase"]:
        """Return the OpenCode Zen chat model class."""
        from ..model import OpenCodeZenChatModel

        return OpenCodeZenChatModel
