# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for ToolOffloadMiddleware."""
import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from pydantic import BaseModel


from utils import MockModel

from agentscope.agent import Agent
from agentscope.app.message_bus import MessageBus
from agentscope.app.middleware import ToolOffloadMiddleware
from agentscope.app._manager import BackgroundTaskManager
from agentscope.message import TextBlock, ToolCallBlock
from agentscope.permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
)
from agentscope.tool import ToolBase, ToolChunk, Toolkit, ToolResponse


class _SlowToolParams(BaseModel):
    """Parameters for the slow test tool."""

    delay: float


class SlowTool(ToolBase):
    """A tool that sleeps for ``delay`` seconds before returning."""

    name: str = "slow_tool"
    description: str = "A slow tool for testing background offload."
    input_schema: dict = _SlowToolParams.model_json_schema()
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Always allow.

        Args:
            tool_input (`dict[str, Any]`):
                The tool input parameters.
            context (`PermissionContext`):
                The permission context.

        Returns:
            `PermissionDecision`:
                Always ALLOW.
        """
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="allowed",
        )

    async def __call__(  # type: ignore[override]
        self,
        delay: float,
    ) -> ToolChunk:
        """Sleep for *delay* seconds then return a result.

        Args:
            delay (`float`):
                Seconds to sleep.

        Returns:
            `ToolChunk`:
                A chunk containing the result text.
        """
        await asyncio.sleep(delay)
        return ToolChunk(
            content=[TextBlock(text=f"SlowTool finished after {delay}s")],
        )


class _FastToolParams(BaseModel):
    """Parameters for the fast test tool."""

    value: str


class FastTool(ToolBase):
    """A tool that returns immediately."""

    name: str = "fast_tool"
    description: str = "A fast tool for testing normal execution."
    input_schema: dict = _FastToolParams.model_json_schema()
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Always allow.

        Args:
            tool_input (`dict[str, Any]`):
                The tool input parameters.
            context (`PermissionContext`):
                The permission context.

        Returns:
            `PermissionDecision`:
                Always ALLOW.
        """
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="allowed",
        )

    async def __call__(  # type: ignore[override]
        self,
        value: str,
    ) -> ToolChunk:
        """Return a chunk with *value*.

        Args:
            value (`str`):
                The value to echo.

        Returns:
            `ToolChunk`:
                A chunk containing the value.
        """
        return ToolChunk(
            content=[TextBlock(text=f"FastTool: {value}")],
        )


