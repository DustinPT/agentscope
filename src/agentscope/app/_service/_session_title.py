# -*- coding: utf-8 -*-
"""Helpers for generating user-facing session titles."""
from pydantic import BaseModel, Field

from ...message import Msg, SystemMsg, UserMsg
from ...model import ChatModelBase

_TITLE_MAX_LENGTH = 80


class SessionTitleOutput(BaseModel):
    """Structured output schema for session title generation."""

    title: str = Field(
        min_length=1,
        max_length=_TITLE_MAX_LENGTH,
        description=(
            "A concise conversation title in the user's language. "
            "Must be plain text without quotes."
        ),
    )


def _normalize_text(value: str) -> str:
    """Collapse whitespace and trim surrounding quotes."""
    normalized = " ".join(value.split()).strip().strip("\"'`“”‘’")
    if len(normalized) <= _TITLE_MAX_LENGTH:
        return normalized
    return normalized[:_TITLE_MAX_LENGTH].rstrip()


def _render_msg(msg: Msg) -> str:
    """Convert the visible parts of a message into plain text."""
    chunks: list[str] = []
    for block in msg.content:
        if block.type == "text":
            chunks.append(block.text)
        elif block.type == "data":
            label = block.name or block.source.media_type
            chunks.append(f"[附件: {label}]")
    return _normalize_text("\n".join(chunks))


async def generate_session_title(
    model: ChatModelBase,
    *,
    first_user_msg: Msg,
    first_reply_msg: Msg,
) -> str | None:
    """Generate a session title from the first user turn and reply."""
    user_summary = _render_msg(first_user_msg)
    reply_summary = _render_msg(first_reply_msg)
    if not user_summary and not reply_summary:
        return None

    prompt = (
        "为下面这段对话生成一个简短的会话标题。\n"
        "要求：\n"
        "1. 使用用户消息的语言。\n"
        "2. 只返回适合侧边栏展示的标题，不要加引号、句号、编号或解释。\n"
        "3. 控制在 18 个字以内；如果不是中文，请尽量控制在 6 个单词以内。\n"
        "4. 标题要具体，避免“关于某某”“问题咨询”“新会话”这类泛化表达。\n\n"
        f"用户首条消息：\n{user_summary or '[无文本内容]'}\n\n"
        f"助手首轮回复摘要：\n{reply_summary or '[无文本内容]'}"
    )

    response = await model.generate_structured_output(
        messages=[
            SystemMsg(
                "system",
                "You generate concise session titles for chat conversations.",
            ),
            UserMsg("user", prompt),
        ],
        structured_model=SessionTitleOutput,
    )
    title = _normalize_text(str(response.content.get("title", "")))
    return title or None
