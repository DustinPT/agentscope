# -*- coding: utf-8 -*-
"""The OpenCode Go credential."""
from typing import Literal, Type, TYPE_CHECKING

from pydantic import ConfigDict, Field, SecretStr

from ._base import CredentialBase

if TYPE_CHECKING:
    from ..model import ChatModelBase

_OPENCODE_GO_BASE_URL = "https://opencode.ai/zen/go/v1"


class OpenCodeGoCredential(CredentialBase):
    """The OpenCode Go credential model."""

    model_config = ConfigDict(
        title="OpenCode Go API",
    )

    type: Literal["opencode_go_credential"] = "opencode_go_credential"
    """The credential type."""

    api_key: SecretStr = Field(
        description="The OpenCode Go API key.",
    )
    """The API key."""

    base_url: str = Field(
        default=_OPENCODE_GO_BASE_URL,
        description="The base URL for the OpenCode Go API.",
    )
    """The base URL for the OpenCode Go API."""

    @classmethod
    def get_chat_model_class(cls) -> Type["ChatModelBase"]:
        """Return the OpenCode Go chat model class."""
        from ..model import OpenCodeGoChatModel

        return OpenCodeGoChatModel
