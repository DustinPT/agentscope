# -*- coding: utf-8 -*-
"""Probe preserved reasoning behavior on DeepSeek-compatible APIs.

This script reproduces the official DeepSeek "thinking with tools" flow using
the OpenAI SDK directly. It compares two providers:

1. DeepSeek official API (`https://api.deepseek.com`)
2. OpenCode Go API (`https://opencode.ai/zen/go/v1`)

For each provider, the script runs two follow-up requests after a tool-using
turn:

- preserve: keep every previous assistant `reasoning_content`
- drop: remove previous assistant `reasoning_content`

This makes it easy to verify whether the provider rejects missing
`reasoning_content` with HTTP 400 as described in the official docs.
"""

from __future__ import annotations

import argparse
import json
import os
from copy import deepcopy
from datetime import datetime
from typing import Any

from openai import BadRequestError, OpenAI


DEEPSEEK_BASE_URL = "https://api.deepseek.com"
OPENCODE_GO_BASE_URL = "https://opencode.ai/zen/go/v1"
DEFAULT_MODEL = "deepseek-v4-flash"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_date",
            "description": "Get the current date.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get weather by city and date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "The city name.",
                    },
                    "date": {
                        "type": "string",
                        "description": "The date in YYYY-MM-DD format.",
                    },
                },
                "required": ["location", "date"],
            },
        },
    },
]


def get_date() -> str:
    """Return today's date in YYYY-MM-DD."""
    return datetime.now().strftime("%Y-%m-%d")


def get_weather(location: str, date: str) -> str:
    """Return a deterministic mocked weather result."""
    return f"{location} on {date}: Cloudy 7~13°C"


TOOL_CALL_MAP = {
    "get_date": get_date,
    "get_weather": get_weather,
}


def build_client(api_key: str, base_url: str) -> OpenAI:
    """Build an OpenAI-compatible client."""
    return OpenAI(api_key=api_key, base_url=base_url)


def tool_call_to_dict(tool_call: Any) -> dict[str, Any]:
    """Convert SDK tool call objects to plain dicts."""
    return {
        "id": tool_call.id,
        "type": tool_call.type,
        "function": {
            "name": tool_call.function.name,
            "arguments": tool_call.function.arguments,
        },
    }


def assistant_message_to_dict(message: Any) -> dict[str, Any]:
    """Convert assistant message objects to request-ready dicts."""
    payload: dict[str, Any] = {
        "role": message.role,
        "content": message.content or "",
    }

    reasoning_content = getattr(message, "reasoning_content", None)
    if reasoning_content is not None:
        payload["reasoning_content"] = reasoning_content

    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        payload["tool_calls"] = [tool_call_to_dict(item) for item in tool_calls]

    return payload


def print_assistant_step(
    provider_label: str,
    turn_label: str,
    message: dict[str, Any],
) -> None:
    """Print a compact assistant step summary."""
    reasoning = message.get("reasoning_content") or ""
    content = message.get("content") or ""
    tool_calls = message.get("tool_calls")
    print(f"[{provider_label}] {turn_label}")
    print(f"  reasoning_content={reasoning!r}")
    print(f"  content={content!r}")
    print(f"  tool_calls={tool_calls!r}")


def create_completion(
    client: OpenAI,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> Any:
    """Create a non-streaming chat completion with thinking enabled."""
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "reasoning_effort": "high",
        "extra_body": {"thinking": {"type": "enabled"}},
    }
    if tools is not None:
        kwargs["tools"] = tools
    return client.chat.completions.create(**kwargs)


def run_user_turn(
    client: OpenAI,
    model: str,
    provider_label: str,
    messages: list[dict[str, Any]],
    user_prompt: str,
    turn_label: str,
    preserve_reasoning: bool = True,
) -> list[dict[str, Any]]:
    """Run one user turn, optionally stripping preserved reasoning each call."""
    messages = deepcopy(messages)
    messages.append({"role": "user", "content": user_prompt})
    sub_turn = 1

    while True:
        if not preserve_reasoning:
            messages = strip_reasoning_content(messages)
        response = create_completion(client, model, messages, tools=TOOLS)
        assistant_message = assistant_message_to_dict(response.choices[0].message)
        messages.append(assistant_message)
        print_assistant_step(
            provider_label=provider_label,
            turn_label=f"{turn_label}.{sub_turn}",
            message=assistant_message,
        )

        tool_calls = assistant_message.get("tool_calls")
        if not tool_calls:
            break

        for tool_call in tool_calls:
            tool_name = tool_call["function"]["name"]
            tool_args = json.loads(tool_call["function"]["arguments"] or "{}")
            tool_result = TOOL_CALL_MAP[tool_name](**tool_args)
            tool_message = {
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "content": tool_result,
            }
            print(
                f"[{provider_label}] tool_result "
                f"{tool_name} -> {tool_result!r}",
            )
            messages.append(tool_message)

        sub_turn += 1

    return messages


