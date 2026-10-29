"""Inject sandbox authorization hints before the next reasoning turn."""

from __future__ import annotations

from collections import defaultdict
import re
from typing import Any, AsyncGenerator, Callable

from ...agent import Agent
from ...event import HintBlockEvent
from ...message import AssistantMsg, HintBlock, ToolResultState
from ...middleware import MiddlewareBase
from ...tool import ToolResponse

_SANDBOX_VIOLATION_HINT_SOURCE = (
    '{"label": "System", "sublabel": "Sandbox Authorization"}'
)


class SandboxViolationHintMiddleware(MiddlewareBase):  # pylint: disable=abstract-method
    """Collect sandbox violations after tool calls and inject one hint later."""

    _MAX_ITEMS_PER_TOOL_CALL = 10
    _MAX_ITEMS_TOTAL = 100

    def __init__(self) -> None:
        self._pending_by_reply_id: dict[str, list[dict[str, Any]]] = defaultdict(list)

    async def on_acting(
        self,
        agent: Agent,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Collect one tool call's sandbox violations after execution."""
        tool_call = input_kwargs.get("tool_call")
        final_response: ToolResponse | None = None
        async for item in next_handler(**input_kwargs):
            if isinstance(item, ToolResponse):
                final_response = item
            yield item

        if tool_call is None:
            return
        if final_response is None or final_response.state == ToolResultState.SUCCESS:
            return

        consumer = getattr(agent.offloader, "consume_tool_call_violations", None)
        if consumer is None:
            return

        violations = consumer(tool_call.id)
        if not violations:
            return
        self._pending_by_reply_id[agent.state.reply_id].extend(violations)

    async def on_reasoning(
        self,
        agent: Agent,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator[Any, None]:
        """Inject pending sandbox violations into the current reply."""
        reply_id = agent.state.reply_id
        violations = self._pending_by_reply_id.pop(reply_id, [])
        if violations:
            hint = HintBlock(
                source=_SANDBOX_VIOLATION_HINT_SOURCE,
                hint=self._build_hint_content(violations),
                metadata={"kind": "sandbox_authorization"},
            )
            self._append_hint(agent, hint)
            yield HintBlockEvent(
                reply_id=reply_id,
                block_id=hint.id,
                source=hint.source,
                hint=hint.hint,
            )

        async for item in next_handler(**input_kwargs):
            yield item

    @staticmethod
    def _append_hint(agent: Agent, hint: HintBlock) -> None:
        if len(agent.state.context) > 0:
            last_msg = agent.state.context[-1]
            if (
                last_msg.role == "assistant"
                and last_msg.name == agent.name
                and last_msg.id == agent.state.reply_id
            ):
                last_msg.content.append(hint)
                return

        agent.state.context.append(
            AssistantMsg(
                id=agent.state.reply_id,
                name=agent.name,
                content=[hint],
            ),
        )

    @staticmethod
    def _build_hint_content(violations: list[dict[str, Any]]) -> str:
        visible_violations, truncated_count = (
            SandboxViolationHintMiddleware._truncate_violations(violations)
        )
        lines = [
            "<system-reminder>",
            (
                "The previous tool call attempted to access additional resources "
                "that are not currently authorized. Some of these accesses may "
                "have been optional, so the tool call may still have succeeded.\n"
            ),
        ]
        for violation in visible_violations:
            tool_name = violation.get("tool_name") or "unknown"
            process_name = violation.get("process_name") or ""
            operation = violation.get("operation") or "access"
            resource = violation.get("resource") or "unknown"
            resource_type = (
                str(violation.get("resource_type") or "resource").replace("_", " ")
            )
            tool_label = tool_name
            if process_name:
                tool_label = f"{tool_name} (process: {process_name})"
            lines.append(
                f"- {tool_label}: {operation} {resource_type} `{resource}`\n",
            )
        if truncated_count > 0:
            lines.append(
                (
                    "Additional sandbox violation records were truncated. "
                    "Showing at most 10 records per failed tool call and 100 "
                    "records total."
                ),
            )
        lines.extend(
            [
                (
                    "If you still need any of these resources to complete the "
                    "current task, call `RequestSandboxAccess` first and request "
                    "only the minimum permissions you need."
                ),
                "</system-reminder>",
            ],
        )
        return "\n".join(lines)

    @classmethod
    def _truncate_violations(
        cls,
        violations: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        """Apply per-tool-call and total caps to violation records."""
        grouped: dict[str, list[dict[str, Any]]] = {}
        order: list[str] = []
        for violation in violations:
            tool_call_id = str(violation.get("tool_call_id") or "")
            if tool_call_id not in grouped:
                grouped[tool_call_id] = []
                order.append(tool_call_id)
            grouped[tool_call_id].append(violation)

        visible: list[dict[str, Any]] = []
        truncated_count = 0
        for tool_call_id in order:
            group = grouped[tool_call_id]
            remaining = cls._MAX_ITEMS_TOTAL - len(visible)
            if remaining <= 0:
                truncated_count += len(group)
                continue
            taken = cls._truncate_tool_call_violations(
                group,
                limit=min(cls._MAX_ITEMS_PER_TOOL_CALL, remaining),
            )
            taken_count = len(taken)
            visible.extend(taken)
            truncated_count += len(group) - taken_count

        return visible, truncated_count

    @staticmethod
    def _truncate_tool_call_violations(
        violations: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        """Pick violations for one tool call with resource-type diversity."""
        if len(violations) <= limit:
            return list(violations)

        grouped: dict[str, list[dict[str, Any]]] = {}
        type_order: list[str] = []
        for violation in violations:
            resource_type = str(violation.get("resource_type") or "resource")
            if resource_type not in grouped:
                grouped[resource_type] = []
                type_order.append(resource_type)
            grouped[resource_type].append(violation)

        selected: list[dict[str, Any]] = []
        while len(selected) < limit:
            progressed = False
            for resource_type in type_order:
                items = grouped[resource_type]
                if not items:
                    continue
                next_index = SandboxViolationHintMiddleware._pick_diverse_resource_index(
                    items,
                    selected,
                    resource_type,
                )
                selected.append(items.pop(next_index))
                progressed = True
                if len(selected) >= limit:
                    break
            if not progressed:
                break

        return selected

    @staticmethod
    def _pick_diverse_resource_index(
        candidates: list[dict[str, Any]],
        selected: list[dict[str, Any]],
        resource_type: str,
    ) -> int:
        """Pick the next candidate with more diverse resource text."""
        same_type_resources = [
            str(item.get("resource") or "")
            for item in selected
            if str(item.get("resource_type") or "resource") == resource_type
        ]
        if not same_type_resources:
            return 0

        best_index = 0
        best_score: tuple[float, float, int] | None = None
        for index, candidate in enumerate(candidates):
            resource = str(candidate.get("resource") or "")
            similarity = max(
                SandboxViolationHintMiddleware._resource_similarity(
                    resource,
                    existing,
                )
                for existing in same_type_resources
            )
            # Prefer lower similarity, then shorter shared-prefix bias, then
            # earlier appearance order.
            prefix_penalty = max(
                SandboxViolationHintMiddleware._common_token_prefix_length(
                    resource,
                    existing,
                )
                for existing in same_type_resources
            )
            score = (similarity, float(prefix_penalty), float(index))
            if best_score is None or score < best_score:
                best_score = score
                best_index = index
        return best_index

    @staticmethod
    def _resource_similarity(left: str, right: str) -> float:
        """Estimate how similar two resource strings are."""
        left_tokens = SandboxViolationHintMiddleware._tokenize_resource(left)
        right_tokens = SandboxViolationHintMiddleware._tokenize_resource(right)
        if not left_tokens or not right_tokens:
            return 1.0 if left == right else 0.0

        left_set = set(left_tokens)
        right_set = set(right_tokens)
        intersection = len(left_set & right_set)
        union = len(left_set | right_set)
        jaccard = intersection / union if union else 0.0

        prefix = SandboxViolationHintMiddleware._common_token_prefix_length(
            left,
            right,
        )
        prefix_ratio = prefix / max(len(left_tokens), len(right_tokens))
        return max(jaccard, prefix_ratio)

    @staticmethod
    def _tokenize_resource(resource: str) -> list[str]:
        """Split one resource string into stable comparison tokens."""
        return [token for token in re.split(r"[/:._-]+", resource) if token]

    @staticmethod
    def _common_token_prefix_length(left: str, right: str) -> int:
        """Return the shared token-prefix length of two resource strings."""
        left_tokens = SandboxViolationHintMiddleware._tokenize_resource(left)
        right_tokens = SandboxViolationHintMiddleware._tokenize_resource(right)
        count = 0
        for left_token, right_token in zip(left_tokens, right_tokens):
            if left_token != right_token:
                break
            count += 1
        return count
