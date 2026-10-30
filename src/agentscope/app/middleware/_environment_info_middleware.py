# -*- coding: utf-8 -*-
"""Middleware that injects fixed runtime environment information."""

from __future__ import annotations

from ...middleware import MiddlewareBase


class EnvironmentInfoMiddleware(MiddlewareBase):  # pylint: disable=abstract-method
    """Append stable runtime environment info to the system prompt."""

    def __init__(
        self,
        *,
        session_id: str,
        extra_info: dict[str, str] | None = None,
    ) -> None:
        self._session_id = session_id
        self._extra_info = extra_info or {}

    def _build_prompt_suffix(self) -> str:
        lines = [
            "## Session Environment",
            "",
            f"- Current session id: `{self._session_id}`",
        ]
        for key, value in self._extra_info.items():
            lines.append(f"- {key}: `{value}`")
        lines.append(
            "- Share these values explicitly when another session must send "
            "results, files, or follow-up messages back to this session."
        )
        return "\n".join(lines)

    async def on_system_prompt(  # type: ignore[override]
        self,
        agent,
        current_prompt: str,
    ) -> str:
        """Append the fixed environment section exactly once."""
        del agent
        suffix = self._build_prompt_suffix()
        if suffix in current_prompt:
            return current_prompt
        return f"{current_prompt}\n\n{suffix}"
