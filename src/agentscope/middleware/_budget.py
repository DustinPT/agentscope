# -*- coding: utf-8 -*-
"""Budget control middleware for AgentScope agents."""
from typing import AsyncGenerator, Callable, TYPE_CHECKING

from ..event import ModelCallEndEvent, ReplyEndEvent, ReplyStartEvent
from ..message import AssistantMsg, HintBlock
from ..tool import ToolChoice
from ._base import MiddlewareBase

if TYPE_CHECKING:
    from ..agent import Agent

_DEFAULT_HINT_MESSAGE = (
    "<system-reminder>You have reached the maximum token budget set by the "
    "user. Now you MUST wrap up immediately and provide a final "
    "concluding response without invoking any tools."
    "</system-reminder>"
)


class ReplyBudgetControlMiddleware(MiddlewareBase):
    """Middleware that enforces a weighted token budget per reply."""

    def __init__(
        self,
        token_budget: float,
        input_token_weight: float = 1,
        output_token_weight: float = 1,
        hint_message: str = _DEFAULT_HINT_MESSAGE,
    ) -> None:
        """Initialize the budget control middleware."""
        self.token_budget = token_budget
        self.input_token_weight = input_token_weight
        self.output_token_weight = output_token_weight
        self.hint_message = hint_message

    async def on_reply(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Manage per-reply budget state in ``agent.state.middle_context``."""
        middleware_key = await self.get_middleware_key()

        async for event in next_handler(**input_kwargs):
            if isinstance(event, ReplyStartEvent):
                if middleware_key not in agent.state.middle_context:
                    agent.state.middle_context[middleware_key] = {}
                agent.state.middle_context[middleware_key][event.reply_id] = 0
            elif isinstance(event, ReplyEndEvent):
                agent.state.middle_context[middleware_key].pop(
                    event.reply_id,
                    None,
                )
            elif isinstance(event, ModelCallEndEvent):
                if middleware_key not in agent.state.middle_context:
                    agent.state.middle_context[middleware_key] = {}
                agent.state.middle_context[middleware_key][event.reply_id] += (
                    self.input_token_weight * event.input_tokens
                    + self.output_token_weight * event.output_tokens
                )

            yield event

    async def on_reasoning(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Intercept each reasoning step to enforce the token budget."""
        reply_id = agent.state.reply_id
        middleware_key = await self.get_middleware_key()
        used = agent.state.middle_context.get(
            middleware_key,
            {},
        ).get(reply_id, 0)

        if used >= self.token_budget:
            hint_block = HintBlock(hint=self.hint_message)
            if (
                len(agent.state.context) > 0
                and agent.state.context[-1].role == "assistant"
                and agent.state.context[-1].name == agent.name
            ):
                agent.state.context[-1].content.append(hint_block)
            else:
                agent.state.context.append(
                    AssistantMsg(
                        id=agent.state.reply_id,
                        name=agent.name,
                        content=[hint_block],
                    ),
                )
            input_kwargs["tool_choice"] = ToolChoice(mode="none")

        async for event in next_handler(**input_kwargs):
            yield event
