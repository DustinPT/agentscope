# -*- coding: utf-8 -*-
"""Bash tool test case."""
import asyncio
import os
import sys
import unittest
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from agentscope.tool import ToolChunk, Bash, LocalBackend
from agentscope.tool._builtin._bash import _subprocess_creation_kwargs
from agentscope.permission import (
    PermissionContext,
    PermissionBehavior,
    PermissionRule,
)
from agentscope.message import TextBlock


class BashSubprocessKwargsTest(unittest.TestCase):
    """Test platform-specific subprocess kwargs."""

    def test_non_windows_subprocess_kwargs_are_empty(self) -> None:
        """On non-Windows the helper returns no extra subprocess kwargs."""
        with patch("agentscope.tool._builtin._bash.os.name", "posix"):
            self.assertEqual(_subprocess_creation_kwargs(), {})

    def test_windows_subprocess_kwargs_hide_console(self) -> None:
        """On Windows the helper sets ``creationflags`` to hide the console."""
        with patch("agentscope.tool._builtin._bash.os.name", "nt"):
            self.assertEqual(
                _subprocess_creation_kwargs(),
                {"creationflags": 0x08000000},
            )


class BashCwdTest(IsolatedAsyncioTestCase):
    """Test Bash working-directory wiring."""

    async def test_cwd_is_passed_to_subprocess(self) -> None:
        """The constructor-level cwd should be used for each command."""
        handle = MagicMock()
        handle.wait = AsyncMock(return_value=MagicMock(
            stdout=b"ok\n",
            stderr=b"",
            exit_code=0,
        ))
        backend = MagicMock()
        backend.start_shell = AsyncMock(return_value=handle)

        chunks = []
        async for chunk in Bash(cwd="workspace", backend=backend).call(
            command="pwd",
            description="Print directory",
        ):
            chunks.append(chunk)

        self.assertEqual(backend.start_shell.call_args.kwargs["cwd"], "workspace")
        self.assertEqual(chunks[0].state, "success")


@unittest.skipIf(
    sys.platform == "win32",
    "Process-group termination test is POSIX-specific",
)
class LocalBackendProcessGroupTest(IsolatedAsyncioTestCase):
    """Regression tests for background process-tree termination."""

    async def test_terminate_kills_child_processes(self) -> None:
        """Terminating a shell handle also kills its child process group."""
        backend = LocalBackend()
        handle = await backend.start_shell(
            [
                "bash",
                "-lc",
                "sleep 30 & child=$!; echo $child; wait $child",
            ],
        )
        child_pid: int | None = None
        try:
            for _ in range(50):
                snapshot = handle.snapshot()
                stdout = snapshot.stdout.decode("utf-8", errors="replace").strip()
                if stdout:
                    child_pid = int(stdout.splitlines()[0].strip())
                    break
                await asyncio.sleep(0.02)

            self.assertIsNotNone(child_pid)
            await handle.terminate()

            for _ in range(50):
                try:
                    assert child_pid is not None
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    break
                await asyncio.sleep(0.02)
            else:
                self.fail(f"child process {child_pid} still alive after terminate()")
        finally:
            if child_pid is not None:
                try:
                    os.kill(child_pid, 9)
                except ProcessLookupError:
                    pass