class ToolOffloadMiddlewareTest(IsolatedAsyncioTestCase):
    """Test cases for the ToolOffloadMiddleware."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.mock_model = MockModel()
        self.bg_manager = BackgroundTaskManager()

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def _make_agent(
        self,
        toolkit: Toolkit,
        timeout_secs: float,
    ) -> tuple[Agent, ToolOffloadMiddleware]:
        """Create an agent with ToolOffloadMiddleware attached.

        Args:
            toolkit (`Toolkit`):
                The toolkit to attach to the agent.
            timeout_secs (`float`):
                The middleware timeout.

        Returns:
            `tuple[Agent, ToolOffloadMiddleware]`:
                The configured agent and the middleware instance.
        """
        @asynccontextmanager
        async def _noop_lock(*args: Any, **kwargs: Any):
            _ = (args, kwargs)
            yield

        message_bus = MagicMock(spec=MessageBus)
        message_bus.acquire_lock = _noop_lock
        message_bus.queue_push = AsyncMock()
        message_bus.publish = AsyncMock()
        message_bus.registry_get = AsyncMock(return_value=None)

        middleware = ToolOffloadMiddleware(
            bg_manager=self.bg_manager,
            message_bus=message_bus,
            user_id="u",
            agent_id="a",
            timeout_secs=timeout_secs,
        )
        agent = Agent(
            name="test_agent",
            system_prompt="test prompt",
            model=self.mock_model,
            toolkit=toolkit,
            middlewares=[middleware],
        )
        return agent, middleware

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    async def test_fast_tool_completes_normally(self) -> None:
        """A tool that finishes within the timeout yields its real result."""

        toolkit = Toolkit(tools=[FastTool()])
        agent, _ = self._make_agent(toolkit, timeout_secs=5.0)

        tool_call = ToolCallBlock(
            id="call_fast",
            name="fast_tool",
            input=json.dumps({"value": "hello"}),
        )

        results: list = []
        # pylint: disable=protected-access
        async for item in agent._acting(tool_call):
            results.append(item)

        # Should yield real ToolResponse (not synthetic)
        responses = [r for r in results if isinstance(r, ToolResponse)]
        self.assertEqual(len(responses), 1)
        text = responses[0].content[0].text  # type: ignore[union-attr]
        self.assertIn("FastTool: hello", text)
        # No background tasks registered
        self.assertEqual(len(self.bg_manager.tasks), 0)

    async def test_slow_tool_offloaded_to_background(self) -> None:
        """A tool that exceeds timeout returns a synthetic result."""

        toolkit = Toolkit(tools=[SlowTool()])
        # Set a very short timeout so the 0.5s tool is always offloaded
        agent, _ = self._make_agent(toolkit, timeout_secs=0.05)

        tool_call = ToolCallBlock(
            id="call_slow",
            name="slow_tool",
            input=json.dumps({"delay": 0.5}),
        )

        results: list = []
        # pylint: disable=protected-access
        async for item in agent._acting(tool_call):
            results.append(item)

        # Should yield a synthetic ToolResponse immediately
        responses = [r for r in results if isinstance(r, ToolResponse)]
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0].state, "success")
        self.assertEqual(responses[0].id, "call_slow")
        text = responses[0].content[0].text  # type: ignore[union-attr]
        self.assertIn("background", text)
        self.assertIn("id=", text)

        # Background task should be registered
        self.assertEqual(len(self.bg_manager.tasks), 1)

    async def test_background_task_result_injected_into_context(
        self,
    ) -> None:
        """After the background tool finishes, the result is pushed to the
        session inbox on the message bus as a serialised HintBlock."""

        toolkit = Toolkit(tools=[SlowTool()])
        agent, middleware = self._make_agent(toolkit, timeout_secs=0.05)

        tool_call = ToolCallBlock(
            id="call_bg",
            name="slow_tool",
            input=json.dumps({"delay": 0.2}),
        )

        # Trigger offload
        # pylint: disable=protected-access
        async for _ in agent._acting(tool_call):
            pass

        # Wait long enough for the background tool (0.2s) to finish
        await asyncio.sleep(0.4)

        # The completed result should now have been pushed to the message bus
        # inbox as a model-dumped HintBlock.
        mock_bus = middleware._message_bus
        inbox_call = mock_bus.queue_push.await_args_list[0]
        queue_key, hint_dict = inbox_call.args
        self.assertIn(agent.state.session_id, queue_key)
        self.assertEqual(hint_dict["type"], "hint")
        self.assertEqual(
            hint_dict["source"],
            '{"label": "tool_output", "sublabel": "slow_tool · call_bg"}',
        )
        hint_text = hint_dict["hint"][0]["text"]
        self.assertIn("SlowTool finished", hint_text)
        self.assertIn("<system-notification>", hint_text)

    async def test_background_task_triggers_wakeup_on_completion(
        self,
    ) -> None:
        """After a background tool finishes, a wakeup is enqueued on the
        message bus so that an idle session can be restarted automatically."""

        toolkit = Toolkit(tools=[SlowTool()])
        agent, middleware = self._make_agent(toolkit, timeout_secs=0.05)

        tool_call = ToolCallBlock(
            id="call_wakeup",
            name="slow_tool",
            input=json.dumps({"delay": 0.2}),
        )

        # Trigger offload
        # pylint: disable=protected-access
        async for _ in agent._acting(tool_call):
            pass

        # Wait long enough for the background tool (0.2s) to finish
        await asyncio.sleep(0.4)

        mock_bus = middleware._message_bus
        self.assertGreaterEqual(mock_bus.queue_push.await_count, 2)
        self.assertTrue(mock_bus.publish.await_count >= 1)

    async def test_job_kill_cancels_background_task(self) -> None:
        """job_kill cancels the running background asyncio task."""

        toolkit = Toolkit(tools=[SlowTool()])
        agent, _ = self._make_agent(toolkit, timeout_secs=0.05)

        tool_call = ToolCallBlock(
            id="call_cancel",
            name="slow_tool",
            input=json.dumps({"delay": 10.0}),
        )

        # Offload the slow tool
        # pylint: disable=protected-access
        async for _ in agent._acting(tool_call):
            pass

        self.assertEqual(len(self.bg_manager.tasks), 1)
        task_id = next(iter(self.bg_manager.tasks))
        asyncio_task = self.bg_manager.tasks[task_id].asyncio_task

        job_tools = await self.bg_manager.list_tools()
        job_kill = next(tool for tool in job_tools if tool.name == "job_kill")
        result = await job_kill(job_id=task_id)
        text = result.content[0].text  # type: ignore[union-attr]
        self.assertIn("stop requested", text)

        # The asyncio task should be cancelling
        self.assertTrue(asyncio_task.cancelled() or asyncio_task.cancelling())
        await asyncio.sleep(0)

        repeat = await job_kill(job_id=task_id)
        repeat_text = repeat.content[0].text  # type: ignore[union-attr]
        self.assertIn("already cancelled", repeat_text)

    async def test_job_list_uses_dsh_style_rendering(self) -> None:
        """job_list renders `<id> [<kind>] <status> — <label>` lines."""

        empty = self.bg_manager.list_jobs()
        self.assertEqual(empty.content[0].text, "(no background jobs)")

        toolkit = Toolkit(tools=[SlowTool()])
        agent, _ = self._make_agent(toolkit, timeout_secs=0.05)
        tool_call = ToolCallBlock(
            id="call_list",
            name="slow_tool",
            input=json.dumps({"delay": 10.0}),
        )

        async for _ in agent._acting(tool_call):
            pass

        task_id = next(iter(self.bg_manager.tasks))
        listed = self.bg_manager.list_jobs()
        text = listed.content[0].text  # type: ignore[union-attr]
        self.assertEqual(
            text,
            f"{task_id} [tool] running — slow_tool · call_list",
        )

    async def test_job_output_wait_timeout_leaves_job_running(self) -> None:
        """job_output supports timeout_ms and leaves the job running on timeout."""

        toolkit = Toolkit(tools=[SlowTool()])
        agent, _ = self._make_agent(toolkit, timeout_secs=0.05)
        tool_call = ToolCallBlock(
            id="call_wait_timeout",
            name="slow_tool",
            input=json.dumps({"delay": 0.3}),
        )

        async for _ in agent._acting(tool_call):
            pass

        task_id = next(iter(self.bg_manager.tasks))
        job_tools = await self.bg_manager.list_tools()
        job_output = next(tool for tool in job_tools if tool.name == "job_output")

        result = await job_output(job_id=task_id, wait=True, timeout_ms=10)
        text = result.content[0].text  # type: ignore[union-attr]
        self.assertIn("[status: running]", text)

    async def test_job_kill_reason_surfaces_in_job_output(self) -> None:
        """job_kill records an optional reason on the cancelled job."""

        toolkit = Toolkit(tools=[SlowTool()])
        agent, _ = self._make_agent(toolkit, timeout_secs=0.05)
        tool_call = ToolCallBlock(
            id="call_reason",
            name="slow_tool",
            input=json.dumps({"delay": 10.0}),
        )

        async for _ in agent._acting(tool_call):
            pass

        task_id = next(iter(self.bg_manager.tasks))
        job_tools = await self.bg_manager.list_tools()
        job_kill = next(tool for tool in job_tools if tool.name == "job_kill")
        job_output = next(tool for tool in job_tools if tool.name == "job_output")

        await job_kill(job_id=task_id, reason="superseded")
        await asyncio.sleep(0)

        result = await job_output(job_id=task_id, wait=False)
        text = result.content[0].text  # type: ignore[union-attr]
        self.assertIn("[status: cancelled, superseded]", text)
