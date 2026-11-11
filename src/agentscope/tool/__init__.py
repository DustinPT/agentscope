# -*- coding: utf-8 -*-
"""The tool module in agentscope."""

from ._types import ToolChoice, Function, RegisteredTool
from ._response import ToolResponse, ToolChunk
from ._toolkit import Toolkit
from ._base import ToolBase, ParamsBase, ToolMiddlewareBase
from ._adapters import MCPTool, FunctionTool
from ._builtin import (
    AskUser,
    AskUserAnswer,
    AskUserMetadata,
    AskUserParams,
    RequestSecret,
    RequestSecretField,
    RequestSecretParams,
    RequestSecretResultMetadata,
    ResetTools,
    Bash,
    PowerShell,
    Edit,
    Glob,
    Grep,
    Read,
    WebFetch,
    WebSearch,
    Write,
    BackendBase,
    DirEntry,
    ExecResult,
    LocalBackend,
    ShellHandle,
    ShellSnapshot,
)
from ._task import (
    TaskUpdate,
    TaskGet,
    TaskList,
    TaskCreate,
)
from ._tool_group import ToolGroup

__all__ = [
    "AskUser",
    "AskUserAnswer",
    "AskUserMetadata",
    "AskUserParams",
    "RequestSecret",
    "RequestSecretField",
    "RequestSecretParams",
    "RequestSecretResultMetadata",
    # Basic tool related types and functions
    "ToolChoice",
    "Function",
    "ToolBase",
    "ParamsBase",
    "ToolMiddlewareBase",
    "MCPTool",
    "FunctionTool",
    "ToolGroup",
    "Toolkit",
    "ToolChunk",
    "ToolResponse",
    "RegisteredTool",
    # Builtin tools
    "BackendBase",
    "LocalBackend",
    "DirEntry",
    "ExecResult",
    "ShellHandle",
    "ShellSnapshot",
    "ResetTools",
    "Bash",
    "PowerShell",
    "Edit",
    "Glob",
    "Grep",
    "Read",
    "WebFetch",
    "WebSearch",
    "Write",
    "TaskUpdate",
    "TaskGet",
    "TaskList",
    "TaskCreate",
]
