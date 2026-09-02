# -*- coding: utf-8 -*-
"""ReMe-backed long-term memory middleware for AgentScope agents.

`ReMe <https://github.com/agentscope-ai/ReMe>`_ is a file-based memory
toolkit built on AgentScope. This middleware **embeds the ReMe
application in-process** (no separate service to run); a chat model for
ReMe's LLM-backed jobs is configured once at construction.

The embedded app uses an AgentScope-owned minimal configuration rather than
ReMe's standalone ``default.yaml``. It keeps the complete conversation-memory
lifecycle (write-back, dream consolidation and search) plus only the
indexing/file jobs required by those paths.

ReMe records memory by **listening to the conversation through the
``on_reply`` hook** — once a reply fully finishes, the trailing exchange
is written back via ReMe's ``auto_memory`` job, in *all* modes. If the
reply parks waiting for user confirmation or an external tool result,
write-back is deferred until the resumed reply completes. The agent never
writes memory itself; there is no manual add tool. The ``mode`` parameter
only controls **retrieval**:

- ``"static_control"`` — search ReMe when a reply starts and inject the
  retrieved memories into context before a later reasoning step (plus the
  automatic write-back). Retrieval runs concurrently with the reply, so
  injection is best-effort: a single-shot reply may finish first.
- ``"agent_control"`` — expose a ``memory_search`` tool the agent calls
  on demand (plus the automatic write-back); no auto-retrieval.
- ``"both"`` — auto-retrieve/inject *and* expose ``memory_search``.

ReMe scopes writes by ``session_id``, which is read from
``agent.state.session_id`` at hook time (not configured on the
middleware), so it always matches the agent's own session; search runs
over the whole workspace.
"""
from __future__ import annotations

import asyncio
from typing import (
    Any,
    AsyncGenerator,
    Callable,
    Literal,
    TYPE_CHECKING,
)

from pydantic import BaseModel, Field

from ..._base import MiddlewareBase
from ...._logging import logger
from ....embedding import EmbeddingModelBase
from ....message import AssistantMsg, HintBlock, Msg
from ....model import ChatModelBase
from ._config import _build_reme_app_config
from ._tools import _build_memory_tools
from ._utils import _extract_memory_texts, _extract_query_text

if TYPE_CHECKING:
    from ....agent import Agent
    from ....tool import ToolBase


# Jobs exposed by the ReMe application that this middleware drives.
_SEARCH_JOB = "search"
_AUTO_MEMORY_JOB = "auto_memory"

# ReMe component coordinates for the AgentScope-owned chat-model / embedding
# backends. An injected model bypasses ReMe building its own from credentials.
_AS_LLM_COMPONENT = "as_llm"
_AS_EMBEDDING_COMPONENT = "as_embedding"
_AS_DEFAULT = "default"

# Header rendered above retrieved memories injected into context.
_MEMORY_MSG_NAME = "memory"
_MEMORY_SECTION_HEADER = "## Relevant memories from past conversations"
_MEMORY_SECTION_INTRO = (
    "The following memories about the user may be relevant. "
    "Use them only if they are pertinent to the current request."
)
_MAIN_SESSION_CHAT_CONTEXT_KEY = "main_session_chat_context"

# Appended to the system prompt to advertise the search tool to the LLM.
_TOOL_INSTRUCTIONS = (
    "## Long-term memory\n\n"
    "You have a `memory_search` tool available. Use it whenever the "
    "current conversation may depend on a durable fact from a past "
    "session (a preference, a name, a prior decision). Recording memory "
    "is handled automatically; there is no add tool."
)


