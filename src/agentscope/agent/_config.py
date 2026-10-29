# -*- coding: utf-8 -*-
"""The agent config classes."""

from datetime import datetime
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ..model import ChatModelBase


def _get_local_timezone() -> str:
    """Resolve the current system timezone as an IANA name when possible."""
    tz_name = os.getenv("TZ", "").strip()
    if tz_name:
        return tz_name

    try:
        from tzlocal import get_localzone

        resolved = str(get_localzone())
        if resolved:
            return resolved
    except Exception:
        pass

    tzinfo = datetime.now().astimezone().tzinfo
    key = getattr(tzinfo, "key", None)
    if isinstance(key, str) and key:
        return key

    localtime = Path("/etc/localtime")
    try:
        resolved = localtime.resolve(strict=True)
    except OSError:
        return "UTC"

    parts = resolved.as_posix().split("/zoneinfo/", 1)
    if len(parts) == 2 and parts[1]:
        return parts[1]
    return "UTC"


class SummarySchema(BaseModel):
    """The compressed memory model, used to generate summary of old memories"""

    task_overview: str = Field(
        max_length=1000,
        description=(
            "The user's core request and success criteria.\n"
            "Any clarifications or constraints they specified"
        ),
    )
    current_state: str = Field(
        max_length=1000,
        description=(
            "What has been completed so far.\n"
            "File created, modified, or analyzed (with paths if relevant).\n"
            "Key outputs or artifacts produced."
        ),
    )
    important_discoveries: str = Field(
        max_length=1200,
        description=(
            "Technical constraints or requirements uncovered.\n"
            "Decisions made and their rationale.\n"
            "Errors encountered and how they were resolved.\n"
            "What approaches were tried that didn't work (and why)"
        ),
    )
    next_steps: str = Field(
        max_length=600,
        description=(
            "Specific actions needed to complete the task.\n"
            "Any blockers or open questions to resolve.\n"
            "Priority order if multiple steps remain"
        ),
    )
    context_to_preserve: str = Field(
        max_length=1000,
        description=(
            "User preferences or style requirements.\n"
            "Domain-specific details that aren't obvious.\n"
            "Any promises made to the user"
        ),
    )
    """Whether to execute multiple tool calls in parallel within one
    reasoning step."""


class ContextConfig(BaseModel):
    """The context related configuration in AgentScope"""

    model_config = {"arbitrary_types_allowed": True}
    """Allow arbitrary types in the pydantic model."""

    trigger_ratio: float = Field(default=0.8, gt=0, le=0.9)
    """When the token exceeds this ratio of the maximum context length, the
    context will be compressed. To reserve the context for context compression,
    the maximum ratio is 0.9."""

    reserve_ratio: float = Field(default=0.1, gt=0, lt=0.9)
    """The ratio of the tokens to reserve in context compression, which should
    be smaller than the trigger ratio."""

    context_buffer_ratio: float = Field(
        title="Context Buffer",
        default=0.2,
        ge=0,
        le=1,
        description=(
            "The buffer ahead of the 'trigger_ratio', within which the agent "
            "is told that a compression is near, and can compress by itself "
            "if the compression tool is enabled. It must be smaller than the "
            "'trigger_ratio'."
        ),
    )
    """The buffer ahead of the compression threshold, e.g. with a trigger ratio
    of 0.8 and a buffer of 0.2, the context length is injected once the input
    tokens exceed 60% of the model context size."""

    compression_prompt: str = Field(
        default=(
            "<system-hint>You have been working on the task described above. "
            "Now write a continuation summary that will allow you to resume "
            "work efficiently in a future context window where the "
            "conversation history will be replaced with this summary. "
            "Your summary should be structured, concise, and actionable.\n"
            "The current time is {current_time}.\n"
            "This summary may itself be summarized again later, and the "
            "conversation history it refers to will be gone, so every "
            "reference must be self-contained — resolve anything that "
            "depends on the vanished context into an absolute, "
            "fully-qualified form:\n"
            "- Time: convert relative expressions ('today', 'now', "
            "'yesterday', 'tomorrow', 'recently') to absolute dates using "
            "the current time above; re-anchor them even if an earlier "
            "summary already wrote them relatively.\n"
            "- Names & pointers: use file paths, symbol names, PR/issue "
            "numbers, IDs, URLs, and exact commands/error strings verbatim "
            "instead of 'this file', 'the above', 'the second approach', "
            "'the 5 failing tests'.\n"
            "- In-flight work: record everything still pending, especially "
            "tools launched in the background whose results you are still "
            "waiting on — give each one's id and a short note of what it is "
            "doing — and mark each item's owner (user request vs your own "
            "decision) and status (done / pending / blocked).\n"
            "</system-hint>"
        ),
        # ``format: textarea`` is a hint for schema-driven UI renderers
        # to use a multi-line input. Plain JSON Schema doesn't natively
        # express this, so we piggy-back on ``json_schema_extra``.
        json_schema_extra={"format": "textarea"},
    )
    """The prompt used to guide the compression model to generate the
    compressed summary, which will be wrapped into a user message and
    attach to the end of the current memory."""

    summary_template: str = Field(
        default=(
            "<system-info>Here is a summary of your previous work\n"
            "# Task Overview\n"
            "{task_overview}\n\n"
            "# Current State\n"
            "{current_state}\n\n"
            "# Important Discoveries\n"
            "{important_discoveries}\n\n"
            "# Next Steps\n"
            "{next_steps}\n\n"
            "# Context to Preserve\n"
            "{context_to_preserve}"
            "</system-info>"
        ),
        json_schema_extra={"format": "textarea"},
    )
    """The string template to present the compressed summary to the agent,
    which will be formatted with the fields from the
    `compression_summary_model`."""

    summary_schema: dict = Field(
        default_factory=SummarySchema.model_json_schema,
    )
    """The structured model used to guide the agent to generate the
    structured compressed summary."""

    tool_result_limit: int = Field(
        title="Tool Result Limit",
        default=50000,
        description=(
            "The maximum length of the tool results in tokens. "
            "If exceeded, the tool result will be truncated."
        ),
    )
    """The tool result limit to avoid tool result bursting."""

    compression_fallback_to_truncation: bool = Field(
        default=True,
        description=(
            "Whether to truncate the oldest context when the compression "
            "summary cannot be generated. Turning it off raises the error "
            "instead, keeping the context intact at the cost of exceeding "
            "the context length."
        ),
    )
    """Whether to fall back to context truncation when the summary generation
    fails."""

    compression_tool_enabled: bool = Field(
        default=False,
        description=(
            "Whether to expose a context compression tool to the agent. "
            "When runtime-state injection is enabled, the agent is prompted "
            "to use the tool between tasks as the context approaches the "
            "hard compression threshold."
        ),
    )
    """Whether the agent can explicitly request context compression."""

    max_image_num: int = Field(
        title="Max Image Number",
        default=5,
        ge=0,
        description=(
            "The maximum number of images kept in the context. The oldest "
            "images exceeding the limit will be removed."
        ),
    )
    """The maximum number of images kept in the context. When the number of
    images exceeds this limit, the oldest images will be removed or offloaded
    and replaced by a reminder."""


