# -*- coding: utf-8 -*-
"""The asking user tool class."""
from typing import Any

from pydantic import BaseModel, Field, model_validator

from .._base import ToolBase
from ...permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)

_HEADER_MAX_CHARS = 12


class _Option(BaseModel):
    """A single selectable option within a question."""

    label: str = Field(
        description=(
            "The display text for this option that the user will see and "
            "select. Should be concise (1-5 words) and clearly describe the "
            "choice."
        ),
    )

    description: str = Field(
        description=(
            "Explanation of what this option means or what will happen if "
            "chosen. Useful for providing context about trade-offs or "
            "implications."
        ),
    )

    preview: str | None = Field(
        default=None,
        description=(
            "Optional preview content rendered when this option is focused. "
            "Use for mockups, code snippets, or visual comparisons that help "
            "users compare options. Only supported in single-select questions."
        ),
    )


class _Question(BaseModel):
    """A single question presented to the user."""

    question: str = Field(
        description=(
            "The complete question to ask the user. Should be clear, "
            "specific, and end with a question mark. "
            'Example: "Which library should we use for date formatting?" '
            "If multi_select is true, phrase it accordingly, "
            'e.g. "Which features do you want to enable?"'
        ),
    )

    header: str = Field(
        max_length=_HEADER_MAX_CHARS,
        description=(
            f"Very short label displayed as a chip/tag "
            f"(max {_HEADER_MAX_CHARS} chars). "
            'Examples: "Auth method", "Library", "Approach".'
        ),
    )

    context: str | None = Field(
        default=None,
        description=(
            "What the user should look at while answering — the draft "
            "being reviewed, the two designs being compared, the error "
            "that prompted the question. Shown above the options."
        ),
    )

    options: list[_Option] = Field(
        min_length=2,
        max_length=4,
        description=(
            "The available choices for this question. Must have 2-4 options. "
            "Each option must be distinct and mutually exclusive unless "
            "multi_select is enabled. Do not add an 'Other' option."
        ),
    )

    multi_select: bool = Field(
        default=False,
        description=(
            "Set to true to allow the user to select multiple options instead "
            "of just one. Preview is only supported when multi_select is false."
        ),
    )

    @model_validator(mode="after")
    def _validate_options(self) -> "_Question":
        """Reject ambiguous option groups."""
        labels = [option.label for option in self.options]
        if len(labels) != len(set(labels)):
            raise ValueError("option labels must be unique within a question")
        return self


class AskUserAnswer(BaseModel):
    """One question's answer in the returned metadata payload."""

    question: str = Field(description="The question this answers, verbatim.")
    selected: list[str] = Field(
        default_factory=list,
        description="Labels of the options the user chose.",
    )
    other: str | None = Field(
        default=None,
        description="Free-text response when the user answered in their own words.",
    )


class AskUserMetadata(BaseModel):
    """Metadata payload attached to the tool result."""

    answers: list[AskUserAnswer]


class AskUserParams(BaseModel):
    """Question batch accepted by the AskUser tool."""

    questions: list[_Question] = Field(
        min_length=1,
        max_length=4,
        description=(
            "Questions to ask the user (1-4 questions). Question texts must "
            "be unique; option labels must be unique within each question."
        ),
    )

    @model_validator(mode="after")
    def _validate_question_texts(self) -> "AskUserParams":
        """Reject duplicated question texts."""
        questions = [question.question for question in self.questions]
        if len(questions) != len(set(questions)):
            raise ValueError("question texts must be unique across the batch")
        return self


class AskUser(ToolBase):
    """Collect structured user choices through an external UI."""

    name: str = "AskUser"
    description: str = """Asks the user multiple-choice questions to gather information, clarify ambiguity, understand preferences, make decisions, or offer choices.

Use this tool when you need to ask the user questions during execution:
1. Gather user preferences or requirements.
2. Clarify ambiguous instructions before proceeding.
3. Get decisions on implementation choices as you work.
4. Offer the user a set of directions to take.

## Usage rules
- You may batch 1-4 questions in a single call; batch related questions together.
- Each question must have 2-4 options. Option labels must be unique within a question.
- Question texts must be unique across the batch.
- Users will always be able to select "Other" to provide free-text input - do not add an "Other" option yourself.
- Use `multi_select: true` only when the choices are genuinely not mutually exclusive.
- If you recommend a specific option, place it first and append "(Recommended)" to its label.
- Put whatever the user must look at to answer - a draft, a diff, an error - in `context` rather than in the question text.

## What comes back
`output` is written for you to read. The caller also returns the same answers in `ToolResultBlock.metadata`, shaped by `AskUserMetadata`, for programs that must branch on the choice rather than read prose."""
    input_schema: dict[str, Any] = AskUserParams.model_json_schema()
    metadata_schema: dict[str, Any] | None = (
        AskUserMetadata.model_json_schema()
    )
    is_read_only: bool = True
    is_state_injected: bool = False
    is_concurrency_safe: bool = True
    is_external_tool: bool = True

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Asking the user never requires an additional confirmation."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="The AskUser tool is always permitted.",
        )
