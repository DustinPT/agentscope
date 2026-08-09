# -*- coding: utf-8 -*-
"""Builtin web content fetching tool backed by Jina Reader."""

import asyncio
from typing import Any
import urllib.error
import urllib.request

from ...message import TextBlock, ToolResultState
from ...permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from .._base import ToolBase
from .._response import ToolChunk
from ._backend import BackendBase
from ._web_utils import normalize_public_http_url

_DEFAULT_TIMEOUT_SECONDS = 30.0
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024
_ANTIBOT_SCAN_BYTES = 4096
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36"
)


def _is_antibot_page(body: bytes) -> bool:
    """Recognize high-confidence Jina/Cloudflare challenge responses."""
    sample = body[:_ANTIBOT_SCAN_BYTES].decode(
        "utf-8",
        errors="ignore",
    ).casefold()
    jina_captcha_warning = "warning:" in sample and "requiring captcha" in sample
    challenge_structure = any(
        marker in sample
        for marker in (
            "title: just a moment...",
            "## performing security verification",
            "title: attention required! | cloudflare",
        )
    )
    cloudflare_block = "title: attention required! | cloudflare" in sample and (
        "ray id" in sample or "/cdn-cgi/challenge-platform/" in sample
    )
    return (jina_captcha_warning and challenge_structure) or cloudflare_block


def _fetch_via_jina(url: str) -> bytes:
    """Fetch one page through Jina Reader using Python's stdlib HTTP client."""
    request = urllib.request.Request(
        f"https://r.jina.ai/{url}",
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "text/plain",
        },
    )
    with urllib.request.urlopen(
        request,
        timeout=_DEFAULT_TIMEOUT_SECONDS,
    ) as response:
        body = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(body) > _MAX_RESPONSE_BYTES:
        raise ValueError(
            f"Jina Reader response exceeds {_MAX_RESPONSE_BYTES} byte limit",
        )
    if _is_antibot_page(body):
        raise RuntimeError(
            "Jina Reader returned an anti-bot verification page instead of "
            "the target content. Use a site-specific tool or browser flow.",
        )
    return body


class WebFetch(ToolBase):
    """Fetch web page content via Jina Reader."""

    name: str = "WebFetch"
    description: str = """Fetches a public web page and returns extracted content.

This tool uses Jina Reader for web page extraction.

Usage:
- Provide a public HTTP(S) URL. If the scheme is omitted, HTTPS is assumed.
- The tool rejects localhost, private IPs, and URLs with embedded credentials.
- The returned content is Jina Reader's extracted text/Markdown, which is usually easier to read than raw HTML."""
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The public HTTP(S) URL to fetch.",
            },
        },
        "required": ["url"],
    }

    is_mcp: bool = False
    is_read_only: bool = True
    is_concurrency_safe: bool = True
    is_external_tool: bool = False
    is_state_injected: bool = False

    def __init__(self, backend: BackendBase | None = None) -> None:
        """Initialize the web fetch tool."""
        del backend

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Treat network fetching as a read-only action."""
        del tool_input, context
        return PermissionDecision(
            behavior=PermissionBehavior.PASSTHROUGH,
            message="Web fetching is read-only.",
        )

    async def call(self, url: str) -> ToolChunk:
        """Fetch extracted page content from Jina Reader."""
        try:
            normalized_url = normalize_public_http_url(url)
        except ValueError as exc:
            return ToolChunk(
                content=[TextBlock(text=f"Error: {exc}")],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        try:
            body = await asyncio.to_thread(_fetch_via_jina, normalized_url)
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            TimeoutError,
            ValueError,
            RuntimeError,
        ) as exc:
            error_text = str(exc).strip() or "unknown error"
            return ToolChunk(
                content=[TextBlock(text=f"Error: Failed to fetch URL: {error_text}")],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        content = body.decode("utf-8", errors="replace").strip()
        if not content:
            content = "No content returned."

        return ToolChunk(
            content=[TextBlock(text=content)],
            state=ToolResultState.SUCCESS,
            is_last=True,
            metadata={"url": normalized_url},
        )
