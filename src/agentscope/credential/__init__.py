# -*- coding: utf-8 -*-
"""The credential module."""

from ._base import CredentialBase
from ._anthropic import AnthropicCredential
from ._dashscope import DashScopeCredential
from ._deepseek import DeepSeekCredential
from ._gemini import GeminiCredential
from ._moonshot import MoonshotCredential
from ._ollama import OllamaCredential
from ._opencode_go import OpenCodeGoCredential
from ._opencode_zen import OpenCodeZenCredential
from ._openai import OpenAICredential
from ._xai import XAICredential
from ._factory import CredentialFactory


__all__ = [
    "CredentialBase",
    "AnthropicCredential",
    "DashScopeCredential",
    "DeepSeekCredential",
    "GeminiCredential",
    "MoonshotCredential",
    "OllamaCredential",
    "OpenCodeGoCredential",
    "OpenCodeZenCredential",
    "OpenAICredential",
    "XAICredential",
    "CredentialFactory",
]
