# -*- coding: utf-8 -*-
"""The OpenCode Go chat model implementation."""
from typing import Literal, Any, AsyncGenerator, Type

from pydantic import BaseModel, Field

from .._base import ChatModelBase
from .._model_response import ChatResponse, StructuredResponse
from .._anthropic._model import AnthropicChatModel
from .._openai_chat._model import OpenAIChatModel
from ...credential import (
    AnthropicCredential,
    OpenAICredential,
    OpenCodeGoCredential,
)
from ...message import Msg
from ...tool import ToolChoice

_API_STYLE_CHAT_COMPLETIONS = "chat_completions"
_API_STYLE_ANTHROPIC_MESSAGES = "anthropic_messages"
_SUPPORTED_API_STYLES = {
    _API_STYLE_CHAT_COMPLETIONS,
    _API_STYLE_ANTHROPIC_MESSAGES,
}


class OpenCodeGoChatModel(ChatModelBase):
    """The OpenCode Go chat model.

    OpenCode Go exposes both OpenAI-compatible and Anthropic-compatible
    endpoints under a single provider. This wrapper delegates to the existing
    model implementations based on model-card metadata.
    """

    class Parameters(BaseModel):
        """The parameters for the OpenCode Go chat model."""

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

        thinking_budget: int | None = Field(
            default=None,
            title="Thinking Budget",
            description=(
                "The thinking budget for Anthropic-compatible Go models."
            ),
            gt=0,
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

    type: Literal["opencode_go_chat"] = "opencode_go_chat"
    """The type of the chat model."""

    def __init__(
        self,
        credential: OpenCodeGoCredential,
        model: str,
        parameters: "OpenCodeGoChatModel.Parameters | None" = None,
        stream: bool = True,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        context_size: int = 200000,
        formatter_input_media_types: list[str] | None = None,
        formatter_tool_result_media_types: list[str] | None = None,
        formatter_input_types: list[str] | None = None,
        api_style: Literal["chat_completions", "anthropic_messages"] = (
            _API_STYLE_CHAT_COMPLETIONS
        ),
    ) -> None:
        """Initialize the OpenCode Go chat model."""
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
                f"Unsupported OpenCode Go api_style: {api_style!r}.",
            )
        self.api_style = api_style
        self.formatter_input_media_types = (
            formatter_input_media_types
            if formatter_input_media_types is not None
            else formatter_input_types
        )
        self.formatter_tool_result_media_types = (
            formatter_tool_result_media_types
        )

    @classmethod
    def _get_retryable_exceptions(cls) -> tuple[Type[Exception], ...]:
        return (
            *OpenAIChatModel._get_retryable_exceptions(),
            *AnthropicChatModel._get_retryable_exceptions(),
        )

    @classmethod
    def get_runtime_init_kwargs(
        cls,
        model_name: str,
        custom_yaml_dir: str | None = None,
    ) -> dict[str, Any]:
        """Include formatter capabilities derived from the model card."""
        runtime_init_kwargs = super().get_runtime_init_kwargs(
            model_name=model_name,
            custom_yaml_dir=custom_yaml_dir,
        )
        card = cls.get_model_card(
            model_name=model_name,
            custom_yaml_dir=custom_yaml_dir,
        )
        if card is not None:
            runtime_init_kwargs["formatter_input_media_types"] = (
                card.input_types
            )
            runtime_init_kwargs["formatter_tool_result_media_types"] = (
                card.tool_result_media_types
            )
        return runtime_init_kwargs

    def _to_openai_credential(self) -> OpenAICredential:
        """Build an OpenAI-compatible credential for delegate models."""
        return OpenAICredential(
            api_key=self.credential.api_key,
            base_url=self.credential.base_url,
        )

    def _to_anthropic_credential(self) -> AnthropicCredential:
        """Build an Anthropic-compatible credential for delegate models."""
        base_url = self.credential.base_url.rstrip("/")
        if base_url.endswith("/v1"):
            base_url = base_url[: -len("/v1")]
        return AnthropicCredential(
            api_key=self.credential.api_key,
            base_url=base_url,
        )

    def _build_delegate_model(self) -> OpenAIChatModel | AnthropicChatModel:
        """Create the underlying protocol-specific model delegate."""
        if self.api_style == _API_STYLE_ANTHROPIC_MESSAGES:
            anthropic_parameters = AnthropicChatModel.Parameters(
                max_tokens=self.parameters.max_tokens,
                thinking_enable=self.parameters.thinking_enable,
                thinking_budget=self.parameters.thinking_budget,
            )
            return AnthropicChatModel(
                credential=self._to_anthropic_credential(),
                model=self.model,
                parameters=anthropic_parameters,
                stream=self.stream,
                max_retries=self.max_retries,
                retry_delay=self.retry_delay,
                context_size=self.context_size,
                formatter_input_media_types=self.formatter_input_media_types,
                formatter_tool_result_media_types=(
                    self.formatter_tool_result_media_types
                ),
            )

        openai_parameters = OpenAIChatModel.Parameters(
            max_tokens=self.parameters.max_tokens,
            thinking_enable=self.parameters.thinking_enable,
            reasoning_effort=self.parameters.reasoning_effort,
            temperature=self.parameters.temperature,
            top_p=self.parameters.top_p,
        )
        return OpenAIChatModel(
            credential=self._to_openai_credential(),
            model=self.model,
            parameters=openai_parameters,
            stream=self.stream,
            max_retries=self.max_retries,
            retry_delay=self.retry_delay,
            context_size=self.context_size,
            formatter_input_media_types=self.formatter_input_media_types,
            formatter_tool_result_media_types=(
                self.formatter_tool_result_media_types
            ),
        )

    def _build_generate_kwargs(
        self,
        generate_kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        """Build provider-specific generate kwargs before delegating."""
        merged_kwargs = dict(generate_kwargs)

        # Some OpenCode Go models exposed via the OpenAI-compatible route still
        # honor a provider-specific ``thinking.type`` toggle in ``extra_body``.
        # Explicitly forward the toggle so disabling thinking from the frontend
        # really disables it for these reasoning-capable models.
        if (
            self.api_style == _API_STYLE_CHAT_COMPLETIONS
            and self.model.startswith(("deepseek-", "mimo-", "glm-"))
        ):
            thinking_type = (
                "enabled" if self.parameters.thinking_enable else "disabled"
            )
            extra_body = dict(merged_kwargs.get("extra_body") or {})
            thinking = dict(extra_body.get("thinking") or {})
            thinking.setdefault("type", thinking_type)
            extra_body["thinking"] = thinking
            merged_kwargs["extra_body"] = extra_body

        # OpenCode Go's Anthropic-compatible models may still emit thinking
        # content unless the compatibility layer is told explicitly to disable
        # it. Forward the disabled toggle so frontend "Thinking = off" is
        # respected for models like Qwen 3.7 Plus.
        if (
            self.api_style == _API_STYLE_ANTHROPIC_MESSAGES
            and not self.parameters.thinking_enable
            and "thinking" not in merged_kwargs
        ):
            merged_kwargs["thinking"] = {"type": "disabled"}

        return merged_kwargs

    async def _call_api(
        self,
        model_name: str,
        messages: list[Msg],
        tools: list[dict] | None = None,
        tool_choice: ToolChoice | None = None,
        **generate_kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Call the OpenCode Go model via the mapped provider protocol."""
        delegate = self._build_delegate_model()
        return await delegate._call_api(
            model_name=model_name,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            **self._build_generate_kwargs(generate_kwargs),
        )

    async def _call_api_with_structured_output(
        self,
        model_name: str,
        messages: list[Msg],
        structured_model: Type[BaseModel] | dict,
        tool_choice: ToolChoice | None = None,
        **kwargs: Any,
    ) -> StructuredResponse:
        """OpenCode Go structured-output compatibility shim.

        This wrapper delegates normal chat calls to OpenAI- or
        Anthropic-compatible model implementations, but structured output is
        still handled by ``ChatModelBase`` on this wrapper itself. When
        thinking is enabled, some upstream providers reject forced tool
        selection for structured output. Downgrade the default tool choice to
        ``auto`` so the injected reminder prompt can still guide the model.
        """
        if tool_choice is None and self.parameters.thinking_enable:
            tool_choice = ToolChoice(mode="auto")
        return await super()._call_api_with_structured_output(
            model_name=model_name,
            messages=messages,
            structured_model=structured_model,
            tool_choice=tool_choice,
            **kwargs,
        )