class InjectionConfig(BaseModel):
    """The state injection related configuration in AgentScope."""

    inject_runtime_state: bool = Field(
        title="Inject Runtime State",
        description=(
            "Inject the runtime state to context, including current time,"
            "tasks state, context length, etc."
        ),
        default=True,
    )
    """Whether to inject the runtime state to context, including current time,
    tasks state, context length, etc."""

    timezone: str = Field(
        title="Timezone",
        default_factory=_get_local_timezone,
        description=(
            "The injected timezone. e.g. 'America/New_York' or "
            "'Asia/Shanghai'."
        ),
    )
    """The timezone to inject into the context."""

    time_format: str = Field(
        title="Time Format",
        default="%Y-%m-%dT%H:%M:%S",
        description=(
            "The format to inject and parse the time information, which must "
            "round-trip a full timestamp, i.e. carry the date part. A "
            "time-only format such as '%H:%M:%S' makes the parsed time fall "
            "back to year 1900, so that the time is injected in every "
            "iteration."
        ),
    )
    """The format to inject and parse the time information."""

    time_interval: float = Field(
        title="Time Interval",
        default=0.5,
        ge=0,
        description=(
            "The minimum time interval in hours from the last injection to "
            "trigger new time injection"
        ),
    )
    """The minimum elapsed time in hours from the recorded time to trigger a
    new time injection."""

    context_buffer_ratio: float | None = Field(
        title="Context Buffer",
        default=None,
        ge=0,
        le=1,
        description=(
            "Deprecated, use the 'context_buffer_ratio' of the context "
            "config instead. When provided, it overrides the one in the "
            "context config."
        ),
    )
    """Deprecated in favor of ``ContextConfig.context_buffer_ratio``."""

    tool_retries_limit: int = Field(
        title="Tool Retries Limit",
        default=3,
        ge=3,
        description=(
            "The number of consecutive failures of the same tool call that "
            "triggers the tool error injection."
        ),
    )
    """The number of consecutive failures of the same tool call that triggers
    the tool error injection."""

    tool_retries_hint: str = Field(
        title="Tool Retries Hint",
        default=(
            "The last {count} calls to '{tool_name}' with the same arguments "
            "all failed. Stop retrying the same call as-is, check the error "
            "message and try a different approach."
        ),
        description=(
            "The hint injected when the same tool call keeps failing, where "
            "the '{tool_name}' and '{count}' placeholders will be replaced "
            "by the failing tool name and the number of consecutive failures."
        ),
    )
    """The hint injected when the same tool call keeps failing."""

    template: str = Field(
        title="Template",
        default="""<system-reminder>Treat the following as the ground truth \
at this point of the conversation. Anything stated earlier is outdated, and a \
later reminder, if any, supersedes this one:
{runtime_state}
</system-reminder>""",
        description=(
            "The template to wrap the injected runtime state, where the "
            "'{runtime_state}' placeholder will be replaced by the injected "
            "fields."
        ),
    )
    """The template to wrap the injected runtime state."""

    @field_validator("template")
    @classmethod
    def _check_template(cls, value: str) -> str:
        """Ensure the template contains the runtime-state placeholder."""
        if "{runtime_state}" not in value:
            raise ValueError(
                "The injection template must contain the '{runtime_state}' "
                f"placeholder, got {value!r}.",
            )
        return value

    injection_source: str = Field(
        title="Injection Source",
        default='{"label": "System", "sublabel": "Runtime State"}',
        description=(
            "The source of the injected hint block, which is also used to "
            "identify the previous injections within the context."
        ),
    )
    """The source of the injected hint block."""

    task_tool_names: list[str] = Field(
        title="Task Tool Names",
        default_factory=lambda: [
            "TaskCreate",
            "TaskGet",
            "TaskList",
            "TaskUpdate",
        ],
        description=(
            "The names of the task related tools. Their presence in the "
            "context suppresses the tasks injection."
        ),
    )
    """The names of task-related tools that indicate task awareness."""

    extra_fields: dict[str, str] = Field(
        title="Extra Fields",
        default_factory=dict,
        description=(
            "The extra fields to inject, which will be wrapped into the "
            "'<{key}>{value}</{key}>' format."
        ),
    )
    """The user defined fields to inject."""

    emit_hint_event: bool = Field(
        title="Emit Hint Event",
        default=True,
        description=(
            "If emit the HintBlockEvent when runtime state injection happens."
        ),
    )
    """Whether to emit a HintBlockEvent when runtime state injection happens."""

