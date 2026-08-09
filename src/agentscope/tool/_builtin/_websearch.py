# -*- coding: utf-8 -*-
"""Builtin web search tool backed by Exa via mcporter."""

import asyncio
import json
from typing import Any

from ...message import TextBlock, ToolResultState
from ...permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from .._base import ToolBase
from .._response import ToolChunk
from ._backend import BackendBase

_DEFAULT_TIMEOUT_SECONDS = 45.0
_DEFAULT_NUM_RESULTS = 5
_MAX_NUM_RESULTS = 10
_EXA_MCP_URL = "https://mcp.exa.ai/mcp"


async def _run_local_command(
    *args: str,
    timeout: float,
) -> tuple[int, bytes, bytes]:
    """Run one local command and return exit code, stdout, and stderr."""
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        raise

    return process.returncode or 0, stdout, stderr


def _normalize_url(url: str) -> str:
    """Normalize URLs for stable equality checks."""
    return url.strip().rstrip("/").casefold()


def _resolve_exa_server_name(output: str) -> str | None:
    """Resolve the actual configured mcporter server name for Exa."""
    payload = json.loads(output)
    if not isinstance(payload, dict) or not isinstance(payload.get("servers"), list):
        raise ValueError("mcporter JSON is missing the servers list")

    normalized_exa_url = _normalize_url(_EXA_MCP_URL)
    named_match: str | None = None
    for server in payload["servers"]:
        if not isinstance(server, dict):
            continue
        name = server.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        stripped_name = name.strip()
        base_url = server.get("baseUrl")
        if isinstance(base_url, str) and _normalize_url(base_url) == normalized_exa_url:
            return stripped_name
        if stripped_name.casefold() == "exa":
            named_match = stripped_name

    return named_match


async def _ensure_exa_mcp_configured() -> tuple[str | None, str | None]:
    """Ensure the Exa MCP entry exists and return its actual server name."""
    try:
        exit_code, stdout, stderr = await _run_local_command(
            "mcporter",
            "config",
            "list",
            "--json",
            timeout=5,
        )
    except FileNotFoundError:
        return None, (
            "the local Exa search backend is not available on the host."
        )
    except asyncio.TimeoutError:
        return None, "initializing the local Exa search backend timed out"

    if exit_code == 0:
        try:
            exa_server_name = _resolve_exa_server_name(
                stdout.decode("utf-8", errors="replace"),
            )
            if exa_server_name is not None:
                return exa_server_name, None
        except ValueError:
            pass

    try:
        exit_code, _stdout, stderr = await _run_local_command(
            "mcporter",
            "config",
            "add",
            "exa",
            _EXA_MCP_URL,
            "--scope",
            "home",
            timeout=10,
        )
    except FileNotFoundError:
        return None, (
            "the local Exa search backend is not available on the host."
        )
    except asyncio.TimeoutError:
        return None, "initializing the local Exa search backend timed out"

    if exit_code != 0:
        error_text = stderr.decode("utf-8", errors="replace").strip()
        if not error_text:
            error_text = "failed to initialize the local Exa search backend"
        return None, error_text

    return "exa", None


class WebSearch(ToolBase):
    """Search the web through Exa."""

    name: str = "WebSearch"
    description: str = """Searches the public web and returns search results.

This tool uses Exa for web search.

Usage:
- Provide a natural-language search query.
- Use num_results to control how many search results to request. Defaults to 5 and is capped at 10.
"""
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query.",
            },
            "num_results": {
                "type": "integer",
                "description": "Maximum number of results to request.",
                "default": _DEFAULT_NUM_RESULTS,
                "minimum": 1,
                "maximum": _MAX_NUM_RESULTS,
            },
        },
        "required": ["query"],
    }

    is_mcp: bool = False
    is_read_only: bool = True
    is_concurrency_safe: bool = True
    is_external_tool: bool = False
    is_state_injected: bool = False

    def __init__(self, backend: BackendBase | None = None) -> None:
        """Initialize the web search tool."""
        del backend

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Treat web search as a read-only action."""
        del tool_input, context
        return PermissionDecision(
            behavior=PermissionBehavior.PASSTHROUGH,
            message="Web search is read-only.",
        )

    async def call(
        self,
        query: str,
        num_results: int = _DEFAULT_NUM_RESULTS,
    ) -> ToolChunk:
        """Run Exa web search."""
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return ToolChunk(
                content=[TextBlock(text="Error: query must not be empty.")],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        normalized_num_results = max(1, min(int(num_results), _MAX_NUM_RESULTS))
        exa_server_name, setup_error = await _ensure_exa_mcp_configured()
        if setup_error is not None:
            return ToolChunk(
                content=[TextBlock(text=f"Error: Web search failed: {setup_error}")],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        try:
            exit_code, stdout, stderr = await _run_local_command(
                "mcporter",
                "call",
                f"{exa_server_name}.web_search_exa",
                f"query={normalized_query}",
                f"numResults={normalized_num_results}",
                timeout=_DEFAULT_TIMEOUT_SECONDS,
            )
        except FileNotFoundError:
            error_text = "the local Exa search backend is not available on the host"
            return ToolChunk(
                content=[TextBlock(text=f"Error: Web search failed: {error_text}")],
                state=ToolResultState.ERROR,
                is_last=True,
            )
        except asyncio.TimeoutError:
            error_text = "the local Exa search backend timed out"
            return ToolChunk(
                content=[TextBlock(text=f"Error: Web search failed: {error_text}")],
                state=ToolResultState.ERROR,
                is_last=True,
            )
        if exit_code != 0:
            error_text = stderr.decode("utf-8", errors="replace").strip()
            if not error_text:
                error_text = "unknown error"
            return ToolChunk(
                content=[TextBlock(text=f"Error: Web search failed: {error_text}")],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        content = stdout.decode("utf-8", errors="replace").strip()
        if not content:
            content = "No search results returned."

        return ToolChunk(
            content=[TextBlock(text=content)],
            state=ToolResultState.SUCCESS,
            is_last=True,
            metadata={
                "query": normalized_query,
                "num_results": normalized_num_results,
            },
        )
