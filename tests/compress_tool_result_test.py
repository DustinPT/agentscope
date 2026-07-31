# -*- coding: utf-8 -*-
"""The unittests for the tool result compression."""
# pylint: disable=protected-access
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent
from agentscope.message import (
    Base64Source,
    DataBlock,
    TextBlock,
    ToolResultBlock,
)
from agentscope.state import AgentState
from agentscope.tool import Toolkit


class ToolResultCompressionTest(IsolatedAsyncioTestCase):
    """Test cases for tool result compression."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.agent = Agent(
            name="TestAgent",
            system_prompt="Test system prompt",
            model=MockModel(),
            toolkit=Toolkit(),
            state=AgentState(session_id="test_session"),
        )

    async def test_merge_consecutive_text_blocks_without_truncation(self) -> None:
        """Merge adjacent text blocks and preserve DataBlock boundaries."""
        image_block = DataBlock(
            id="img-1",
            source=Base64Source(
                data="base64data",
                media_type="image/png",
            ),
        )
        tool_result = ToolResultBlock(
            id="test_1",
            name="test_tool",
            output=[
                TextBlock(text="Hello ", id="text-1"),
                TextBlock(text="World", id="text-2"),
                image_block,
                TextBlock(text="Tail ", id="text-3"),
                TextBlock(text="Segment", id="text-4"),
            ],
        )

        reserved, offload = await self.agent._split_tool_result_for_compression(
            tool_result,
        )

        self.assertIsNone(offload)
        self.assertEqual(len(reserved.output), 3)
        self.assertEqual(reserved.output[0].text, "Hello World")
        self.assertEqual(reserved.output[0].id, "text-1")
        self.assertEqual(reserved.output[1].model_dump(), image_block.model_dump())
        self.assertEqual(reserved.output[2].text, "Tail Segment")
        self.assertEqual(reserved.output[2].id, "text-3")

    async def test_truncate_oversized_merged_text_and_keep_datablock(self) -> None:
        """Preview oversized merged text while preserving DataBlock."""
        oversized_text = "A" * (60 * 1024)
        image_block = DataBlock(
            id="img-1",
            name="fake_image.png",
            source=Base64Source(
                data="AAECAwQF",
                media_type="image/png",
            ),
        )
        tool_result = ToolResultBlock(
            id="test_2",
            name="test_tool",
            output=[
                TextBlock(text=oversized_text[:30_000], id="text-1"),
                TextBlock(text=oversized_text[30_000:], id="text-2"),
                image_block,
                TextBlock(text="tail-", id="text-3"),
                TextBlock(text="text", id="text-4"),
            ],
        )

        reserved, offload = await self.agent._split_tool_result_for_compression(
            tool_result,
        )

        self.assertIsNotNone(offload)
        self.assertEqual(len(reserved.output), 3)
        self.assertEqual(reserved.output[0].id, "text-1")
        self.assertIn("... output truncated ...", reserved.output[0].text)
        self.assertEqual(reserved.output[1].model_dump(), image_block.model_dump())
        self.assertEqual(reserved.output[2].text, "tail-text")
        self.assertEqual(reserved.output[2].id, "text-3")

        self.assertEqual(len(offload.output), 1)
        self.assertEqual(offload.output[0].id, "text-1")
        self.assertEqual(offload.output[0].text, oversized_text)

    async def test_keep_string_output_when_within_limit(self) -> None:
        """Normalize string output into a single text block."""
        tool_result = ToolResultBlock(
            id="test_3",
            name="test_tool",
            output="short result",
        )

        reserved, offload = await self.agent._split_tool_result_for_compression(
            tool_result,
        )

        self.assertIsNone(offload)
        self.assertEqual(len(reserved.output), 1)
        self.assertEqual(reserved.output[0].text, "short result")
