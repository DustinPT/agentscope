# -*- coding: utf-8 -*-
"""The OpenCode Zen chat model implementation."""
from typing import Literal, Any, AsyncGenerator, Type

from pydantic import BaseModel, Field

from .._base import ChatModelBase
from .._model_response import ChatResponse
from .._openai_chat._model import OpenAIChatModel
from .._openai_response._model import OpenAIResponseModel
from ...credential import OpenAICredential, OpenCodeZenCredential
from ...message import Msg
from ...tool import ToolChoice

_API_STYLE_RESPONSES = "responses"
_API_STYLE_CHAT_COMPLETIONS = "chat_completions"
_SUPPORTED_API_STYLES = {
    _API_STYLE_RESPONSES,
    _API_STYLE_CHAT_COMPLETIONS,
}


class OpenCodeZenChatModel(ChatModelBase):
    """The OpenCode Zen chat model.

    Zen aggregates multiple model families behind different OpenAI-compatible
    protocols. This wrapper keeps a single provider surface while delegating
    the actual request/response handling to the existing OpenAI model
    implementations based on model-card metadata.
    """

    class Parameters(BaseModel):
        """The parameters for the OpenCode Zen chat model."""

        max_tokens: int | None = Field(
            default=None,
            title="Max Tokens",
            description="The maximum number of tokens for the LLM output.",
            gt=0,
        )

        thinking_enable: bool = Field(
            default=False,
            title="Thinking",
            description=(
                "Whether to enable reasoning for models that support it."
            ),
        )

        reasoning_effort: (
            Literal["none", "minimal", "low", "medium", "high", "xhigh"] | None
        ) = Field(
            default=None,
            title="Reasoning Effort",
            description=(
                "Controls the depth of reasoning when supported by the "
                "selected model."
            ),
        )

        temperature: float | None = Field(
            default=None,
            title="Temperature",
            description="The temperature for the LLM output.",
            ge=0,
            le=2,
        )

        top_p: float | None = Field(
            default=None,
            title="Top P",
            description="The top P value for the LLM output.",
            gt=0,
            le=1,
        )

    type: Literal["opencode_zen_chat"] = "opencode_zen_chat"
    """The type of the chat model."""

    def __init__(
        self,
        credential: OpenCodeZenCredential,
        model: str,
        parameters: "OpenCodeZenChatModel.Parameters | None" = None,
        stream: bool = True,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        context_size: int = 200000,
        api_style: Literal["responses", "chat_completions"] = (
            _API_STYLE_CHAT_COMPLETIONS
        ),
    ) -> None:
        """Initialize the OpenCode Zen chat model."""
        super().__init__(
            credential=credential,
            model=model,
            parameters=parameters or self.Parameters(),
            stream=stream,
            max_retries=max_retries,
            retry_delay=retry_delay,
            context_size=context_size,
        )
        if api_style not in _SUPPORTED_API_STYLES:
            raise ValueError(
                f"Unsupported OpenCode Zen api_style: {api_style!r}.",
            )
        self.api_style = api_style

    @classmethod
    def _get_retryable_exceptions(cls) -> tuple[Type[Exception], ...]:
        return OpenAIChatModel._get_retryable_exceptions()

    def _to_openai_credential(self) -> OpenAICredential:
        """Build an OpenAI-compatible credential for delegate models."""
        return OpenAICredential(
            api_key=self.credential.api_key,
            base_url=self.credential.base_url,
        )

    def _build_delegate_model(self) -> OpenAIChatModel | OpenAIResponseModel:
        """Create the underlying OpenAI-compatible model delegate."""
        credential = self._to_openai_credential()

        if self.api_style == _API_STYLE_RESPONSES:
            response_parameters = OpenAIResponseModel.Parameters(
                max_tokens=self.parameters.max_tokens,
                thinking_enable=self.parameters.thinking_enable,
                reasoning_effort=self.parameters.reasoning_effort,
                temperature=self.parameters.temperature,
            )
            return OpenAIResponseModel(
                credential=credential,
                model=self.model,
                parameters=response_parameters,
                stream=self.stream,
                max_retries=self.max_retries,
                retry_delay=self.retry_delay,
                context_size=self.context_size,
            )

        chat_parameters = OpenAIChatModel.Parameters(
            max_tokens=self.parameters.max_tokens,
            thinking_enable=self.parameters.thinking_enable,
            reasoning_effort=self.parameters.reasoning_effort,
            temperature=self.parameters.temperature,
            top_p=self.parameters.top_p,
        )
        return OpenAIChatModel(
            credential=credential,
            model=self.model,
            parameters=chat_parameters,
            stream=self.stream,
            max_retries=self.max_retries,
            retry_delay=self.retry_delay,
            context_size=self.context_size,
        )

    def _build_generate_kwargs(
        self,
        generate_kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        """Build provider-specific generate kwargs before delegating."""
        merged_kwargs = dict(generate_kwargs)

        # Zen exposes several reasoning-capable model families through the
        # OpenAI chat-completions surface. These models may keep emitting
        # reasoning content unless the provider-specific ``thinking.type``
        # toggle is forwarded explicitly.
        if (
            self.api_style == _API_STYLE_CHAT_COMPLETIONS
            and self.model.startswith(
                ("deepseek-", "kimi-", "glm-", "minimax-", "grok-"),
            )
        ):
            thinking_type = (
                "enabled" if self.parameters.thinking_enable else "disabled"
            )
            extra_body = dict(merged_kwargs.get("extra_body") or {})
            thinking = dict(extra_body.get("thinking") or {})
            thinking.setdefault("type", thinking_type)
            extra_body["thinking"] = thinking
            merged_kwargs["extra_body"] = extra_body

        return merged_kwargs

    async def _call_api(
        self,
        model_name: str,
        messages: list[Msg],
        tools: list[dict] | None = None,
        tool_choice: ToolChoice | None = None,
        **generate_kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Call the OpenCode Zen model via the mapped OpenAI-compatible API."""
        delegate = self._build_delegate_model()
        return await delegate._call_api(
            model_name=model_name,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            **self._build_generate_kwargs(generate_kwargs),
        )