class ReMeMiddleware(MiddlewareBase):
    """AgentScope middleware that adds long-term memory backed by
    `ReMe <https://github.com/agentscope-ai/ReMe>`_.

    ReMe is embedded **in-process** (no separate service): the middleware
    instantiates a :class:`reme.ReMe` application whose LLM-backed jobs use
    the ``chat_model`` configured at construction. The app is built and
    owned by the middleware — pass ``workspace_dir`` and optionally a
    ``Parameters`` with ``chat_model`` / ``embedding_model``. Service code
    can start it eagerly through :meth:`start`; otherwise it is created on
    first use. When ``chat_model`` is omitted, the AgentScope config supplies
    the LLM from ReMe's ``LLM_*`` environment variables. Providing an
    ``embedding_model`` enables the vector store; otherwise search stays
    keyword-only.

    The model is fixed at construction (never taken from an agent), so the
    embedded app's single LLM is well-defined even when one middleware
    instance is shared across several agents. Per-conversation state — the
    ReMe ``session_id`` — is instead read live from each agent at hook
    time and never stored, keeping shared use isolated. Background
    retrieval tasks are likewise tracked per ``session_id`` so concurrent
    replies in different sessions never clobber each other's in-flight
    search.

    AgentScope middleware has no framework-managed lifecycle, so the app
    is built once and started idempotently either through :meth:`start`
    or on first use. Call :meth:`close` for explicit teardown of the
    embedded app.

    Example::

        from agentscope.middleware import ReMeMiddleware
        from agentscope.tool import Toolkit

        mw = ReMeMiddleware(
            workspace_dir=".reme",
            parameters=ReMeMiddleware.Parameters(mode="both"),
        )
        agent = Agent(
            ...,
            toolkit=Toolkit(tools=await mw.list_tools()),
            middlewares=[mw],
        )
    """

    class Parameters(BaseModel):
        """User-tunable ReMe memory parameters.

        The agent service parses this schema to render a configuration
        form. Structural wiring (``workspace_dir``) stays on the constructor.
        """

        model_config = {"arbitrary_types_allowed": True}

        chat_model: ChatModelBase | None = Field(
            default=None,
            title="Chat Model",
            description=(
                "AgentScope chat model injected into the embedded app's "
                "default-named LLM component, fixed for the lifetime of "
                "the app. When `None`, the AgentScope ReMe config supplies "
                "the LLM. Needed for `auto_memory` write-back."
            ),
        )

        embedding_model: EmbeddingModelBase | None = Field(
            default=None,
            title="Embedding Model",
            description=(
                "AgentScope embedding model injected into the embedded "
                "app's default-named embedding component, fixed for the "
                "lifetime of the app. In the AgentScope minimal config, "
                "`None` keeps search keyword-only; providing a model enables "
                "vector search."
            ),
        )

        mode: Literal["static_control", "agent_control", "both"] = Field(
            default="both",
            title="Retrieval Mode",
            description=(
                "How the agent retrieves from ReMe (write-back runs "
                "automatically in every mode). `static_control`: search and "
                "inject before each reply, no tool. `agent_control`: expose "
                "the `memory_search` tool, no auto-retrieval. `both`: "
                "auto-retrieve/inject and expose the tool."
            ),
        )

        top_k: int = Field(
            default=5,
            title="Top K",
            description=(
                "Max number of memories retrieved per search, and the "
                "default `limit` advertised by the `memory_search` tool."
            ),
        )

    def __init__(
        self,
        *,
        workspace_dir: str = ".reme",
        parameters: Parameters | None = None,
    ) -> None:
        """Initialize the ReMe middleware.

        The ReMe ``session_id`` (which scopes write-back memory cards) is
        **not** taken here — it is read from ``agent.state.session_id`` at
        hook time (every agent has one; ``AgentState`` generates it),
        mirroring :class:`TracingMiddleware`. To pin a resumable session,
        set the id on the agent (``Agent(state=AgentState(session_id=...))``).

        Args:
            workspace_dir (`str`, optional):
                ReMe workspace (vault) directory for memory cards and
                indexes. Defaults to ``".reme"``.
            parameters (`ReMeMiddleware.Parameters | None`, optional):
                User-tunable parameters (``chat_model`` / ``embedding_model``
                / ``mode`` / ``top_k``) whose schema the agent service
                renders as a configuration form. When ``None``, defaults are
                used. Providing an ``embedding_model`` also enables ReMe's
                vector store (otherwise search is keyword-only).
        """
        # Embedded ReMe application state. The app is built lazily and
        # started once with idempotent guards, either via :meth:`start`
        # or on first use. The middleware always owns the app it builds,
        # so :meth:`close` tears it down.
        self._app: Any | None = None
        self._started = False
        self._workspace_dir = workspace_dir
        self._parameters = parameters or self.Parameters()
        # In-flight background retrieval per session (started in ``on_reply``,
        # consumed/injected in ``on_reasoning``, cleaned up in ``on_reply``'s
        # finally). Keyed by ``session_id`` so one middleware shared across
        # agents keeps each session's retrieval isolated — a concurrent reply
        # in another session never clobbers this one's task.
        self._retrieval_tasks: dict[Any, asyncio.Task] = {}

    # ==================================================================
    # Embedded ReMe application lifecycle
    # ==================================================================
    def _build_app(self) -> Any:
        """Lazily build the embedded :class:`reme.ReMe` application.

        Raises:
            ImportError:
                If ``reme-ai`` is not installed.
        """
        try:
            from reme import ReMe
        except ImportError as e:  # pragma: no cover - import guard
            raise ImportError(
                "ReMeMiddleware requires the `reme-ai` package. Install "
                'it with `pip install "agentscope[memory-reme]"` (or '
                "`pip install reme-ai`).",
            ) from e

        embedding_dimensions = None
        if self._parameters.embedding_model is not None:
            embedding_dimensions = self._parameters.embedding_model.dimensions
        app_config = _build_reme_app_config(
            workspace_dir=self._workspace_dir,
            embedding_dimensions=embedding_dimensions,
        )
        return ReMe(**app_config)

    async def _ensure_started(self) -> None:
        """Build (if needed) and start the embedded app (idempotent).

        The configured ``chat_model`` / ``embedding_model`` are injected
        into the embedded app's default-named components **before** the
        one-time ``start()`` (ReMe's ``BaseAsLLM._start`` /
        ``BaseAsEmbedding._start`` skip building a model from credentials
        when ``model`` is already set). Both are fixed at construction —
        never taken from an agent — so the embedded app (which has a
        single LLM and a single embedding component) has one well-defined
        model each regardless of how many agents share this middleware.
        When ``chat_model`` is ``None``, the AgentScope config supplies its
        LLM.
        The AgentScope minimal config only creates an embedding component when
        ``embedding_model`` is provided.
        """
        if self._app is None:
            self._app = self._build_app()
        if not self._started:
            if self._parameters.chat_model is not None:
                await self._app.update_component(
                    _AS_LLM_COMPONENT,
                    _AS_DEFAULT,
                    model=self._parameters.chat_model,
                )
            if self._parameters.embedding_model is not None:
                await self._app.update_component(
                    _AS_EMBEDDING_COMPONENT,
                    _AS_DEFAULT,
                    model=self._parameters.embedding_model,
                )
            await self._app.start()
            self._started = True

    async def start(self) -> None:
        """Start the embedded ReMe app eagerly.

        This allows service code with an application lifecycle to bring up
        background watchers and cron jobs during process startup instead of
        waiting for the first memory read/write request.
        """
        await self._ensure_started()

    async def close(self) -> None:
        """Close the embedded ReMe app.

        AgentScope does not manage middleware lifecycle, so call this
        explicitly for clean teardown (e.g. on application shutdown).
        """
        if self._app is not None and self._started:
            await self._app.close()
        self._started = False

    @staticmethod
    def _session_id_of(agent: "Agent") -> str | None:
        """Read the ReMe ``session_id`` live from the agent.

        ReMe scopes write-back memory cards by ``session_id``. It is read
        from ``agent.state.session_id`` at hook time and threaded through
        per call — **never** stored on the middleware — so a single
        instance shared across agents keeps each conversation's writes
        isolated. Mirrors how :class:`TracingMiddleware` sources the
        session from the agent rather than from middleware config.
        """
        return getattr(getattr(agent, "state", None), "session_id", None)

    @staticmethod
    def _main_session_chat_context(agent: "Agent") -> dict[str, Any] | None:
        """Read the normalized main-session chat context from agent state."""
        middle_context = getattr(getattr(agent, "state", None), "middle_context", {})
        if not isinstance(middle_context, dict):
            return None
        raw = middle_context.get(_MAIN_SESSION_CHAT_CONTEXT_KEY)
        return raw if isinstance(raw, dict) else None

    @classmethod
    def _build_memory_hint(cls, agent: "Agent") -> str | None:
        """Build a multi-user-aware write-back hint for ReMe extraction."""
        context = cls._main_session_chat_context(agent)
        if not context:
            return None

        source_system_id = context.get("source_system_id") or "unknown"
        source_system_label = context.get("source_system_label") or source_system_id
        conversation_kind = context.get("conversation_kind") or "unknown"
        target_user_id = context.get("target_user_id")
        target_user_name = context.get("target_user_name")
        chat_name = context.get("chat_name")

        lines = [
            "Apply the following extraction policy to this conversation.",
            "This note is a hint for memory extraction strategy. Do not copy it verbatim into the memory note.",
            "Prefer durable, future-useful facts; skip trivial small talk.",
            f"- Source system identifier: {source_system_id!r}.",
            f"- Source system label: {source_system_label}.",
            f"- Conversation kind: {conversation_kind!r}.",
            (
                "- For any user-specific memory, preserve a stable identity "
                "anchor in the memory body itself, including at least the "
                "source system identifier and the source user id."
            ),
            (
                "- Never merge facts from different users into one generic "
                "user memory or one unattributed bullet."
            ),
            (
                "- If a fact cannot be reliably attributed to a specific user, "
                "do not write it as a user memory. Either record it as "
                "shared conversation context/group context, or omit it."
            ),
            (
                "- Keep source identifiers in the memory content itself, not "
                "only in the filename or description."
            ),
        ]

        if conversation_kind == "private" and target_user_id:
            lines.append(
                f"- The target user's source user id is {target_user_id!r}.",
            )
            if target_user_name:
                lines.append(
                    f'- The target user\'s display name is "{target_user_name}".',
                )
            lines.append(
                "- In this private conversation, user facts should normally be "
                "bound to this target user unless the conversation clearly "
                "references someone else.",
            )
            lines.append(
                "- When recording user-specific memory, make the identity "
                "explicit in the content, for example by stating which "
                "source system and source user id the fact belongs to.",
            )
        elif conversation_kind == "group":
            lines.extend(
                [
                    "- This is a group conversation. Messages may come from "
                    "different people and each user turn is labelled with "
                    "its sender.",
                    "- Extract user memories per sender. Distinguish speakers "
                    "strictly by their sender identity and bind each fact to "
                    "the correct source user id.",
                    "- If multiple users appear in one conversation, keep "
                    "their facts in clearly separated sections or bullets "
                    "with explicit identity anchors.",
                    "- Facts about the group, channel, or shared project may "
                    "be recorded as shared context, but do not rewrite them "
                    "as if they belong to one specific user.",
                ],
            )
            if chat_name:
                lines.append(
                    f'- The current group/chat name is "{chat_name}".',
                )

        return "\n".join(lines)

    @classmethod
    def _build_tool_instructions(cls, _agent: "Agent") -> str:
        """Build dynamic memory-search instructions for the LLM."""
        lines = [
            _TOOL_INSTRUCTIONS,
            "",
            "Memory lookup rules:",
            (
                "- When searching for user-specific memory, include the source "
                "system identifier and the relevant source user id in your query."
            ),
            (
                "- Search results may contain memories about multiple users. "
                "Before using a result, verify that it belongs to the intended "
                "user in the current conversation."
            ),
            (
                "- Rely on the injected session background and sender labels to "
                "decide which user's memory you are querying."
            )
        ]

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Hook: on_reply
    # ------------------------------------------------------------------
    async def on_reply(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        session_id = self._session_id_of(agent)

        # Kick off retrieval (static_control / both only) concurrently with
        # the reply. It runs in the background while the agent ingests input
        # and starts reasoning; ``on_reasoning`` injects the result once the
        # task finishes. Completed exchanges are written back afterwards in
        # every mode.
        inputs = input_kwargs.get("inputs")
        query_text = _extract_query_text(inputs)

        # Discard any stale task left for this session (a previous turn that
        # never reached its finally is unexpected, but never leak one).
        stale = self._retrieval_tasks.pop(session_id, None)
        if stale is not None and not stale.done():
            stale.cancel()
        if self._parameters.mode != "agent_control" and query_text:
            self._retrieval_tasks[session_id] = asyncio.create_task(
                self._search(query_text),
            )

        try:
            async for item in next_handler(**input_kwargs):
                yield item
        finally:
            # Retrieval may still be running (or its hint may never have been
            # injected — e.g. a single-shot reply that finished before the
            # task did). Consume this session's task so none is orphaned.
            task = self._retrieval_tasks.pop(session_id, None)
            if task is not None and not task.done():
                task.cancel()
            if task is not None:
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            # Only write memory after a reply truly finishes. If the reply
            # parked waiting for user confirmation or an external execution
            # result, skip write-back and wait for the resumed reply to
            # complete in a later turn.
            increment = self._collect_completed_exchange(agent)
            if increment:
                await self._write_back(
                    increment,
                    session_id,
                    memory_hint=self._build_memory_hint(agent),
                )

    # ------------------------------------------------------------------
    # Hook: on_reasoning (inject retrieved memories once ready)
    # ------------------------------------------------------------------
    async def on_reasoning(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Inject background-retrieved memories before a reasoning step.

        The search started in :meth:`on_reply` runs concurrently; this hook
        polls it before each reasoning step and, once it is finished, appends
        the retrieved memories to the agent's context so the *next* model
        call sees them. This is best-effort: a single-shot reply (one model
        call) may finish before retrieval does, in which case the hint is
        skipped for that turn — the same trade-off as
        :class:`AgenticMemoryMiddleware`.
        """
        session_id = self._session_id_of(agent)
        task = self._retrieval_tasks.get(session_id)
        if task is not None and task.done():
            self._retrieval_tasks.pop(session_id, None)
            try:
                memories = task.result()
            except (asyncio.CancelledError, Exception) as e:  # noqa: BLE001
                memories = []
                logger.warning("ReMe search failed: %s", e)
            if memories:
                agent.state.context.append(
                    self._build_memory_message(memories),
                )

        async for event in next_handler(**input_kwargs):
            yield event

    # ------------------------------------------------------------------
    # Hook: on_system_prompt (advertise the search tool to the LLM)
    # ------------------------------------------------------------------
    async def on_system_prompt(
        self,
        agent: "Agent",
        current_prompt: str,
    ) -> str:
        """Append search-tool instructions to the system prompt.

        Args:
            agent (`Agent`):
                The agent whose system prompt is being transformed.
            current_prompt (`str`):
                The system prompt produced by previous middleware.

        Returns:
            `str`:
                The unchanged prompt in static-control mode, otherwise the
                prompt with the ``memory_search`` nudge appended.
        """
        if self._parameters.mode == "static_control":
            return current_prompt
        return f"{current_prompt}\n\n{self._build_tool_instructions(agent)}"

    async def list_tools(self) -> list["ToolBase"]:
        """List memory tools provided by this middleware.

        Returns:
            `list[ToolBase]`:
                The ``memory_search`` tool in agent-control modes
                (``"agent_control"`` / ``"both"``), otherwise an empty
                list. There is no add tool — writing is automatic.
        """
        if self._parameters.mode == "static_control":
            return []
        return _build_memory_tools(self)

    # ==================================================================
    # ReMe job helpers (shared by hooks and tools)
    # ==================================================================
    async def _run_job(self, name: str, **kwargs: Any) -> Any:
        """Start the embedded app (if needed) and run a ReMe job.

        Raises:
            RuntimeError:
                If ReMe reports ``success=False``.
        """
        await self._ensure_started()
        assert self._app is not None
        response = await self._app.run_job(name, **kwargs)
        if getattr(response, "success", True) is False:
            raise RuntimeError(
                f"ReMe {name!r} failed: {getattr(response, 'answer', '')}",
            )
        return response

    async def _search(
        self,
        query: str,
        *,
        limit: int | None = None,
    ) -> list[str]:
        """Search ReMe and return the retrieved memory texts."""
        response = await self._run_job(
            _SEARCH_JOB,
            query=query,
            limit=self._parameters.top_k if limit is None else limit,
        )
        return _extract_memory_texts(getattr(response, "metadata", {}))

    async def _write_back(
        self,
        messages: list[Msg],
        session_id: str | None,
        memory_hint: str | None = None,
    ) -> None:
        """Persist a completed conversation increment to ReMe.

        ``messages`` is the trailing completed exchange extracted from the
        current context: the final contiguous assistant reply followed by
        the contiguous user messages it answers. Tool calls / tool results
        embedded in that assistant message are preserved automatically.

        ``session_id`` is passed in per call (read live from the agent),
        never stored, so a shared middleware keeps conversations isolated.
        Skipped (with a warning) when no ``session_id`` is available;
        failures are logged rather than propagated so a write never blocks
        the reply.

        ``memory_hint`` is an optional extra instruction forwarded to ReMe's
        ``auto_memory`` job. It is used as a soft hint for memory extraction,
        helping the downstream writer focus on specific facts, decisions, or
        themes that should be emphasized when summarizing the conversation.
        """
        if not session_id:
            logger.warning(
                "ReMe write skipped: no session_id captured from the agent.",
            )
            return
        try:
            await self._run_job(
                _AUTO_MEMORY_JOB,
                messages=[m.model_dump(mode="json") for m in messages],
                session_id=session_id,
                memory_hint=memory_hint,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "ReMe auto_memory failed for session_id=%s: %s",
                session_id,
                e,
            )

    # ==================================================================
    # Helpers
    # ==================================================================
    @staticmethod
    def _is_reply_completed(agent: "Agent") -> bool:
        """Return whether the current reply finished without parking.

        A parked reply ends the stream early while waiting for either user
        confirmation (``ASKING``) or an external execution result
        (``SUBMITTED``). Those partial replies should not be written into
        long-term memory yet.
        """
        return not agent.state.has_awaiting_tool_calls(agent.name)

    @classmethod
    def _collect_completed_exchange(cls, agent: "Agent") -> list[Msg]:
        """Collect the trailing completed assistant/user exchange.

        The current context is the whole conversation history, so extracting
        the increment by message id is brittle once a reply can pause and
        resume across turns. Instead, walk backward from the tail and keep
        the latest contiguous assistant reply plus the contiguous user
        messages immediately before it. Synthetic memory-hint messages are
        skipped and never written back.
        """
        if not cls._is_reply_completed(agent):
            return []

        collected: list[Msg] = []
        phase: Literal["assistant", "user"] = "assistant"

        for msg in reversed(agent.state.context):
            if not isinstance(msg, Msg):
                continue
            if getattr(msg, "name", None) == _MEMORY_MSG_NAME:
                continue

            if phase == "assistant":
                if msg.role != "assistant":
                    if not collected:
                        return []
                    phase = "user"
                else:
                    collected.append(msg)
                    continue

            if phase == "user":
                if msg.role != "user":
                    break
                collected.append(msg)

        collected.reverse()
        has_user = any(msg.role == "user" for msg in collected)
        has_assistant_text = any(
            msg.role == "assistant" and msg.get_text_content()
            for msg in collected
        )
        return collected if has_user and has_assistant_text else []

    @staticmethod
    def _build_memory_message(memories: list[str]) -> Msg:
        """Format retrieved ``memories`` as a synthetic hint message.

        The context entry uses an assistant-role ``Msg`` container because
        user messages cannot carry ``HintBlock`` content. Formatters convert
        the ``HintBlock`` itself into a user message before the model call.

        Args:
            memories (`list[str]`):
                Retrieved memory texts to expose to the model.
        Returns:
            `Msg`:
                An assistant-role message containing one ``HintBlock``.
        """
        bullets = "\n".join(f"- {m}" for m in memories)
        content = (
            f"{_MEMORY_SECTION_HEADER}\n"
            f"{_MEMORY_SECTION_INTRO}\n"
            f"{bullets}"
        )
        return AssistantMsg(
            name=_MEMORY_MSG_NAME,
            content=[HintBlock(hint=content)],
        )
