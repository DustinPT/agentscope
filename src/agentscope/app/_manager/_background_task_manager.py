# -*- coding: utf-8 -*-
"""Generic background job registry shared by the agent service."""

from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Self

import shortuuid
from pydantic import BaseModel, Field

from agentscope._logging import logger
from agentscope._request_secret import redact_text_by_secret_values
from agentscope.app._bus_ops import deliver_to_inbox
from agentscope.message import HintBlock, TextBlock, ToolResultState
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from agentscope.tool import ToolBase, ToolChunk
from agentscope.tool._builtin._backend import ShellHandle, _normalize_newlines

CompletionNoticeFactory = Callable[["BackgroundTask"], str | None]
_TERMINAL_STATES = {"completed", "failed", "cancelled"}


def _always_allow(tool_name: str) -> PermissionDecision:
    return PermissionDecision(
        behavior=PermissionBehavior.ALLOW,
        message=f"{tool_name} is always allowed to be called.",
    )


def _normalize_output(text: str, secret_values: list[str]) -> str:
    text = _normalize_newlines(text)
    if secret_values:
        text = redact_text_by_secret_values(text, secret_values)
    return text


def _render_output(stdout: str, stderr: str) -> str:
    parts: list[str] = []
    if stdout:
        parts.append(stdout)
    if stderr:
        if parts and not parts[-1].endswith("\n"):
            parts[-1] += "\n"
        parts.append("[stderr]\n" + stderr)
    rendered = "".join(parts).rstrip("\n")
    return rendered or "(no output)"


@dataclass
class BackgroundTask:
    """One background job visible to job tools."""

    session_id: str
    agent_id: str
    user_id: str
    kind: str = "task"
    title: str = "background task"
    cwd: str | None = None
    asyncio_task: asyncio.Task[Any] | None = None
    status: str = "running"
    detail: str = "running"
    stdout: bytearray = field(default_factory=bytearray)
    stderr: bytearray = field(default_factory=bytearray)
    text_output: str = ""
    stdout_cursor: int = 0
    stderr_cursor: int = 0
    text_cursor: int = 0
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    notify_on_completion: bool = False
    completion_notice_factory: CompletionNoticeFactory | None = None
    notification_sent: bool = False
    secret_values: list[str] = field(default_factory=list)
    id: str = field(default_factory=shortuuid.uuid)

    @property
    def is_active(self) -> bool:
        return self.status not in _TERMINAL_STATES

    def status_line(self) -> str:
        if self.detail and self.detail != self.status:
            return f"[status: {self.status}, {self.detail}]"
        return f"[status: {self.status}]"

    def full_output_text(self) -> str:
        if self.text_output:
            return self.text_output.rstrip("\n") or "(no output)"
        stdout = _normalize_output(
            self.stdout.decode("utf-8", errors="replace"),
            self.secret_values,
        )
        stderr = _normalize_output(
            self.stderr.decode("utf-8", errors="replace"),
            self.secret_values,
        )
        return _render_output(stdout, stderr)

    def consume_output_text(self) -> str:
        if self.text_output:
            chunk = self.text_output[self.text_cursor :]
            self.text_cursor = len(self.text_output)
            return chunk.rstrip("\n")

        stdout_chunk = bytes(self.stdout[self.stdout_cursor :])
        stderr_chunk = bytes(self.stderr[self.stderr_cursor :])
        self.stdout_cursor = len(self.stdout)
        self.stderr_cursor = len(self.stderr)
        stdout_text = _normalize_output(
            stdout_chunk.decode("utf-8", errors="replace"),
            self.secret_values,
        )
        stderr_text = _normalize_output(
            stderr_chunk.decode("utf-8", errors="replace"),
            self.secret_values,
        )
        return _render_output(stdout_text, stderr_text)


def _default_shell_notice(task: BackgroundTask) -> str:
    summary = (
        f"background job {task.id} ({task.kind}: {task.title}) finished "
        f"{task.status_line()}."
    )
    output = task.full_output_text()
    if output == "(no output)":
        return summary
    return f"{summary} Output:\n{output}"


class _JobKillParams(BaseModel):
    job_id: str = Field(
        description="Job id returned by the tool that started the background work.",
    )
    reason: str | None = Field(
        default=None,
        description="Optional short reason, recorded in the log and forwarded to the job.",
    )


class _JobOutputParams(BaseModel):
    job_id: str = Field(
        description="Job id returned by the tool that started the background work.",
    )
    wait: bool = Field(
        default=False,
        description="Block until the job finishes. Defaults to false.",
    )
    timeout_ms: int | None = Field(
        default=None,
        gt=0,
        description="Max wait in milliseconds with wait: true.",
    )