def strip_reasoning_content(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove assistant reasoning_content fields from conversation history."""
    stripped = deepcopy(messages)
    for message in stripped:
        if message.get("role") == "assistant":
            message.pop("reasoning_content", None)
    return stripped


def run_followup_probe(
    client: OpenAI,
    model: str,
    provider_label: str,
    base_messages: list[dict[str, Any]],
    second_user_prompt: str,
    third_user_prompt: str,
    preserve_reasoning: bool,
) -> None:
    """Run strict multi-turn follow-up probes with or without passthrough."""
    mode = "preserve" if preserve_reasoning else "drop"
    messages = deepcopy(base_messages)
    prompts = [
        ("Turn2", second_user_prompt),
        ("Turn3", third_user_prompt),
    ]

    for turn_label, user_prompt in prompts:
        probe_messages = (
            messages
            if preserve_reasoning
            else strip_reasoning_content(messages)
        )
        print(
            f"[{provider_label}] {turn_label} probe mode={mode}, "
            f"assistant_reasoning_fields="
            f"{sum('reasoning_content' in item for item in probe_messages if item.get('role') == 'assistant')}",
        )

        try:
            messages = run_user_turn(
                client=client,
                model=model,
                provider_label=provider_label,
                messages=messages,
                user_prompt=user_prompt,
                turn_label=turn_label,
                preserve_reasoning=preserve_reasoning,
            )
            print(f"[{provider_label}] {turn_label}.{mode} -> SUCCESS")
        except BadRequestError as exc:
            print(
                f"[{provider_label}] {turn_label}.{mode} -> BAD_REQUEST "
                f"status={getattr(exc, 'status_code', None)}",
            )
            print(f"  error={exc}")
            if getattr(exc, "response", None) is not None:
                print(f"  body={exc.response.text}")
            break
        except Exception as exc:  # pragma: no cover - diagnostic script path
            print(
                f"[{provider_label}] {turn_label}.{mode} -> ERROR "
                f"{type(exc).__name__}: {exc}",
            )
            break


def run_provider_probe(
    provider_label: str,
    api_key: str | None,
    base_url: str,
    model: str,
    first_user_prompt: str,
    second_user_prompt: str,
    third_user_prompt: str,
) -> None:
    """Run the full preserved-reasoning probe for one provider."""
    if not api_key:
        print(f"[{provider_label}] skipped: missing API key")
        return

    print("=" * 80)
    print(f"Provider: {provider_label}")
    print(f"Base URL: {base_url}")
    print(f"Model: {model}")

    client = build_client(api_key=api_key, base_url=base_url)
    messages = run_user_turn(
        client=client,
        model=model,
        provider_label=provider_label,
        messages=[],
        user_prompt=first_user_prompt,
        turn_label="Turn1",
    )
    run_followup_probe(
        client=client,
        model=model,
        provider_label=provider_label,
        base_messages=messages,
        second_user_prompt=second_user_prompt,
        third_user_prompt=third_user_prompt,
        preserve_reasoning=True,
    )
    run_followup_probe(
        client=client,
        model=model,
        provider_label=provider_label,
        base_messages=messages,
        second_user_prompt=second_user_prompt,
        third_user_prompt=third_user_prompt,
        preserve_reasoning=False,
    )


def parse_args() -> argparse.Namespace:
    """Parse CLI args."""
    parser = argparse.ArgumentParser(
        description=(
            "Probe whether DeepSeek-compatible APIs require preserved "
            "reasoning_content after tool calling."
        ),
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Model name to test. Default: {DEFAULT_MODEL}",
    )
    parser.add_argument(
        "--deepseek-api-key",
        default=os.getenv("DEEPSEEK_API_KEY"),
        help="DeepSeek official API key. Defaults to DEEPSEEK_API_KEY.",
    )
    parser.add_argument(
        "--opencode-go-api-key",
        default=os.getenv("OPENCODE_GO_API_KEY") or os.getenv("OPENCODE_API_KEY"),
        help=(
            "OpenCode Go API key. Defaults to OPENCODE_GO_API_KEY, "
            "then OPENCODE_API_KEY."
        ),
    )
    parser.add_argument(
        "--first-user-prompt",
        default="How is the weather in Hangzhou tomorrow?",
        help="First user prompt that should trigger tool calling.",
    )
    parser.add_argument(
        "--second-user-prompt",
        default="How is the weather in Guangzhou tomorrow?",
        help="Second user prompt used for the preserved reasoning probe.",
    )
    parser.add_argument(
        "--third-user-prompt",
        default="How is the weather in Shenzhen tomorrow?",
        help="Third user prompt used for the strict multi-turn probe.",
    )
    return parser.parse_args()


def main() -> None:
    """Run the probe for official DeepSeek and OpenCode Go."""
    args = parse_args()
    run_provider_probe(
        provider_label="deepseek_official",
        api_key=args.deepseek_api_key,
        base_url=DEEPSEEK_BASE_URL,
        model=args.model,
        first_user_prompt=args.first_user_prompt,
        second_user_prompt=args.second_user_prompt,
        third_user_prompt=args.third_user_prompt,
    )
    run_provider_probe(
        provider_label="opencode_go",
        api_key=args.opencode_go_api_key,
        base_url=OPENCODE_GO_BASE_URL,
        model=args.model,
        first_user_prompt=args.first_user_prompt,
        second_user_prompt=args.second_user_prompt,
        third_user_prompt=args.third_user_prompt,
    )


if __name__ == "__main__":
    main()
