# -*- coding: utf-8 -*-
"""The bash tool in agentscope."""
import os
from typing import TYPE_CHECKING, AsyncGenerator, Any, List
import re

from ._backend import (
    BackendBase,
    LocalBackend,
    _normalize_newlines,
    _subprocess_creation_kwargs,
)
from ._bash_parser import BashCommandParser
from .._base import ToolBase, ToolMiddlewareBase
from .._constants import (
    DEFAULT_DANGEROUS_FILES,
    DEFAULT_DANGEROUS_DIRECTORIES,
)
from ...permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
    PermissionMode,
    PermissionRule,
)
from ...message import TextBlock, ToolResultState
from .._response import ToolChunk
from ..._request_secret import (
    get_session_secret_env_map,
    redact_text_by_secret_values,
)

if TYPE_CHECKING:
    from ...app.storage import StorageBase
    from ...app._manager import BackgroundTaskManager


class Bash(ToolBase):
    """The bash tool."""

    name: str = "Bash"
    """The tool name presented to the agent."""

    description: str = """Execute a bash command (`bash -lc`) and return its stdout/stderr.

Each call runs in a fresh shell. Working directory, variables, and
functions do not persist across calls. Pass `workdir` instead of
relying on `cd`.

IMPORTANT: Avoid using this tool to run `find`, `grep`, `cat`, `head`,
`tail`, `sed`, `awk`, or `echo` commands, unless explicitly instructed
or after you have verified that a dedicated tool cannot accomplish your
task. Instead, use the appropriate dedicated tool as this will provide
a much better experience for the user:

 - File search: Use Glob (NOT find or ls)
 - Content search: Use Grep (NOT grep or rg)
 - Read files: Use Read (NOT cat/head/tail)
 - Edit files: Use Edit (NOT sed/awk)
 - Write files: Use Write (NOT echo >/cat <<EOF)
 - Communication: Output text directly (NOT echo/printf)

While the Bash tool can do similar things, it's better to use the
built-in tools as they provide a better user experience and make it
easier to review tool calls and give permission.

# Instructions
 - If your command will create new directories or files, first use
   this tool to run `ls` to verify the parent directory exists and is
   the correct location.
 - Always quote file paths that contain spaces with double quotes in
   your command (e.g., cd "path with spaces/file.txt")
 - Try to maintain your current working directory throughout the
   session by using absolute paths and avoiding usage of `cd`. You may
   use `cd` if the User explicitly requests it.
 - You may specify an optional timeout in milliseconds (up to 600000ms
   / 10 minutes). By default, the tool waits 120000ms (2 minutes). If
   the command is still running after the timeout, it moves to the
   background as a job instead of being killed.
 - Write a clear, concise, user-facing description of what your
   command does. This description is shown directly to the user, so
   phrase it in the user's language, avoid shell jargon when possible,
   and describe the intended action or outcome rather than repeating
   the command itself. For simple commands, keep it brief (5-10 words).
   For complex commands (piped commands, obscure flags, or anything
   hard to understand at a glance), include enough context so that the
   user can understand what your command will do.
 - When issuing multiple commands:
  - If the commands are independent and can run in parallel, make
    multiple Bash tool calls in a single message. Example: if you need
    to run "git status" and "git diff", send a single message with two
    Bash tool calls in parallel.
  - If the commands depend on each other and must run sequentially,
    use a single Bash call with '&&' to chain them together.
  - Use ';' only when you need to run commands sequentially but don't
    care if earlier commands fail.
  - DO NOT use newlines to separate commands (newlines are ok in
    quoted strings).
 - For git commands:
  - Prefer to create a new commit rather than amending an existing
    commit.
  - Before running destructive operations (e.g., git reset --hard, git
    push --force, git checkout --), consider whether there is a safer
    alternative that achieves the same goal. Only use destructive
    operations when they are truly the best approach.
  - Never skip hooks (--no-verify) or bypass signing (--no-gpg-sign,
    -c commit.gpgsign=false) unless the user has explicitly asked for
    it. If a hook fails, investigate and fix the underlying issue.
 - Avoid unnecessary `sleep` commands:
  - Do not sleep between commands that can run immediately — just run
    them.
  - Do not retry failing commands in a sleep loop — diagnose the root
    cause or consider an alternative approach.
  - If you must sleep, keep the duration short (1-5 seconds) to avoid
    blocking the user.
 - Do not use `2>/dev/null` or other `/dev/null` redirections by
   default. Keep stderr visible for unknown commands, external CLIs,
   search commands, network commands, or any command where success,
   failure, or "no result" must be judged from the output. Only use
   `2>/dev/null` when you already know stderr is irrelevant noise and
   hiding it will not affect task correctness.
 - Example of what can go wrong with `2>/dev/null`: if an external
   command fails and writes the real error to stderr, `2>/dev/null`
   hides that error. If the command is also piped through something
   like `head`, the overall command can misleadingly look like it
   simply returned no results instead of failing.
 - Do not use `head`, `tail`, or similar truncation commands merely to
   limit tool output size. The Bash tool already truncates overly long
   output safely. Use `head` or `tail` only when the task itself
   explicitly needs the first or last N lines or results.
 - `run_in_background: true` returns a job id immediately; use
   `job_output` to collect it, `job_list` to inspect it, and `job_kill`
   to stop it.
 - Check the `[exit code: N]` marker on every bash result. Non-zero
   exit codes are results to interpret, not automatic tool failures."""
    """The description presented to the agent."""

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": (
                    "Clear, concise description of what this command "
                    "does in active voice, 5-10 words when possible "
                    "(shown in the UI). Example: 'git status' -> "
                    "'Show working tree status'."
                ),
            },
            "command": {
                "type": "string",
                "description": "The bash command to execute.",
            },
            "timeout": {
                "type": "integer",
                "description": (
                    "Timeout in milliseconds (default: 120000, max: "
                    "600000). On expiry the command moves to the "
                    "background as a job instead of being killed."
                ),
                "default": 120000,
                "maximum": 600000,
                "minimum": 0,
            },
            "workdir": {
                "type": "string",
                "description": (
                    "Working directory for this command. Defaults to "
                    "the session workspace; a relative path is "
                    "resolved against it."
                ),
            },
            "run_in_background": {
                "type": "boolean",
                "description": (
                    "Run in the background and return a job id "
                    "immediately (collect with job_output, inspect "
                    "with job_list, stop with job_kill). No timeout "
                    "applies."
                ),
                "default": False,
            },
        },
        "required": ["description", "command"],
    }

    is_mcp: bool = False
    is_read_only: bool = False
    is_concurrency_safe: bool = False
    is_external_tool: bool = False
    is_state_injected: bool = False

    def __init__(  # pylint: disable=dangerous-default-value
        self,
        dangerous_files: list[str] = DEFAULT_DANGEROUS_FILES,
        dangerous_directories: list[str] = DEFAULT_DANGEROUS_DIRECTORIES,
        cwd: str | os.PathLike[str] | None = None,
        middlewares: List[ToolMiddlewareBase] | None = None,
        backend: BackendBase | None = None,
        storage: "StorageBase | None" = None,
        user_id: str | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
        background_task_manager: "BackgroundTaskManager | None" = None,
    ) -> None:
        """Initialize the bash tool.

        Args:
            dangerous_files (`list[str]`, optional):
                Sensitive files that require explicit user confirmation,
                even in BYPASS mode. Matched by basename
                (case-insensitive). Defaults to `DEFAULT_DANGEROUS_FILES`.
                Pass a custom list to fully replace the defaults, or `[]`
                to disable the filename check.
            dangerous_directories (`list[str]`, optional):
                Sensitive directories that require explicit user
                confirmation. Matched when any path segment equals an
                entry (case-insensitive). Defaults to
                `DEFAULT_DANGEROUS_DIRECTORIES`. Pass a custom list to
                fully replace the defaults, or `[]` to disable the
                directory check.
            cwd (`str | os.PathLike[str] | None`, optional):
                The working directory used when executing bash commands.
        """

        super().__init__(middlewares=middlewares)
        self._bash_parser = BashCommandParser()

        self.dangerous_files = list(dangerous_files)
        self.dangerous_directories = list(dangerous_directories)
        self._cwd = os.fspath(cwd) if cwd is not None else None
        self._backend = backend or LocalBackend()
        self._storage = storage
        self._user_id = user_id
        self._session_id = session_id
        self._agent_id = agent_id
        self._background_task_manager = background_task_manager

    def configure_secret_context(
        self,
        *,
        storage: "StorageBase",
        user_id: str,
        session_id: str,
        agent_id: str | None = None,
        background_task_manager: "BackgroundTaskManager | None" = None,
    ) -> "Bash":
        """Attach session-scoped secret storage used during execution."""
        self._storage = storage
        self._user_id = user_id
        self._session_id = session_id
        self._agent_id = agent_id
        self._background_task_manager = background_task_manager
        return self

    async def _resolve_cwd(self, workdir: str | None) -> str | None:
        base_cwd = self._cwd or await self._backend.getcwd()
        if workdir is None:
            return base_cwd
        if self._backend.isabs(workdir):
            return self._backend.normpath(workdir)
        if base_cwd is None:
            return self._backend.normpath(workdir)
        return self._backend.abspath(workdir, cwd=base_cwd)

    async def _get_secret_env(self) -> dict[str, str]:
        if (
            self._storage is None
            or not self._user_id
            or not self._session_id
        ):
            return {}
        return await get_session_secret_env_map(
            self._storage,
            user_id=self._user_id,
            session_id=self._session_id,
        )

    def _render_text_output(
        self,
        stdout: bytes,
        stderr: bytes,
        *,
        secret_values: list[str],
    ) -> str:
        stdout_text = _normalize_newlines(
            stdout.decode("utf-8", errors="replace"),
        )
        stderr_text = _normalize_newlines(
            stderr.decode("utf-8", errors="replace"),
        )
        if secret_values:
            stdout_text = redact_text_by_secret_values(stdout_text, secret_values)
            stderr_text = redact_text_by_secret_values(stderr_text, secret_values)

        body = stdout_text
        if stderr_text:
            if body and not body.endswith("\n"):
                body += "\n"
            body += "[stderr]\n" + stderr_text
        return body.rstrip("\n") or "(no output)"

    def _truncate_output(self, text: str, limit: int = 30000) -> str:
        if len(text) <= limit:
            return text
        return (
            "... (output truncated)\n"
            + text[-limit:]
        )

    def _render_result_text(
        self,
        *,
        stdout: bytes,
        stderr: bytes,
        secret_values: list[str],
        exit_code: int | None,
        timeout_ms: int | None = None,
        promoted_job_id: str | None = None,
    ) -> str:
        body = self._render_text_output(
            stdout,
            stderr,
            secret_values=secret_values,
        )
        lines = [body]
        if promoted_job_id is not None and timeout_ms is not None:
            lines.append(
                "[still running after "
                f"{timeout_ms}ms; moved to background job {promoted_job_id}]",
            )
        elif timeout_ms is not None:
            lines.append(f"[timed out after {timeout_ms}ms]")
        if promoted_job_id is None or exit_code is not None:
            lines.append(
                "[exit code: null]"
                if exit_code is None
                else f"[exit code: {exit_code}]",
            )
        return self._truncate_output("\n".join(lines))

    async def check_read_only(
        self,
        tool_input: dict[str, Any],
    ) -> bool:
        """Decide whether this specific bash invocation is read-only.

        Inspects the command and returns ``True`` for known-safe read-only
        commands (e.g. ``ls``, ``cat``, ``grep``, ``git status``). The
        static :attr:`is_read_only` class attribute is ``False`` because
        Bash can execute arbitrary commands; this method overrides that
        with a per-invocation answer.
        """
        command = tool_input.get("command", "")
        if not command:
            return self.is_read_only
        return self._bash_parser.is_read_only_command(command)

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for bash command execution.

        This method implements Bash-specific permission checks:

        0. Injection risk check (bypass-immune safety ASK if command
           contains dynamic expansion like ``$(...)`` or ``<(...)``)
        1. Read-only command check — auto-ALLOW in **every mode**
           (including DEFAULT) for known-safe read-only commands
           (``ls``, ``pwd``, ``git status``, ``cat``, etc.). This is
           the static counterpart to :meth:`check_read_only`.
        2. Dangerous command pattern check (bypass-immune safety ASK)
        3. Sed in-place constraint check (bypass-immune safety ASK)
        4. Dangerous path check for config files (bypass-immune safety
           ASK)
        5. Dangerous removal path check for system dirs (bypass-immune
           safety ASK)
        6. ACCEPT_EDITS auto-allow for ``mkdir``/``touch``/``rm``/
           ``rmdir``/``mv``/``cp``/``sed`` — only when **every**
           target path resolves inside a working directory
        7. PASSTHROUGH (engine continues with rule matching)

        "Bypass-immune" decisions set
        :attr:`PermissionDecision.bypass_immune` so they cannot be
        silenced by allow rules in DEFAULT mode. In BYPASS mode all
        bypass-immune ASKs are intentionally skipped — see
        :attr:`PermissionMode.BYPASS`.

        Args:
            tool_input (`dict[str, Any]`):
                The tool input containing "command" key
            context (`PermissionContext`):
                The permission context with mode and rules

        Returns:
            `PermissionDecision`:
                ALLOW for safe operations, ASK for dangerous operations,
                PASSTHROUGH to let Engine continue with rule matching
        """

        command = tool_input.get("command", "")
        if not command:
            return PermissionDecision(
                behavior=PermissionBehavior.PASSTHROUGH,
                message="Empty command",
            )

        # 0. Injection check: detect dynamic shell structures that cannot be
        # statically analyzed (command substitution, process substitution,
        # control flow, etc.). Must run before read-only check so that
        # `$(rm -rf /)` inside an otherwise-safe command is caught.
        injection_reason = self._bash_parser.check_injection_risk(command)
        if injection_reason:
            return PermissionDecision(
                behavior=PermissionBehavior.ASK,
                message=f"Permission required: {injection_reason}",
                decision_reason="Safety check: command contains dynamic "
                "expansion that cannot be statically analyzed",
                bypass_immune=True,
            )

        # 1. Check if command is read-only (auto-allow)
        if self._bash_parser.is_read_only_command(command):
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message="Permission granted for read-only command",
                decision_reason="Read-only command is allowed",
            )

        # 2. Check for dangerous commands (safety check, bypass-immune)
        dangerous_pattern = self._bash_parser.check_dangerous_command(command)
        if dangerous_pattern:
            return PermissionDecision(
                behavior=PermissionBehavior.ASK,
                message=f"Permission required: Command contains dangerous "
                f"pattern: {dangerous_pattern}",
                decision_reason="Safety check: dangerous command pattern "
                "detected",
                bypass_immune=True,
            )

        # 3. Check for sed constraints (safety check, bypass-immune)
        sed_error = self._bash_parser.check_sed_constraints(
            command,
            self.dangerous_files,
        )
        if sed_error:
            return PermissionDecision(
                behavior=PermissionBehavior.ASK,
                message=f"Permission required: {sed_error}",
                decision_reason="Safety check: sed in-place modification "
                "of dangerous file",
                bypass_immune=True,
            )

        # 4. Check for dangerous paths in sensitive config files/dirs
        # (safety check, bypass-immune)
        dangerous_paths = self._extract_dangerous_paths_from_bash(command)
        if dangerous_paths:
            paths_str = ", ".join(dangerous_paths)
            return PermissionDecision(
                behavior=PermissionBehavior.ASK,
                message=f"Permission required: Bash command operates on "
                f"sensitive paths: {paths_str}",
                decision_reason="Safety check: dangerous file or "
                "directory in bash command",
                bypass_immune=True,
            )

        # 5. Check for dangerous removal paths: rm/rmdir targeting system
        # critical directories like /, /usr, /etc, ~ (bypass-immune).
        # Checked separately from step 4 because these paths are not in the
        # dangerous_files/directories lists — they are system-level paths
        # that should never be removed regardless of user configuration.
        removal_path = self._check_dangerous_removal_path(command)
        if removal_path:
            return PermissionDecision(
                behavior=PermissionBehavior.ASK,
                message=f"Dangerous removal operation detected: "
                f"'{removal_path}'\n\nThis command would remove a critical "
                f"system directory. This requires explicit approval and "
                f"cannot be auto-allowed by permission rules.",
                decision_reason="Safety check: dangerous removal of "
                "critical system path",
                bypass_immune=True,
            )

        # 6. ACCEPT_EDITS auto-allow for filesystem commands whose targets
        # all live inside a working directory. Mirrors Write/Edit's strict
        # working-directory check — we never auto-allow a bash command that
        # would touch a path outside the configured working set (e.g.
        # ``cp /etc/hosts /tmp/x`` must not pass even though ``cp`` is in
        # the auto-allow list).
        if context.mode == PermissionMode.ACCEPT_EDITS:
            filesystem_commands = {
                "mkdir",
                "touch",
                "rm",
                "rmdir",
                "mv",
                "cp",
                "sed",
            }
            base_command = (
                command.strip().split()[0] if command.strip() else ""
            )

            if base_command in filesystem_commands:
                # Collect every target path: file arguments AND output
                # redirections. ``extract_file_paths`` includes both.
                target_paths = [
                    path
                    for _cmd, path in self._bash_parser.extract_file_paths(
                        command,
                    )
                ]
                # Conservative: only auto-allow when we extracted at least
                # one target AND every target resolves inside a working
                # directory. An empty list means the parser found nothing
                # actionable (or the command has no args) — in that case
                # we fall through to PASSTHROUGH rather than blindly
                # allowing.
                if target_paths and all(
                    self._path_in_allowed_working_path(path, context)
                    for path in target_paths
                ):
                    return PermissionDecision(
                        behavior=PermissionBehavior.ALLOW,
                        message=f"Permission granted for '{base_command}' "
                        f"command (accept edits mode - filesystem command, "
                        f"all targets in working directory)",
                        decision_reason=(
                            f"Filesystem command '{base_command}' is "
                            f"auto-allowed in accept edits mode because "
                            f"all target paths are within a working "
                            f"directory"
                        ),
                    )

        # 7. Passthrough to let Engine continue with rule matching
        return PermissionDecision(
            behavior=PermissionBehavior.PASSTHROUGH,
            message=f"Execute bash command: {command}",
        )

    def match_rule(
        self,
        rule_content: str | None,
        tool_input: dict[str, Any],
    ) -> bool:
        r"""Match Bash command using regex-based wildcard matching.

        Implements wildcard matching with escape sequences:
        - Supports \* for literal asterisk and \\ for literal backslash
        - Special optimization: "git *" matches both "git" and "git add"
        - Prefix pattern (e.g., "git:*"): matches commands starting with "git "
        - Wildcard pattern: converts to regex with proper escape handling
        - Substring pattern: exact substring matching
        - If rule_content is None, matches all invocations
         (tool-name-level rule)

        Args:
            rule_content: The command pattern to match, or None to match all
            tool_input: Must contain a "command" key with the command string

        Returns:
            True if pattern matches the command
        """
        # None = tool-name-level rule, matches everything
        if rule_content is None:
            return True

        command = tool_input.get("command", "")

        # Check if pattern is a prefix pattern (ends with :*)
        if rule_content.endswith(":*"):
            prefix = rule_content[:-2].strip()
            return command.startswith(prefix + " ") or command == prefix

        # Check if pattern has unescaped wildcards
        def has_wildcards(pattern: str) -> bool:
            """Check if pattern contains unescaped * wildcards."""
            i = 0
            while i < len(pattern):
                if pattern[i] == "\\":
                    i += 2  # Skip escaped character
                elif pattern[i] == "*":
                    return True
                else:
                    i += 1
            return False

        if not has_wildcards(rule_content):
            # No wildcards, but may have escape sequences
            # Convert escape sequences for matching
            pattern = rule_content
            pattern = pattern.replace("\\\\", "\x00BACKSLASH\x00")
            pattern = pattern.replace("\\*", "*")
            pattern = pattern.replace("\x00BACKSLASH\x00", "\\")
            # Use substring matching with converted pattern
            return pattern in command

        # Convert wildcard pattern to regex with escape handling
        # Use placeholders for escaped sequences
        ESCAPED_STAR = "\x00ESCAPED_STAR\x00"
        ESCAPED_BACKSLASH = "\x00ESCAPED_BACKSLASH\x00"

        pattern = rule_content
        # Replace \\ with placeholder
        pattern = pattern.replace("\\\\", ESCAPED_BACKSLASH)
        # Replace \* with placeholder
        pattern = pattern.replace("\\*", ESCAPED_STAR)

        # Manually escape regex special characters (except *)
        # Don't use re.escape() as it escapes spaces too
        special_chars = r".^$+?{}[]|()"
        for char in special_chars:
            pattern = pattern.replace(char, "\\" + char)

        # Convert * to regex .* (match any characters)
        pattern = pattern.replace("*", ".*")

        # Restore escaped sequences
        pattern = pattern.replace(ESCAPED_STAR, r"\*")
        pattern = pattern.replace(ESCAPED_BACKSLASH, r"\\")

        # Special optimization: "git *" should match both "git" and "git add"
        # Pattern: if ends with .*, make it optional
        if pattern.endswith(".*"):
            base_pattern = pattern[:-2]  # Remove .*
            # Try exact match first (handles trailing space)
            base_pattern = base_pattern.rstrip()
            if re.fullmatch(base_pattern, command):
                return True

        # Full regex match
        try:
            return bool(re.fullmatch(pattern, command))
        except re.error:
            # Invalid regex, fall back to substring matching
            return rule_content.replace("*", "") in command

    def generate_suggestions(
        self,
        tool_input: dict[str, Any],
    ) -> List["PermissionRule"]:
        """Generate suggested permission rules for Bash commands.

        Generates prefix rules based on command + subcommand (two words).
        For example, "git commit -m 'xxx'" generates "git commit:*".

        Args:
            tool_input (`dict[str, Any]`):
                The tool input data containing "command" key

        Returns:
            `List[PermissionRule]`:
                List of suggested permission rules based on command prefixes
        """

        command = tool_input.get("command", "")
        if not command:
            return []

        # Use bash parser to extract command prefixes
        prefixes = self._bash_parser.extract_command_prefixes(
            command,
            max_prefixes=5,
        )

        if not prefixes:
            # Cannot extract any prefix, return empty
            return []

        # Generate rules for each prefix
        rules = []
        for prefix in prefixes:
            rules.append(
                PermissionRule(
                    tool_name="Bash",
                    rule_content=f"{prefix}:*",
                    behavior=PermissionBehavior.ALLOW,
                    source="suggested",
                ),
            )

        return rules

    def _extract_dangerous_paths_from_bash(
        self,
        command: str,
    ) -> list[str]:
        """Extract dangerous paths from a bash command using tree-sitter.

        Checks for dangerous paths in:
        - File-manipulating commands (rm, mv, cp, chmod, chown, sed, touch)
        - Output redirections (>, >>)

        Args:
            command (`str`):
                The bash command string

        Returns:
            `list[str]`:
                List of dangerous paths found in the command
        """
        dangerous_paths = []

        # Use tree-sitter to extract file paths
        file_paths = self._bash_parser.extract_file_paths(command)

        for _cmd_name, path in file_paths:
            if self._is_dangerous_path(path):
                dangerous_paths.append(path)

        return dangerous_paths

    def _check_dangerous_removal_path(self, command: str) -> str | None:
        """Check if an rm/rmdir command targets a critical system path.

        Detects commands like `rm -rf /`, `rm -rf /usr`, `rmdir ~` that
        would destroy critical system directories. Unlike _is_dangerous_path
        (which checks against a configurable list of sensitive config files),
        this checks against a fixed set of system-level paths that must
        never be removed regardless of user configuration.

        Dangerous paths are:
        - Root directory (/)
        - Home directory (~)
        - Wildcard alone (*) or as dir/* (removes everything)
        - Direct children of root (/usr, /etc, /tmp, /var, etc.)

        Args:
            command (`str`):
                The bash command string

        Returns:
            `str | None`:
                The dangerous path if found, None otherwise
        """
        tokens = command.strip().split()
        if not tokens:
            return None

        # Find rm or rmdir subcommands (handle compound commands)
        try:
            tree = self._bash_parser.parser.parse(bytes(command, "utf8"))
            subcommands = self._bash_parser.split_compound_command(
                tree.root_node,
                command,
            )
        except Exception:
            subcommands = [command]

        # Check each subcommand for rm/rmdir
        for subcmd in subcommands:
            subcmd_tokens = subcmd.strip().split()
            if not subcmd_tokens:
                continue
            base = subcmd_tokens[0]
            if base not in ("rm", "rmdir"):
                continue

            # Collect non-flag arguments as potential paths
            i = 1
            while i < len(subcmd_tokens):
                tok = subcmd_tokens[i]
                # Skip flags
                if tok.startswith("-"):
                    i += 1
                    continue
                path = tok.strip("'\"")
                if self._is_dangerous_removal_path(path):
                    return path
                i += 1

        return None

    def _is_dangerous_removal_path(self, path: str) -> bool:
        """Check if a path is a critical system directory that must not be
        removed.

        Args:
            path (`str`):
                The path to check (may be relative, absolute, or contain ~)

        Returns:
            `bool`:
                True if removing this path would be catastrophic
        """

        # Bare wildcard
        if path in ("*", "./*", "/"):
            return True
        # Ends with /* — removes everything in a directory
        if path.endswith("/*") or path.endswith("\\*"):
            return True

        # Expand tilde and resolve to absolute path
        expanded = os.path.expanduser(path)
        # Don't resolve symlinks — /tmp is a symlink on macOS but is
        # still a root-child and should be flagged
        abs_path = os.path.normpath(os.path.abspath(expanded))

        # Home directory
        home = os.path.expanduser("~")
        if abs_path == home:
            return True

        # Root itself
        if abs_path == "/":
            return True

        # Direct children of root: /usr, /etc, /tmp, /var, /bin, etc.
        parent = os.path.dirname(abs_path)
        if parent == "/":
            return True

        return False

    async def call(  # type: ignore[override]
        self,
        command: str,
        description: str = "",
        timeout: int = 120000,
        workdir: str | None = None,
        run_in_background: bool = False,
    ) -> AsyncGenerator[ToolChunk, None]:
        """Execute the bash and return the output.

        Args:
            command: The bash command to execute.
            description: Optional description of what the command does.
            timeout: Timeout in milliseconds (default: 120000, max: 600000).

        Yields:
            ToolChunk: The tool execution result with stdout/stderr content.
        """

        if not command.strip():
            yield ToolChunk(
                content=[
                    TextBlock(
                        text="Error: invalid command: expected a non-empty string",
                    ),
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )
            return
        if not description.strip():
            yield ToolChunk(
                content=[
                    TextBlock(
                        text=(
                            "Error: invalid description: expected a "
                            "non-empty string"
                        ),
                    ),
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )
            return

        timeout_ms = min(timeout, 600000)
        timeout_sec = timeout_ms / 1000.0
        session_secret_env = await self._get_secret_env()
        secret_values = list(session_secret_env.values())
        resolved_cwd = await self._resolve_cwd(workdir)

        try:
            handle = await self._backend.start_shell(
                ["bash", "-lc", command],
                cwd=resolved_cwd,
                env=session_secret_env or None,
            )

            if run_in_background:
                if (
                    self._background_task_manager is None
                    or self._user_id is None
                    or self._session_id is None
                ):
                    yield ToolChunk(
                        content=[
                            TextBlock(
                                text=(
                                    "Error: background jobs unavailable: "
                                    "job tools are not configured"
                                ),
                            ),
                        ],
                        state=ToolResultState.ERROR,
                        is_last=True,
                    )
                    return
                job_id = await self._background_task_manager.adopt_shell_handle(
                    handle=handle,
                    session_id=self._session_id,
                    agent_id=self._agent_id or self.name,
                    user_id=self._user_id,
                    title=command,
                    cwd=resolved_cwd,
                    notify_on_completion=True,
                    secret_values=secret_values,
                )
                yield ToolChunk(
                    content=[
                        TextBlock(
                            text=f"started background job {job_id}",
                        ),
                    ],
                    state=ToolResultState.SUCCESS,
                    is_last=True,
                )
                return

            result = await handle.wait(timeout=timeout_sec)
            if result is None:
                snapshot = handle.snapshot()
                if (
                    self._background_task_manager is not None
                    and self._user_id is not None
                    and self._session_id is not None
                ):
                    job_id = await self._background_task_manager.adopt_shell_handle(
                        handle=handle,
                        session_id=self._session_id,
                        agent_id=self._agent_id or self.name,
                        user_id=self._user_id,
                        title=command,
                        cwd=resolved_cwd,
                        notify_on_completion=True,
                        initial_stdout_cursor=len(snapshot.stdout),
                        initial_stderr_cursor=len(snapshot.stderr),
                        secret_values=secret_values,
                    )
                    text = self._render_result_text(
                        stdout=snapshot.stdout,
                        stderr=snapshot.stderr,
                        secret_values=secret_values,
                        exit_code=None,
                        timeout_ms=timeout_ms,
                        promoted_job_id=job_id,
                    )
                    yield ToolChunk(
                        content=[TextBlock(text=text)],
                        state=ToolResultState.SUCCESS,
                        is_last=True,
                    )
                    return

                await handle.terminate()
                text = self._render_result_text(
                    stdout=snapshot.stdout,
                    stderr=snapshot.stderr,
                    secret_values=secret_values,
                    exit_code=None,
                    timeout_ms=timeout_ms,
                )
                yield ToolChunk(
                    content=[TextBlock(text=text)],
                    state=ToolResultState.SUCCESS,
                    is_last=True,
                )
                return

            text = self._render_result_text(
                stdout=result.stdout,
                stderr=result.stderr,
                secret_values=secret_values,
                exit_code=result.exit_code,
            )
            yield ToolChunk(
                content=[TextBlock(text=text)],
                state=ToolResultState.SUCCESS,
                is_last=True,
            )

        except Exception as e:
            error_msg = f"Error: {str(e)}"
            yield ToolChunk(
                content=[TextBlock(text=error_msg)],
                state=ToolResultState.ERROR,
                is_last=True,
            )
