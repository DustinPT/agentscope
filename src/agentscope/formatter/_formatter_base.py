# -*- coding: utf-8 -*-
"""The formatter module."""
import base64
import hashlib
import mimetypes
import os
import tempfile
from abc import abstractmethod
from copy import deepcopy
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, List, AsyncGenerator
from urllib.parse import unquote, urlparse

from pydantic import BaseModel, Field

from ..message import (
    Msg,
    DataBlock,
    HintBlock,
    TextBlock,
    ToolResultBlock,
    UserMsg,
    URLSource,
    Base64Source,
)


class FormatterBase(BaseModel):
    """The base class for formatters."""

    input_types: list[str] = Field(
        default_factory=lambda: ["text/plain"],
        description=(
            "The supported input types, aligned with the model card's "
            "``input_types`` field. Entries other than ``text/plain`` and "
            "``application/x-thinking`` are treated as media-type patterns "
            "(glob-style, e.g. ``image/*``, ``audio/mp3``) that control which "
            "``DataBlock``\\s are forwarded to the API."
        ),
    )
    """The supported input types for this formatter, aligned with the model
    card's ``input_types`` field."""

    tool_result_media_types: list[str] | None = Field(
        default=None,
        description=(
            "The media types that are allowed to remain inside a "
            "``ToolResultBlock``. When omitted, no media type is kept inside "
            "tool results unless the model card explicitly declares it."
        ),
    )
    """The media types that can remain in tool result blocks."""

    @property
    def supported_input_media_types(self) -> list[str]:
        """Derive the accepted media-type patterns from :attr:`input_types` by
        excluding ``text/plain`` and ``application/x-thinking``."""
        return [
            t
            for t in self.input_types
            if t not in ("text/plain", "application/x-thinking")
        ]

    @property
    def supported_tool_result_media_types(self) -> list[str]:
        """Return the media-type patterns accepted inside tool results."""
        source = self.tool_result_media_types or []
        return [
            t
            for t in source
            if t not in ("text/plain", "application/x-thinking")
        ]

    def supports_input_media(self, media_type: str) -> bool:
        """Return whether the formatter accepts the media type as input."""
        return any(
            fnmatch(media_type, pattern)
            for pattern in self.supported_input_media_types
        )

    def supports_tool_result_media(self, media_type: str) -> bool:
        """Return whether the formatter keeps the media type in tool results."""
        return any(
            fnmatch(media_type, pattern)
            for pattern in self.supported_tool_result_media_types
        )

    def supports_message_name(self) -> bool:
        """Whether the formatter can preserve ``Msg.name`` natively."""
        return False

    @staticmethod
    def _build_source_digest(
        source: URLSource | Base64Source,
    ) -> str:
        """Build a stable digest for a media source."""
        if isinstance(source, URLSource):
            payload = (
                f"url:{source.media_type}:{source.url}"
            ).encode("utf-8")
        else:
            payload = (
                source.media_type.encode("utf-8")
                + b"\0"
                + base64.b64decode(source.data)
            )
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _build_media_identifier(block: DataBlock) -> str:
        """Build a stable identifier for promoted multimodal content."""
        main_type = block.source.media_type.split("/")[0]
        digest = FormatterBase._build_source_digest(block.source)
        return f"{main_type}-{digest[:12]}"

    def _materialize_base64_source(
        self,
        source: Base64Source,
    ) -> str:
        """Persist base64 media to a stable cache path and return it."""
        digest = self._build_source_digest(source)
        extension = mimetypes.guess_extension(source.media_type) or ""
        cache_dir = os.path.join(
            tempfile.gettempdir(),
            "agentscope_media_cache",
        )
        os.makedirs(cache_dir, exist_ok=True)
        stable_path = os.path.join(cache_dir, f"{digest}{extension}")

        if not os.path.exists(stable_path):
            decoded_data = base64.b64decode(source.data)
            try:
                with open(stable_path, "xb") as file:
                    file.write(decoded_data)
            except FileExistsError:
                pass

        return stable_path

    @staticmethod
    def _build_markdown_link(label: str, url: str) -> str:
        """Build a markdown link for fallback text."""
        escaped_label = label.replace("\\", "\\\\").replace("]", "\\]")
        return f"[{escaped_label}]({url})"

    @staticmethod
    def _build_fallback_link_label(
        main_type: str,
        file_name: str | None,
    ) -> str:
        """Build a concise label for a fallback markdown link."""
        if file_name:
            return file_name
        return f"{main_type} file"

    def _build_data_block_fallback_text(self, block: DataBlock) -> str:
        """Convert a message data block into a markdown link."""
        source = block.source
        main_type = source.media_type.split("/")[0]

        if isinstance(source, URLSource):
            parsed = urlparse(str(source.url))
            if parsed.scheme == "file":
                local_path = unquote(parsed.path or "")
                if parsed.netloc and parsed.netloc != "localhost":
                    local_path = f"//{parsed.netloc}{local_path}"
                return self._build_markdown_link(
                    self._build_fallback_link_label(
                        main_type,
                        os.path.basename(local_path) or None,
                    ),
                    str(source.url),
                )
            return self._build_markdown_link(
                self._build_fallback_link_label(
                    main_type,
                    os.path.basename(unquote(parsed.path or "")) or None,
                ),
                str(source.url),
            )

        if isinstance(source, Base64Source):
            stable_path = self._materialize_base64_source(source)
            return self._build_markdown_link(
                self._build_fallback_link_label(
                    main_type,
                    os.path.basename(stable_path) or None,
                ),
                Path(stable_path).as_uri(),
            )

        return f"{main_type} file ({type(source).__name__})"

    def _build_tool_result_data_block_fallback_text(
        self,
        block: DataBlock,
    ) -> str:
        """Convert a tool-result data block into a textual fallback reference."""
        source = block.source
        main_type = source.media_type.split("/")[0]

        if isinstance(source, URLSource):
            parsed = urlparse(str(source.url))
            if parsed.scheme == "file":
                local_path = unquote(parsed.path or "")
                if parsed.netloc and parsed.netloc != "localhost":
                    local_path = f"//{parsed.netloc}{local_path}"
                return (
                    f"<system-reminder>A(n) {main_type} file is "
                    f"returned and saved locally at: {local_path}."
                    f"</system-reminder>"
                )
            return (
                f"<system-reminder>A(n) {main_type} file is returned "
                f"and can be accessed at the URL: {source.url}."
                f"</system-reminder>"
            )

        if isinstance(source, Base64Source):
            stable_path = self._materialize_base64_source(source)
            return (
                f"<system-reminder>A(n) {main_type} file is "
                f"returned and saved locally at: {stable_path}."
                f"</system-reminder>"
            )

        return (
            f"<system-reminder>A(n) {main_type} file is returned with "
            f"unsupported source type: {type(source).__name__}."
            f"</system-reminder>"
        )

    def _wrap_promoted_multimodal_data(
        self,
        blocks: list[TextBlock | DataBlock],
    ) -> list[TextBlock | DataBlock]:
        """Wrap promoted multimodal content with reminder markers."""
        if not blocks:
            return []

        return [
            TextBlock(
                text="<system-reminder>The multimodal data and their "
                "identifiers are listed as follows:",
            ),
            *blocks,
            TextBlock(text="</system-reminder>"),
        ]

    def _adapt_data_block_for_input(
        self,
        block: DataBlock,
    ) -> list[TextBlock | DataBlock]:
        """Adapt a standalone data block for model input."""
        if self.supports_input_media(block.source.media_type):
            return [block]

        return [TextBlock(text=self._build_data_block_fallback_text(block))]

    def _adapt_hint_block(self, block: HintBlock) -> HintBlock:
        """Adapt multimodal hint content according to model input support."""
        if isinstance(block.hint, str):
            return block

        hint_blocks: list[TextBlock | DataBlock] = []
        for sub_block in block.hint:
            if isinstance(sub_block, TextBlock):
                hint_blocks.append(sub_block)
            elif isinstance(sub_block, DataBlock):
                hint_blocks.extend(self._adapt_data_block_for_input(sub_block))

        return block.model_copy(update={"hint": hint_blocks})

    def _adapt_tool_result_block(
        self,
        block: ToolResultBlock,
    ) -> tuple[ToolResultBlock, list[TextBlock | DataBlock]]:
        """Adapt tool result multimodal content before formatter encoding."""
        output = block.output
        if isinstance(output, str):
            return block, []

        adapted_output: list[TextBlock | DataBlock] = []
        promoted_blocks: list[TextBlock | DataBlock] = []

        for out_block in output:
            if isinstance(out_block, TextBlock):
                adapted_output.append(out_block)
                continue

            media_type = out_block.source.media_type
            main_type = media_type.split("/")[0]

            if self.supports_tool_result_media(media_type):
                adapted_output.append(out_block)
                continue

            if self.supports_input_media(media_type):
                identifier = self._build_media_identifier(out_block)
                adapted_output.append(
                    TextBlock(
                        text=(
                            f"<system-reminder>A(n) {main_type} file is "
                            "returned and will be presented to you with the "
                            f"identifier [{identifier}].</system-reminder>"
                        ),
                    ),
                )
                promoted_blocks.extend(
                    [
                        TextBlock(
                            text=f"- {identifier} ({main_type} file): ",
                        ),
                        out_block,
                    ],
                )
                continue

            adapted_output.append(
                TextBlock(
                    text=self._build_tool_result_data_block_fallback_text(
                        out_block,
                    ),
                ),
            )

        return block.model_copy(update={"output": adapted_output}), promoted_blocks


    @staticmethod
    def _build_sender_xml_prefix(user_name: str) -> str:
        """Build the opening sender XML tag."""
        return f'<user_message user_name="{user_name}">'

    @staticmethod
    def _build_sender_xml_suffix() -> str:
        """Build the closing sender XML tag."""
        return "</user_message>"

    def _wrap_user_content_with_sender(
        self,
        content: str | list[TextBlock | DataBlock],
        user_name: str,
    ) -> list[TextBlock | DataBlock]:
        """Wrap user-visible content with sender XML markers."""
        prefix = self._build_sender_xml_prefix(user_name)
        suffix = self._build_sender_xml_suffix()

        if isinstance(content, str):
            return [TextBlock(text=f"{prefix}{content}{suffix}")]

        blocks = deepcopy(content)
        if not blocks:
            return [TextBlock(text=f"{prefix}{suffix}")]

        if isinstance(blocks[0], TextBlock):
            blocks[0] = blocks[0].model_copy(
                update={"text": f"{prefix}{blocks[0].text}"},
            )
        else:
            blocks.insert(0, TextBlock(text=prefix))

        if isinstance(blocks[-1], TextBlock):
            blocks[-1] = blocks[-1].model_copy(
                update={"text": f"{blocks[-1].text}{suffix}"},
            )
        else:
            blocks.append(TextBlock(text=suffix))

        return blocks

    def adapt_messages_for_model(
        self,
        msgs: list[Msg],
        *,
        group_conversation: bool = False,
        degrade_sender_identity: bool = False,
    ) -> list[Msg]:
        self.assert_list_of_msgs(msgs)

        adapted_messages: list[Msg] = []

        for msg in deepcopy(msgs):
            adapted_content: list = []
            promoted_blocks = []

            for block in msg.get_content_blocks():
                if isinstance(block, DataBlock):
                    adapted_content.extend(
                        self._adapt_data_block_for_input(block),
                    )
                    continue

                if isinstance(block, HintBlock):
                    adapted_hint = self._adapt_hint_block(block)
                    sender_name = adapted_hint.metadata.get("user_name")
                    if group_conversation and sender_name:
                        if adapted_content:
                            adapted_messages.append(
                                msg.model_copy(
                                    update={"content": adapted_content},
                                ),
                            )
                            adapted_content = []

                        hint_content = adapted_hint.hint
                        if degrade_sender_identity:
                            adapted_messages.append(
                                UserMsg(
                                    name="",
                                    content=self._wrap_user_content_with_sender(
                                        hint_content,
                                        sender_name,
                                    ),
                                    metadata=deepcopy(msg.metadata),
                                    created_at=msg.created_at,
                                    finished_at=msg.finished_at,
                                ),
                            )
                        else:
                            adapted_messages.append(
                                UserMsg(
                                    name=sender_name,
                                    content=hint_content,
                                    metadata=deepcopy(msg.metadata),
                                    created_at=msg.created_at,
                                    finished_at=msg.finished_at,
                                ),
                            )
                        continue

                    adapted_content.append(adapted_hint)
                    continue

                if isinstance(block, ToolResultBlock):
                    adapted_block, block_promoted_blocks = (
                        self._adapt_tool_result_block(block)
                    )
                    adapted_content.append(adapted_block)
                    promoted_blocks.extend(block_promoted_blocks)
                    continue

                adapted_content.append(block)

            if adapted_content:
                if (
                    group_conversation
                    and degrade_sender_identity
                    and msg.role == "user"
                    and msg.name
                ):
                    adapted_messages.append(
                        msg.model_copy(
                            update={
                                "name": "",
                                "content": self._wrap_user_content_with_sender(
                                    adapted_content,
                                    msg.name,
                                ),
                            },
                        ),
                    )
                else:
                    adapted_messages.append(
                        msg.model_copy(update={"content": adapted_content}),
                    )
            if promoted_blocks:
                adapted_messages.append(
                    UserMsg(
                        name="system-reminder",
                        content=self._wrap_promoted_multimodal_data(
                            promoted_blocks,
                        ),
                        metadata=deepcopy(msg.metadata),
                        created_at=msg.created_at,
                        finished_at=msg.finished_at,
                    ),
                )

        return adapted_messages

    @abstractmethod
    async def format(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        """Format the Msg objects to a list of dictionaries that satisfy the
        API requirements."""

    @staticmethod
    def assert_list_of_msgs(msgs: list[Msg]) -> None:
        """Assert that the input is a list of Msg objects.

        Args:
            msgs (`list[Msg]`):
                A list of Msg objects to be validated.
        """
        if not isinstance(msgs, list):
            raise TypeError("Input must be a list of Msg objects.")

        for msg in msgs:
            if not isinstance(msg, Msg):
                raise TypeError(
                    f"Expected Msg object, got {type(msg)} instead.",
                )

    def convert_tool_result_to_string(
        self,
        output: str | List[TextBlock | DataBlock],
    ) -> tuple[str, list[TextBlock | DataBlock]]:
        """Turn a tool result payload into textual fallback output.

        Any remaining ``DataBlock`` should already have been adapted by
        :meth:`adapt_messages_for_model`. This helper only converts those
        fallback data blocks into textual references so formatters can encode
        tool results as plain text when needed.

        Args:
            output (`str | List[TextBlock | DataBlock]`):
                The output of the tool response, including text and multimodal
                data like images and audio.

        Returns:
            `tuple[str, list[TextBlock | DataBlock]]`:
                A tuple containing the textual representation of the tool
                result and an empty promotion list kept for backward
                compatibility with existing formatter call sites.
        """

        if isinstance(output, str):
            return output, []

        textual_output = []

        for block in output:
            if isinstance(block, TextBlock):
                textual_output.append(block.text)

            elif isinstance(block, DataBlock):
                textual_output.append(
                    self._build_tool_result_data_block_fallback_text(block),
                )

        return "\n".join(textual_output), []

    @staticmethod
    async def _group_messages(msgs: list[Msg]) -> AsyncGenerator:
        """Group messages into tool sequences and agent messages.

        Args:
            msgs (`list[Msg]`):
                A list of Msg objects to be grouped.
        """
        group_type = None
        group = []
        for msg in msgs:
            if group_type is None:
                if msg.get_content_blocks(
                    "tool_call",
                ) or msg.get_content_blocks("tool_result"):
                    group_type = "tool_sequence"
                else:
                    group_type = "agent_message"
                group.append(msg)
                continue

            if group_type == "tool_sequence":
                if msg.has_content_blocks(
                    "tool_call",
                ) or msg.has_content_blocks("tool_result"):
                    group.append(msg)
                else:
                    yield group_type, group
                    group = [msg]
                    group_type = "agent_message"

            elif group_type == "agent_message":
                if msg.has_content_blocks(
                    "tool_call",
                ) or msg.has_content_blocks("tool_result"):
                    yield group_type, group
                    group = [msg]
                    group_type = "tool_sequence"
                else:
                    group.append(msg)

        if group_type:
            yield group_type, group
