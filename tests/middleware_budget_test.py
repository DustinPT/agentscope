# -*- coding: utf-8 -*-
"""Unit tests for ReplyBudgetControlMiddleware."""
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel
from agentscope.agent import Agent
from agentscope.event import ConfirmResult, UserConfirmResultEvent
from agentscope.message import HintBlock, TextBlock, ToolCallBlock, UserMsg
from agentscope.middleware import ReplyBudgetControlMiddleware
from agentscope.model import ChatResponse, ChatUsage
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from agentscope.tool import ToolBase, ToolChunk, Toolkit


def _response(
    text: str,
    input_tokens: int,
    output_tokens: int,
) -> ChatResponse:
    """Build a non-streaming ChatResponse with usage."""
    return ChatResponse(
        content=[TextBlock(text=text)],
        is_last=True,
        usage=ChatUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            time=0.0,
        ),
    )


class DummyTool(ToolBase):
    """Minimal tool that always allows and returns a fixed result."""

    name: str = "dummy"
    description: str = "A dummy tool for testing"
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_external_tool: bool = False
    is_mcp: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Always allow."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            decision_reason="Dummy tool always allows",
            message="Dummy tool always allows",
        )

    async def call(self) -> ToolChunk:
        """Return a fixed result."""
        return ToolChunk(content=[TextBlock(text="ok")])


