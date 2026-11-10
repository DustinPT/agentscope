# -*- coding: utf-8 -*-
"""Tests for DSH-style subagent identity prompt guidance."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

from agentscope.app._subagent_runtime import attach_subagent_meta
from agentscope.app.middleware import SubAgentPromptMiddleware
from agentscope.permission import PermissionContext
from agentscope.state import AgentState


class TestSubagentPromptMiddleware(IsolatedAsyncioTestCase):
    async def test_root_prompt_includes_dsh_identity_guidance(self) -> None:
        middleware = SubAgentPromptMiddleware(
            storage=SimpleNamespace(),
            message_bus=SimpleNamespace(),
            user_id="u",
            agent_id="a",
            session_id="s",
            session_is_subagent=False,
            enable_subagent_controls=True,
        )

        prompt = await middleware.on_system_prompt(
            SimpleNamespace(state=AgentState(permission_context=PermissionContext())),
            "Base prompt",
        )

        self.assertIn('You are the root agent. Your name is "lead".', prompt)
        self.assertIn(
            'To message other agents, use send_message({ agent: "<agent-name>", message: "..." }).',
            prompt,
        )
        self.assertIn(
            "Use list_agents({}) to find your collaborator agents and their names.",
            prompt,
        )

    async def test_child_prompt_includes_identity_and_task_protocol(self) -> None:
        middleware = SubAgentPromptMiddleware(
            storage=SimpleNamespace(),
            message_bus=SimpleNamespace(),
            user_id="u",
            agent_id="a",
            session_id="s",
            session_is_subagent=True,
            enable_subagent_controls=True,
        )
        state = AgentState(permission_context=PermissionContext())
        attach_subagent_meta(
            state,
            root_session_id="root",
            parent_session_id="parent",
            parent_name="lead",
            name="reviewer",
            path="reviewer",
            label="Review code",
            mode="spawn",
        )

        prompt = await middleware.on_system_prompt(
            SimpleNamespace(state=state),
            "Base prompt",
        )

        self.assertIn('You are a subagent delegated by agent "lead".', prompt)
        self.assertIn('Your name is "reviewer".', prompt)
        self.assertIn(
            'The root agent of your collaboration tree is "lead".',
            prompt,
        )
        self.assertIn(
            "When you receive a message wrapped in `<subagent-task ...>...</subagent-task>`",
            prompt,
        )
        self.assertIn(
            "Your final answer is delivered automatically if your parent is still online.",
            prompt,
        )