class ReActConfig(BaseModel):
    """The reasoning related configuration"""

    max_iters: int = Field(
        title="Max Iterations",
        default=20,
        description="The maximum number of reasoning-acting iterations in "
        "one reply",
    )
    """The maximum number of iterations for the reasoning-acting loop."""

    stop_on_reject: bool = Field(
        title="Rejection Handling",
        default=False,
        description="Whether to stop replying when being rejected to "
        "execute tools.",
    )
    """If stop reasoning when tool call(s) are rejected. If `True`, the agent
    won't continue reasoning and wait for outside interaction from the user.
    """

    interruption_message: str = Field(
        title="Interruption Message",
        default="I notice the interruption. How can I help you?",
        description="The quick reply message when interrupted.",
    )
    """The interruption message."""

    interruption_raise_cancelled_error: bool = Field(
        title="Raise CancelledError on Interruption",
        default=False,
        description="Whether to re-raise ``asyncio.CancelledError`` after "
        "handling the interruption. When ``False``, the ``CancelledError`` "
        "is swallowed once the interruption context has been produced.",
    )
    """Whether to re-raise the ``asyncio.CancelledError`` after the
    interruption has been handled."""

    enable_tool_offload: bool = Field(
        title="Enable Tool Offload",
        default=False,
        description=(
            "Whether to offload long-running tool calls to background tasks."
        ),
    )
    """Whether long-running tool calls can be offloaded to the background."""

    enabled_builtin_tool_groups: list[
        Literal[
            "read",
            "edit",
            "schedule",
            "terminal",
            "network",
            "team",
            "agent_management",
            "session_management",
        ]
    ] = Field(
        title="Enabled Builtin Tool Groups",
        default_factory=lambda: [
            "read",
            "edit",
            "terminal",
            "network",
        ],
        description=(
            "Builtin tool groups enabled for this agent. Controls file "
            "reading, file editing, schedule management, terminal access, "
            "network search tools, team collaboration tools, agent "
            "package management, and session management tools."
        ),
    )
    """Builtin tool groups enabled for this agent."""


class ModelConfig(BaseModel):
    """The model related configuration."""

    # TODO: remove this line after PR #1564 is merged, where the ChatModel
    #  will be child class of BaseModel
    model_config = {"arbitrary_types_allowed": True}

    max_retries: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of retries on top of the initial call before falling "
            "over to the fallback model. ``0`` means call the model exactly "
            "once and immediately move to the fallback on failure. Same "
            "semantics as ``ChatModelBase.max_retries``. Defaults to 0 to "
            "avoid compounding with the model's own inner retry loop."
        ),
    )
    """Number of retries on top of the initial call before falling over to
    the fallback model. ``0`` means a single attempt with no retries.
    Mirrors the semantics of ``ChatModelBase.max_retries``."""

    fallback_model: ChatModelBase | None = Field(
        default=None,
        description="The fallback model used when the main model fails.",
    )
    """The fallback model used when the main model fails. Also supports the
    max_retries logic."""
