# -*- coding: utf-8 -*-
"""Tests for session-interrupt handling of in-progress tool calls."""
from unittest import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent
from agentscope.event import SessionInterruptEvent, ToolResultTextDeltaEvent
from agentscope.message import AssistantMsg, ToolCallBlock, ToolCallState
from agentscope.tool import Toolkit


class AgentInterruptTest(IsolatedAsyncioTestCase):
    """Verify interrupt handling marks partial tool output as incomplete."""

    async def test_interrupt_message_warns_about_partial_tool_output(
        self,
    ) -> None:
        """Interrupted tool calls emit an explicit incomplete-result warning."""
        agent = Agent(
            name="Friday",
            system_prompt="You are a helpful assistant.",
            model=MockModel(),
            toolkit=Toolkit(),
        )
        agent.state.reply_id = "reply-1"
        agent.state.context = [
            AssistantMsg(
                id="reply-1",
                name="Friday",
                content=[
                    ToolCallBlock(
                        id="tool-call-1",
                        name="search_docs",
                        input='{"query":"partial"',
                        state=ToolCallState.ALLOWED,
                    ),
                ],
            ),
        ]

        events = [
            evt
            async for evt in agent._handle_incoming_event(
                SessionInterruptEvent(reply_id="reply-1"),
            )
        ]

        text_events = [
            evt for evt in events if isinstance(evt, ToolResultTextDeltaEvent)
        ]
        self.assertEqual(len(text_events), 1)
        self.assertIn("must not be treated as a complete tool result", text_events[0].delta)
        self.assertEqual(agent.state.context[-1].content[0].state, "finished")
        self.assertEqual(agent.state.context[-1].content[1].state, "interrupted")