@unittest.skipIf(
    sys.platform == "win32",
    "Bash tool is not supported on Windows",
)
class BashToolTest(IsolatedAsyncioTestCase):
    """The bash tool test case."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.bash_tool = Bash()

    async def test_tool_properties(self) -> None:
        """Test bash tool properties."""
        self.assertEqual(self.bash_tool.name, "Bash")
        self.assertIsInstance(self.bash_tool.description, str)
        self.assertIsInstance(self.bash_tool.input_schema, dict)
        self.assertFalse(self.bash_tool.is_mcp)
        self.assertFalse(self.bash_tool.is_read_only)
        self.assertFalse(self.bash_tool.is_concurrency_safe)

    async def test_check_permissions(self) -> None:
        """Test bash tool permission checking."""

        context = PermissionContext()
        tool_input = {"command": "echo hello"}
        decision = await self.bash_tool.check_permissions(tool_input, context)

        self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)

    async def test_simple_command(self) -> None:
        """Test executing a simple bash command."""
        chunks = []
        async for chunk in self.bash_tool.call(
            command="echo 'Hello World'",
            description="Print greeting",
        ):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        self.assertIsInstance(chunks[0], ToolChunk)
        self.assertEqual(chunks[0].state, "success")
        self.assertTrue(chunks[0].is_last)
        self.assertEqual(len(chunks[0].content), 1)
        self.assertIsInstance(chunks[0].content[0], TextBlock)
        self.assertIn("Hello World", chunks[0].content[0].text)
        self.assertIn("[exit code: 0]", chunks[0].content[0].text)

    async def test_command_with_error(self) -> None:
        """Non-zero exits are returned as results, not tool errors."""
        chunks = []
        async for chunk in self.bash_tool.call(
            command="exit 1",
            description="Exit with failure",
        ):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].state, "success")
        self.assertTrue(chunks[0].is_last)
        self.assertIn("[exit code: 1]", chunks[0].content[0].text)

    @unittest.skipIf(
        sys.platform == "win32",
        "sleep command not available on Windows",
    )
    async def test_command_timeout(self) -> None:
        """Test command timeout."""
        chunks = []
        async for chunk in self.bash_tool.call(
            command="sleep 10",
            description="Sleep briefly",
            timeout=100,  # 100ms timeout
        ):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].state, "success")
        self.assertIn("timed out", chunks[0].content[0].text.lower())

    async def test_timeout_promoted_to_background_has_no_null_exit_code(self) -> None:
        """Promoted runs match DSH and omit the null exit marker."""

        handle = MagicMock()
        handle.wait = AsyncMock(side_effect=[None])
        handle.snapshot.return_value = MagicMock(
            stdout=b"tick 1\ntick 2\n",
            stderr=b"",
        )
        backend = MagicMock()
        backend.start_shell = AsyncMock(return_value=handle)
        backend.getcwd = AsyncMock(return_value="/tmp")

        bg_manager = MagicMock()
        bg_manager.adopt_shell_handle = AsyncMock(return_value="job-123")

        bash = Bash(
            backend=backend,
            background_task_manager=bg_manager,
            user_id="u",
            session_id="s",
            agent_id="a",
        )

        chunks = []
        async for chunk in bash.call(
            command="sleep 10",
            description="Promote job",
            timeout=2000,
        ):
            chunks.append(chunk)

        text = chunks[0].content[0].text
        self.assertIn(
            "[still running after 2000ms; moved to background job job-123]",
            text,
        )
        self.assertNotIn("[exit code: null]", text)


@unittest.skipIf(
    sys.platform == "win32",
    "Bash tool is not supported on Windows",
)
class BashToolInjectionCheckTest(IsolatedAsyncioTestCase):
    """Test injection detection in Bash tool permission checks."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.bash_tool = Bash()
        self.context = PermissionContext()

    async def test_command_substitution_blocked(self) -> None:
        """Test that command substitution is blocked."""
        test_cases = [
            "ls $(pwd)",
            "rm $(find . -name '*.tmp')",
            "cat `which python`",
        ]
        for cmd in test_cases:
            with self.subTest(cmd=cmd):
                decision = await self.bash_tool.check_permissions(
                    {"command": cmd},
                    self.context,
                )
                self.assertEqual(decision.behavior, PermissionBehavior.ASK)
                self.assertIn("command_substitution", decision.message)

    async def test_control_flow_blocked(self) -> None:
        """Test that control flow structures are blocked."""

        test_cases = [
            "for f in *.txt; do cat $f; done",
            "while read line; do echo $line; done < file.txt",
            "if [ -f file.txt ]; then cat file.txt; fi",
        ]
        for cmd in test_cases:
            with self.subTest(cmd=cmd):
                decision = await self.bash_tool.check_permissions(
                    {"command": cmd},
                    self.context,
                )
                self.assertEqual(decision.behavior, PermissionBehavior.ASK)
                self.assertIn(
                    "cannot be statically analyzed",
                    decision.message,
                )

    async def test_subshell_blocked(self) -> None:
        """Test that subshells are blocked."""

        cmd = "(cd /tmp && ls)"
        decision = await self.bash_tool.check_permissions(
            {"command": cmd},
            self.context,
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)
        self.assertIn("subshell", decision.message)

    async def test_injection_check_before_readonly(self) -> None:
        """Test that injection check runs before read-only check."""

        # ls is read-only, but $(rm -rf /) is dangerous
        cmd = "ls $(rm -rf /)"
        decision = await self.bash_tool.check_permissions(
            {"command": cmd},
            self.context,
        )
        # Should be blocked by injection check, not allowed as read-only
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)
        self.assertIn("command_substitution", decision.message)

    async def test_safe_commands_pass(self) -> None:
        """Test that safe commands pass injection check."""

        safe_commands = [
            "ls -la",
            "cat file.txt",
            "git status",
            "echo 'hello world'",
        ]
        for cmd in safe_commands:
            with self.subTest(cmd=cmd):
                decision = await self.bash_tool.check_permissions(
                    {"command": cmd},
                    self.context,
                )
                # Should pass injection check (either ALLOW or PASSTHROUGH)
                self.assertNotEqual(decision.behavior, PermissionBehavior.ASK)
                if decision.behavior == PermissionBehavior.ASK:
                    self.assertNotIn(
                        "cannot be statically analyzed",
                        decision.message,
                    )

    async def asyncTearDown(self) -> None:
        """Clean up test fixtures."""
        self.bash_tool = None
        self.context = None