class ConfirmRequiredTool(ToolBase):
    """Minimal tool that always requires user confirmation before running."""

    name: str = "confirm_required"
    description: str = "A tool that requires user confirmation"
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}
    is_concurrency_safe: bool = False
    is_read_only: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Always require user confirmation."""
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            decision_reason="Confirmation required",
            message="Confirmation required",
        )

    async def call(self) -> ToolChunk:
        """Return a fixed result."""
        return ToolChunk(content=[TextBlock(text="confirmed result")])


def _has_hint_block(msg: Any, hint_message: str) -> bool:
    """Return whether *msg* contains a matching ``HintBlock``."""
    content = getattr(msg, "content", None)
    if not isinstance(content, list):
        return False
    return any(
        isinstance(block, HintBlock) and hint_message in block.hint
        for block in content
    )


class TestBudgetControlMiddleware(IsolatedAsyncioTestCase):
    """Test cases for ReplyBudgetControlMiddleware."""

    async def asyncSetUp(self) -> None:
        """Set up shared fixtures."""
        self.toolkit = Toolkit()

    async def test_under_budget_no_hint_injected(self) -> None:
        """When token usage stays below the budget, no hint is injected."""
        model = MockModel()
        model.set_responses(
            [_response("done", input_tokens=10, output_tokens=5)],
        )

        middleware = ReplyBudgetControlMiddleware(token_budget=1000)
        agent = Agent(
            name="test_agent",
            system_prompt="you are helpful",
            model=model,
            toolkit=self.toolkit,
            middlewares=[middleware],
        )

        context_before = len(agent.state.context)
        await agent.reply(UserMsg("user", "hello"))

        hint_msgs = [
            msg
            for msg in agent.state.context[context_before:]
            if _has_hint_block(msg, middleware.hint_message)
        ]
        self.assertEqual(len(hint_msgs), 0)

    async def test_budget_exceeded_injects_hint(self) -> None:
        """When the budget is exceeded, the hint block is injected."""
        model = MockModel()
        model.set_responses(
            [_response("wrap up", input_tokens=10, output_tokens=5)],
        )

        middleware = ReplyBudgetControlMiddleware(token_budget=0)
        agent = Agent(
            name="test_agent",
            system_prompt="you are helpful",
            model=model,
            toolkit=self.toolkit,
            middlewares=[middleware],
        )

        context_before = len(agent.state.context)
        await agent.reply(UserMsg("user", "hello"))

        hint_msgs = [
            msg
            for msg in agent.state.context[context_before:]
            if _has_hint_block(msg, middleware.hint_message)
        ]
        self.assertGreater(len(hint_msgs), 0)

    async def test_budget_exceeded_forces_tool_choice_none(self) -> None:
        """When budget is exceeded, tool_choice forwarded to model is none."""
        received_tool_choices = []

        class TrackingModel(MockModel):
            """Model that records tool_choice on every call."""

            async def _call_api(
                self,
                *args: Any,
                **kwargs: Any,
            ) -> ChatResponse:
                received_tool_choices.append(kwargs.get("tool_choice"))
                return await super()._call_api(*args, **kwargs)

        model = TrackingModel()
        model.set_responses(
            [_response("wrap up", input_tokens=10, output_tokens=5)],
        )

        middleware = ReplyBudgetControlMiddleware(token_budget=0)
        agent = Agent(
            name="test_agent",
            system_prompt="you are helpful",
            model=model,
            toolkit=self.toolkit,
            middlewares=[middleware],
        )

        await agent.reply(UserMsg("user", "hello"))

        self.assertTrue(
            any(
                getattr(tool_choice, "mode", None) == "none"
                for tool_choice in received_tool_choices
                if tool_choice is not None
            ),
        )

    async def test_token_accumulation_across_steps(self) -> None:
        """Tokens accumulate across steps and trigger enforcement correctly."""
        toolkit = Toolkit(tools=[DummyTool()])

        model = MockModel()
        model.set_responses(
            [
                [
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id="tc_1",
                                name="dummy",
                                input="{}",
                            ),
                        ],
                        is_last=True,
                        usage=ChatUsage(
                            input_tokens=200,
                            output_tokens=100,
                            time=0.0,
                        ),
                    ),
                ],
                [
                    ChatResponse(
                        content=[TextBlock(text="done")],
                        is_last=True,
                        usage=ChatUsage(
                            input_tokens=150,
                            output_tokens=50,
                            time=0.0,
                        ),
                    ),
                ],
            ],
        )

        middleware = ReplyBudgetControlMiddleware(token_budget=300)
        agent = Agent(
            name="test_agent",
            system_prompt="you are helpful",
            model=model,
            toolkit=toolkit,
            middlewares=[middleware],
        )

        context_before = len(agent.state.context)
        await agent.reply(UserMsg("user", "hello"))

        hint_msgs = [
            msg
            for msg in agent.state.context[context_before:]
            if _has_hint_block(msg, middleware.hint_message)
        ]
        self.assertGreater(len(hint_msgs), 0)

    async def test_weighted_token_calculation(self) -> None:
        """Weighted token calculation controls budget enforcement."""
        toolkit = Toolkit(tools=[DummyTool()])

        model = MockModel()
        model.set_responses(
            [
                [
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id="tc_1",
                                name="dummy",
                                input="{}",
                            ),
                        ],
                        is_last=True,
                        usage=ChatUsage(
                            input_tokens=50,
                            output_tokens=50,
                            time=0.0,
                        ),
                    ),
                ],
                [
                    ChatResponse(
                        content=[TextBlock(text="done")],
                        is_last=True,
                        usage=ChatUsage(
                            input_tokens=30,
                            output_tokens=10,
                            time=0.0,
                        ),
                    ),
                ],
            ],
        )

        middleware = ReplyBudgetControlMiddleware(
            token_budget=200,
            input_token_weight=1,
            output_token_weight=3,
        )
        agent = Agent(
            name="test_agent",
            system_prompt="you are helpful",
            model=model,
            toolkit=toolkit,
            middlewares=[middleware],
        )

        context_before = len(agent.state.context)
        await agent.reply(UserMsg("user", "hello"))

        hint_msgs = [
            msg
            for msg in agent.state.context[context_before:]
            if _has_hint_block(msg, middleware.hint_message)
        ]
        self.assertGreater(len(hint_msgs), 0)

    async def test_state_cleanup_after_reply(self) -> None:
        """Per-reply middle_context entry is removed after reply ends."""
        model = MockModel()
        model.set_responses(
            [_response("done", input_tokens=10, output_tokens=5)],
        )

        middleware = ReplyBudgetControlMiddleware(token_budget=1000)
        agent = Agent(
            name="test_agent",
            system_prompt="you are helpful",
            model=model,
            toolkit=self.toolkit,
            middlewares=[middleware],
        )

        await agent.reply(UserMsg("user", "hello"))

        middleware_key = await middleware.get_middleware_key()
        bucket = agent.state.middle_context.get(middleware_key, {})
        self.assertEqual(len(bucket), 0)

    async def test_token_accumulation_persists_across_hitl(self) -> None:
        """Token accumulation in middle_context persists across HITL."""
        tool_call_id = "tc_hitl"
        tool_input = "{}"
        toolkit = Toolkit(tools=[ConfirmRequiredTool()])

        model = MockModel()
        model.set_responses(
            [
                [
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id=tool_call_id,
                                name="confirm_required",
                                input=tool_input,
                            ),
                        ],
                        is_last=True,
                        usage=ChatUsage(
                            input_tokens=200,
                            output_tokens=100,
                            time=0.0,
                        ),
                    ),
                ],
                [
                    ChatResponse(
                        content=[TextBlock(text="wrap up")],
                        is_last=True,
                        usage=ChatUsage(
                            input_tokens=50,
                            output_tokens=20,
                            time=0.0,
                        ),
                    ),
                ],
            ],
        )

        middleware = ReplyBudgetControlMiddleware(token_budget=300)
        agent = Agent(
            name="test_agent",
            system_prompt="you are helpful",
            model=model,
            toolkit=toolkit,
            middlewares=[middleware],
        )

        events = []
        async for event in agent.reply_stream(UserMsg("user", "hello")):
            events.append(event)

        event_types = [event.type for event in events]
        self.assertIn("REQUIRE_USER_CONFIRM", event_types)
        self.assertNotIn("REPLY_END", event_types)

        reply_id = agent.state.reply_id
        middleware_key = await middleware.get_middleware_key()
        stored = agent.state.middle_context.get(middleware_key, {})
        self.assertAlmostEqual(stored.get(reply_id, 0), 300.0)

        user_confirm_event = UserConfirmResultEvent(
            reply_id=reply_id,
            confirm_results=[
                ConfirmResult(
                    confirmed=True,
                    tool_call=ToolCallBlock(
                        id=tool_call_id,
                        name="confirm_required",
                        input=tool_input,
                    ),
                ),
            ],
        )

        resume_events = []
        async for event in agent.reply_stream(inputs=user_confirm_event):
            resume_events.append(event)

        resume_event_types = [event.type for event in resume_events]
        self.assertIn("REPLY_END", resume_event_types)

        hint_msgs = [
            msg
            for msg in agent.state.context
            if _has_hint_block(msg, middleware.hint_message)
        ]
        self.assertGreater(len(hint_msgs), 0)

        bucket = agent.state.middle_context.get(middleware_key, {})
        self.assertNotIn(reply_id, bucket)