class JobKill(ToolBase):
    """Stop a background job."""

    name: str = "job_kill"
    description: str = "Request cancellation of a running background job."
    input_schema: dict = _JobKillParams.model_json_schema()
    is_concurrency_safe: bool = True
    is_read_only: bool = False
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(
        self,
        manager: "BackgroundTaskManager",
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        self._manager = manager
        self._user_id = user_id
        self._session_id = session_id

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        _ = (tool_input, context)
        return _always_allow(self.name)

    async def __call__(
        self,
        job_id: str,
        reason: str | None = None,
    ) -> ToolChunk:
        return self._manager.kill_job(
            job_id,
            reason=reason,
            user_id=self._user_id,
            session_id=self._session_id,
        )


class JobOutput(ToolBase):
    """Read output from one background job."""

    name: str = "job_output"
    description: str = (
        "Read a background job: output since the previous read for stream "
        "jobs, or the result of a finished final-output job."
    )
    input_schema: dict = _JobOutputParams.model_json_schema()
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(
        self,
        manager: "BackgroundTaskManager",
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        self._manager = manager
        self._user_id = user_id
        self._session_id = session_id

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        _ = (tool_input, context)
        return _always_allow(self.name)

    async def __call__(
        self,
        job_id: str,
        wait: bool = False,
        timeout_ms: int | None = None,
    ) -> ToolChunk:
        return await self._manager.read_job_output(
            job_id,
            wait=wait,
            timeout_ms=timeout_ms,
            user_id=self._user_id,
            session_id=self._session_id,
        )


class JobList(ToolBase):
    """List visible background jobs."""

    name: str = "job_list"
    description: str = (
        "List your background jobs (running and finished) with their ids, "
        "kinds, and statuses."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
    }
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(
        self,
        manager: "BackgroundTaskManager",
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        self._manager = manager
        self._user_id = user_id
        self._session_id = session_id

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        _ = (tool_input, context)
        return _always_allow(self.name)

    async def __call__(self) -> ToolChunk:
        return self._manager.list_jobs(
            user_id=self._user_id,
            session_id=self._session_id,
        )


class BackgroundTaskManager:
    """Track generic background jobs and expose DSH-style job tools."""

    def __init__(
        self,
        *,
        message_bus: Any | None = None,
        max_retained_tasks: int = 128,
    ) -> None:
        self.tasks: OrderedDict[str, BackgroundTask] = OrderedDict()
        self._message_bus = message_bus
        self._max_retained_tasks = max_retained_tasks

    def _visible_tasks(
        self,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[BackgroundTask]:
        tasks = list(self.tasks.values())
        if user_id is not None:
            tasks = [task for task in tasks if task.user_id == user_id]
        if session_id is not None:
            tasks = [task for task in tasks if task.session_id == session_id]
        return tasks

    def _visible_task(
        self,
        task_id: str,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> BackgroundTask | None:
        task = self.tasks.get(task_id)
        if task is None:
            return None
        if user_id is not None and task.user_id != user_id:
            return None
        if session_id is not None and task.session_id != session_id:
            return None
        return task

    def _schedule_done_callback(self, task_id: str) -> Callable[[asyncio.Task[Any]], None]:
        def _done(asyncio_task: asyncio.Task[Any]) -> None:
            _ = asyncio_task
            asyncio.create_task(self._handle_task_done(task_id))

        return _done

    async def _handle_task_done(self, task_id: str) -> None:
        task = self.tasks.get(task_id)
        if task is None or task.asyncio_task is None:
            return

        asyncio_task = task.asyncio_task
        if task.status not in _TERMINAL_STATES:
            if asyncio_task.cancelled():
                task.status = "cancelled"
                if task.detail == "running":
                    task.detail = "cancelled"
            else:
                exc = asyncio_task.exception()
                if exc is not None:
                    task.status = "failed"
                    task.detail = str(exc)
                else:
                    task.status = "completed"
                    if task.detail == "running":
                        task.detail = "completed"

        task.finished_at = time.time()
        await self._maybe_send_completion_notice(task)
        self._prune_finished_tasks()

    def _prune_finished_tasks(self) -> None:
        finished = [
            task_id
            for task_id, task in self.tasks.items()
            if not task.is_active
        ]
        excess = len(finished) - self._max_retained_tasks
        if excess <= 0:
            return
        for task_id in finished[:excess]:
            self.tasks.pop(task_id, None)

    async def _maybe_send_completion_notice(
        self,
        task: BackgroundTask,
    ) -> None:
        if (
            self._message_bus is None
            or not task.notify_on_completion
            or task.notification_sent
            or task.completion_notice_factory is None
        ):
            return

        notice = task.completion_notice_factory(task)
        if not notice:
            task.notification_sent = True
            return

        hint = HintBlock(
            hint=f"<system-notification>{notice}</system-notification>",
            source=json.dumps(
                {
                    "label": "job_completion",
                    "sublabel": f"{task.kind} · {task.id}",
                },
                ensure_ascii=False,
            ),
        )
        logger.info(
            "Background job completed, pushing notice to inbox: job_id=%s, "
            "session_id=%s, agent_id=%s",
            task.id,
            task.session_id,
            task.agent_id,
        )
        await deliver_to_inbox(
            self._message_bus,
            user_id=task.user_id,
            session_id=task.session_id,
            agent_id=task.agent_id,
            payload=hint.model_dump(mode="json"),
        )
        task.notification_sent = True

    async def register_task(
        self,
        asyncio_task: asyncio.Task[Any],
        session_id: str,
        agent_id: str,
        user_id: str,
        *,
        kind: str = "task",
        title: str | None = None,
        cwd: str | None = None,
        notify_on_completion: bool = False,
        completion_notice_factory: CompletionNoticeFactory | None = None,
        secret_values: list[str] | None = None,
    ) -> str:
        """Register one already-running background task."""
        bg_task = BackgroundTask(
            asyncio_task=asyncio_task,
            session_id=session_id,
            agent_id=agent_id,
            user_id=user_id,
            kind=kind,
            title=title or kind,
            cwd=cwd,
            notify_on_completion=notify_on_completion,
            completion_notice_factory=completion_notice_factory,
            secret_values=list(secret_values or []),
        )
        self.tasks[bg_task.id] = bg_task
        asyncio_task.add_done_callback(self._schedule_done_callback(bg_task.id))
        logger.info(
            "Background job registered: job_id=%s, kind=%s, session_id=%s, "
            "agent_id=%s",
            bg_task.id,
            bg_task.kind,
            bg_task.session_id,
            bg_task.agent_id,
        )
        return bg_task.id

    async def adopt_shell_handle(
        self,
        *,
        handle: ShellHandle,
        session_id: str,
        agent_id: str,
        user_id: str,
        title: str,
        cwd: str | None,
        notify_on_completion: bool = True,
        initial_stdout_cursor: int = 0,
        initial_stderr_cursor: int = 0,
        secret_values: list[str] | None = None,
    ) -> str:
        """Track one already-started shell handle as a background job."""
        snapshot = handle.snapshot()
        bg_task = BackgroundTask(
            session_id=session_id,
            agent_id=agent_id,
            user_id=user_id,
            kind="bash",
            title=title,
            cwd=cwd,
            stdout=bytearray(snapshot.stdout),
            stderr=bytearray(snapshot.stderr),
            stdout_cursor=initial_stdout_cursor,
            stderr_cursor=initial_stderr_cursor,
            notify_on_completion=notify_on_completion,
            completion_notice_factory=_default_shell_notice,
            secret_values=list(secret_values or []),
        )
        self.tasks[bg_task.id] = bg_task

        async def _monitor_shell() -> None:
            try:
                while True:
                    latest = handle.snapshot()
                    bg_task.stdout = bytearray(latest.stdout)
                    bg_task.stderr = bytearray(latest.stderr)
                    result = await handle.wait(timeout=0.2)
                    if result is None:
                        continue
                    latest = handle.snapshot()
                    bg_task.stdout = bytearray(latest.stdout)
                    bg_task.stderr = bytearray(latest.stderr)
                    bg_task.status = "completed"
                    bg_task.detail = f"exit code: {result.exit_code}"
                    return
            except asyncio.CancelledError:
                await handle.terminate()
                latest = handle.snapshot()
                bg_task.stdout = bytearray(latest.stdout)
                bg_task.stderr = bytearray(latest.stderr)
                bg_task.status = "cancelled"
                bg_task.detail = "cancelled"
                raise
            except Exception as exc:  # noqa: BLE001
                bg_task.status = "failed"
                bg_task.detail = str(exc)
                self.append_text_output(bg_task.id, f"Error: {exc}")
                raise

        asyncio_task = asyncio.create_task(_monitor_shell())
        bg_task.asyncio_task = asyncio_task
        asyncio_task.add_done_callback(self._schedule_done_callback(bg_task.id))
        logger.info(
            "Background shell job adopted: job_id=%s, session_id=%s, "
            "agent_id=%s, cwd=%s",
            bg_task.id,
            session_id,
            agent_id,
            cwd,
        )
        return bg_task.id

    def append_text_output(self, task_id: str, text: str) -> bool:
        task = self.tasks.get(task_id)
        if task is None or not text:
            return False
        text = _normalize_newlines(text)
        if task.text_output and not task.text_output.endswith("\n"):
            task.text_output += "\n"
        task.text_output += text
        return True

    def list_jobs(
        self,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> ToolChunk:
        tasks = self._visible_tasks(user_id=user_id, session_id=session_id)
        if not tasks:
            return ToolChunk(
                content=[TextBlock(text="(no background jobs)")],
                state=ToolResultState.SUCCESS,
            )

        lines = []
        for task in tasks:
            lines.append(
                f"{task.id} [{task.kind}] {task.status} — {task.title}",
            )
        return ToolChunk(
            content=[TextBlock(text="\n".join(lines))],
            state=ToolResultState.SUCCESS,
        )

    async def read_job_output(
        self,
        task_id: str,
        *,
        wait: bool = False,
        timeout_ms: int | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> ToolChunk:
        task = self._visible_task(
            task_id,
            user_id=user_id,
            session_id=session_id,
        )
        if task is None:
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"JobNotFoundError: The job {task_id} does not exist.",
                    ),
                ],
                state=ToolResultState.ERROR,
            )

        if wait and task.is_active and task.asyncio_task is not None:
            try:
                if timeout_ms is None:
                    await asyncio.shield(task.asyncio_task)
                else:
                    await asyncio.wait_for(
                        asyncio.shield(task.asyncio_task),
                        timeout=timeout_ms / 1000.0,
                    )
            except asyncio.CancelledError:
                pass
            except asyncio.TimeoutError:
                pass
            except Exception:
                pass

        output = task.consume_output_text()
        if not output:
            output = "(no new output)"
        output = output.rstrip("\n")
        output = f"{output}\n{task.status_line()}"
        return ToolChunk(
            content=[TextBlock(text=output)],
            state=ToolResultState.SUCCESS,
        )

    def kill_job(
        self,
        task_id: str,
        *,
        reason: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> ToolChunk:
        task = self._visible_task(
            task_id,
            user_id=user_id,
            session_id=session_id,
        )
        if task is None:
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"JobNotFoundError: The job {task_id} does not exist.",
                    ),
                ],
                state=ToolResultState.ERROR,
            )
        if not task.is_active or task.asyncio_task is None:
            if task.status == "cancelled":
                message = f"Job {task_id} is already cancelled."
            elif task.status == "failed":
                message = f"Job {task_id} is already failed."
            else:
                message = f"Job {task_id} is already completed."
            return ToolChunk(
                content=[
                    TextBlock(
                        text=message,
                    ),
                ],
                state=ToolResultState.SUCCESS,
            )

        logger.info(
            "Background job cancelled via job_kill: job_id=%s, "
            "session_id=%s, agent_id=%s, reason=%s",
            task.id,
            task.session_id,
            task.agent_id,
            reason,
        )
        task.status = "cancelled"
        task.detail = reason or "cancelled"
        task.asyncio_task.cancel()
        return ToolChunk(
            content=[TextBlock(text=f"Job {task_id} stop requested.")],
            state=ToolResultState.SUCCESS,
        )

    async def list_tools(
        self,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[ToolBase]:
        return [
            JobList(self, user_id=user_id, session_id=session_id),
            JobOutput(self, user_id=user_id, session_id=session_id),
            JobKill(self, user_id=user_id, session_id=session_id),
        ]

    def cancel_task(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if task is None or not task.is_active or task.asyncio_task is None:
            return False
        task.asyncio_task.cancel()
        return True

    def cancel_session_tasks(self, session_id: str) -> int:
        cancelled = 0
        for task in self.tasks.values():
            if task.session_id != session_id or not task.is_active:
                continue
            if task.asyncio_task is None:
                continue
            logger.info(
                "Cancelling background job for session: job_id=%s, "
                "session_id=%s, agent_id=%s",
                task.id,
                task.session_id,
                task.agent_id,
            )
            task.asyncio_task.cancel()
            cancelled += 1
        return cancelled

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        logger.info(
            "Shutting down BackgroundTaskManager: cancelling %d job(s).",
            len(self.tasks),
        )
        for task in list(self.tasks.values()):
            if task.asyncio_task is not None and not task.asyncio_task.done():
                task.asyncio_task.cancel()
        self.tasks.clear()