@unittest.skipIf(
    sys.platform == "win32",
    "Bash tool is not supported on Windows",
)
class BashToolMatchRuleTest(IsolatedAsyncioTestCase):
    """Test cases for Bash tool match_rule and generate_suggestions."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.bash_tool = Bash()

    async def test_match_rule_prefix_pattern(self) -> None:
        """Test match_rule with prefix patterns (e.g., git:*)."""
        # Test exact command match
        self.assertTrue(
            self.bash_tool.match_rule(
                "git:*",
                {"command": "git"},
            ),
        )

        # Test command with arguments
        self.assertTrue(
            self.bash_tool.match_rule(
                "git:*",
                {"command": "git status"},
            ),
        )

        # Test non-matching command
        self.assertFalse(
            self.bash_tool.match_rule(
                "git:*",
                {"command": "npm install"},
            ),
        )

    async def test_match_rule_wildcard_pattern(self) -> None:
        """Test match_rule with wildcard patterns."""
        # Test wildcard matching
        self.assertTrue(
            self.bash_tool.match_rule(
                "git * -m *",
                {"command": "git commit -m 'test'"},
            ),
        )

        # Test non-matching wildcard
        self.assertFalse(
            self.bash_tool.match_rule(
                "git * -m *",
                {"command": "git status"},
            ),
        )

    async def test_match_rule_substring_pattern(self) -> None:
        """Test match_rule with substring patterns."""
        # Test substring matching
        self.assertTrue(
            self.bash_tool.match_rule(
                "install",
                {"command": "npm install package"},
            ),
        )

        # Test non-matching substring
        self.assertFalse(
            self.bash_tool.match_rule(
                "install",
                {"command": "npm run build"},
            ),
        )

    async def test_match_rule_escaped_characters(self) -> None:
        """Test match_rule with escaped characters."""
        # Test escaped asterisk
        self.assertTrue(
            self.bash_tool.match_rule(
                r"echo \*",
                {"command": "echo *"},
            ),
        )

        # Test escaped backslash
        self.assertTrue(
            self.bash_tool.match_rule(
                r"echo \\",
                {"command": "echo \\"},
            ),
        )

    async def test_generate_suggestions(self) -> None:
        """Test generate_suggestions for bash commands."""

        # Test two-word command
        suggestions = self.bash_tool.generate_suggestions(
            {"command": "git commit -m 'test'"},
        )

        self.assertIsInstance(suggestions, list)
        self.assertGreater(len(suggestions), 0)
        self.assertIsInstance(suggestions[0], PermissionRule)

        # Should suggest "git commit:*"
        suggestion_contents = [s.rule_content for s in suggestions]
        self.assertIn("git commit:*", suggestion_contents)

    async def test_generate_suggestions_single_word(self) -> None:
        """Test generate_suggestions for single-word commands."""
        suggestions = self.bash_tool.generate_suggestions(
            {"command": "npm install"},
        )

        self.assertGreater(len(suggestions), 0)

        # Should suggest "npm install:*"
        suggestion_contents = [s.rule_content for s in suggestions]
        self.assertIn("npm install:*", suggestion_contents)

    async def asyncTearDown(self) -> None:
        """Clean up test fixtures."""
        self.bash_tool = None


@unittest.skipIf(
    sys.platform == "win32",
    "Bash tool is not supported on Windows",
)
class BashToolDangerousRemovalTest(IsolatedAsyncioTestCase):
    """Test dangerous removal path detection in Bash tool."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.bash_tool = Bash()
        self.context = PermissionContext()

    async def test_rm_root_blocked(self) -> None:
        """Test that rm -rf / is blocked."""

        cmd = "rm -rf /"
        decision = await self.bash_tool.check_permissions(
            {"command": cmd},
            self.context,
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)
        # Can be blocked by either dangerous command pattern or dangerous
        # removal path check
        self.assertTrue(
            "Dangerous removal operation" in decision.message
            or "dangerous pattern" in decision.message,
        )

    async def test_rm_root_children_blocked(self) -> None:
        """Test that rm -rf /usr, /etc, etc. are blocked."""

        test_cases = [
            "rm -rf /usr",
            "rm -rf /etc",
            "rm -rf /tmp",
            "rm -rf /var",
            "rm -rf /bin",
        ]
        for cmd in test_cases:
            with self.subTest(cmd=cmd):
                decision = await self.bash_tool.check_permissions(
                    {"command": cmd},
                    self.context,
                )
                self.assertEqual(decision.behavior, PermissionBehavior.ASK)
                # Can be blocked by either dangerous command pattern or
                # dangerous removal path check
                self.assertTrue(
                    "Dangerous removal operation" in decision.message
                    or "dangerous pattern" in decision.message,
                )

    async def test_rm_home_blocked(self) -> None:
        """Test that rm -rf ~ is blocked."""

        cmd = "rm -rf ~"
        decision = await self.bash_tool.check_permissions(
            {"command": cmd},
            self.context,
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)
        # Can be blocked by either dangerous command pattern or dangerous
        # removal path check
        self.assertTrue(
            "Dangerous removal operation" in decision.message
            or "dangerous pattern" in decision.message,
        )

    async def test_rm_wildcard_blocked(self) -> None:
        """Test that rm -rf * and rm -rf /* are blocked."""

        test_cases = [
            "rm -rf *",
            "rm -rf /*",
            "rm -rf /tmp/*",
        ]
        for cmd in test_cases:
            with self.subTest(cmd=cmd):
                decision = await self.bash_tool.check_permissions(
                    {"command": cmd},
                    self.context,
                )
                self.assertEqual(decision.behavior, PermissionBehavior.ASK)
                # Can be blocked by either dangerous command pattern or
                # dangerous removal path check
                self.assertTrue(
                    "Dangerous removal operation" in decision.message
                    or "dangerous pattern" in decision.message,
                )

    async def test_rmdir_dangerous_paths_blocked(self) -> None:
        """Test that rmdir on dangerous paths is blocked."""

        test_cases = [
            "rmdir /",
            "rmdir /usr",
            "rmdir ~",
        ]
        for cmd in test_cases:
            with self.subTest(cmd=cmd):
                decision = await self.bash_tool.check_permissions(
                    {"command": cmd},
                    self.context,
                )
                self.assertEqual(decision.behavior, PermissionBehavior.ASK)
                self.assertIn("Dangerous removal operation", decision.message)

    async def test_safe_rm_commands_pass(self) -> None:
        """Test that safe rm commands pass dangerous removal check."""

        safe_commands = [
            "rm file.txt",
            "rm -f temp.log",
            "rm -rf /tmp/my_project/build",
            "rm -rf ./node_modules",
        ]
        for cmd in safe_commands:
            with self.subTest(cmd=cmd):
                decision = await self.bash_tool.check_permissions(
                    {"command": cmd},
                    self.context,
                )
                # Should not be blocked by dangerous removal check
                # (may still be blocked by other checks)
                if decision.behavior == PermissionBehavior.ASK:
                    self.assertNotIn(
                        "Dangerous removal operation",
                        decision.message,
                    )

    async def test_compound_commands_with_dangerous_removal(self) -> None:
        """Test compound commands containing dangerous removal."""

        test_cases = [
            "ls && rm -rf /",
            "cd /tmp && rm -rf /usr",
            "echo start; rm -rf ~; echo end",
        ]
        for cmd in test_cases:
            with self.subTest(cmd=cmd):
                decision = await self.bash_tool.check_permissions(
                    {"command": cmd},
                    self.context,
                )
                self.assertEqual(decision.behavior, PermissionBehavior.ASK)
                # Can be blocked by either dangerous command pattern or
                # dangerous removal path check
                self.assertTrue(
                    "Dangerous removal operation" in decision.message
                    or "dangerous pattern" in decision.message,
                )

    async def asyncTearDown(self) -> None:
        """Clean up test fixtures."""
        self.bash_tool = None
        self.context = None
