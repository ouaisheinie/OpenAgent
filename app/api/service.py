from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import Enum
from typing import Any, Awaitable, Callable, Protocol

from app.api.models import (
    MAX_ACTIVE_TASKS,
    EventName,
    TaskCancelResponse,
    TaskCreateRequest,
    TaskCreateResponse,
    TaskResultResponse,
    TaskStatus,
    TaskStatusResponse,
    ToolProfile,
    serialize_utc_z,
    utc_now,
)
from app.api.task_store import InMemoryTaskStore, TaskRecord


AgentEventCallback = Callable[[dict[str, Any]], Awaitable[None]]


class TaskAgent(Protocol):
    messages: Sequence[Any]

    async def run(
        self,
        message: str,
        on_event: AgentEventCallback | None = None,
    ) -> str: ...


AgentFactory = Callable[[ToolProfile, int], TaskAgent]


class TaskServiceError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        status_code: int = 400,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.status_code = status_code

    def to_error(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


ServiceError = TaskServiceError

_ACTIVE_STATUSES = frozenset({TaskStatus.QUEUED, TaskStatus.RUNNING})
_TERMINAL_STATUSES = frozenset(
    {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}
)
_CLIENT_CANCELLED_REASON = "Task cancelled."
_SHUTDOWN_CANCELLED_REASON = "Task cancelled during shutdown."


def _dev_local_enabled() -> bool:
    return os.getenv("OPENMANUS_API_ENABLE_DEV_LOCAL") == "1"


def _default_agent_factory(tool_profile: ToolProfile, max_steps: int) -> TaskAgent:
    from app.agent.web_manus import WebManus

    return WebManus.create_for_web(
        profile=tool_profile,
        max_steps=max_steps,
        allow_dev_local=_dev_local_enabled(),
    )


class TaskService:
    def __init__(
        self,
        store: InMemoryTaskStore | None = None,
        agent_factory: AgentFactory | None = None,
    ) -> None:
        self.store = store or InMemoryTaskStore()
        self.agent_factory = agent_factory or _default_agent_factory
        self._task_handles: dict[str, asyncio.Task[None]] = {}
        self._submit_lock = asyncio.Lock()
        self._cancel_reasons: dict[str, str] = {}

    async def submit(self, request: TaskCreateRequest) -> TaskCreateResponse:
        if request.tool_profile == ToolProfile.DEV_LOCAL and not _dev_local_enabled():
            raise ServiceError(
                code="tool_profile_disabled",
                message="Tool profile dev_local is disabled.",
                details={"tool_profile": ToolProfile.DEV_LOCAL.value},
                status_code=403,
            )

        async with self._submit_lock:
            active_count = await self.store.active_non_terminal_count()
            if active_count >= MAX_ACTIVE_TASKS:
                raise ServiceError(
                    code="capacity_exceeded",
                    message="Task capacity exceeded.",
                    details={"max_active_tasks": MAX_ACTIVE_TASKS},
                    status_code=429,
                )

            record = await self.store.create_task(request)
            response = record.to_create_response()
            handle = asyncio.create_task(
                self._run_task(record.task_id),
                name=f"openmanus-task-{record.task_id}",
            )
            self._task_handles[record.task_id] = handle
            handle.add_done_callback(
                lambda done_handle, task_id=record.task_id: self._discard_handle(
                    task_id, done_handle
                )
            )
            return response

    async def get_status(self, task_id: str) -> TaskStatusResponse:
        record = await self._get_record_or_error(task_id)
        return record.to_status_response()

    async def get_result(self, task_id: str) -> TaskResultResponse:
        record = await self._get_record_or_error(task_id)
        if record.status == TaskStatus.EXPIRED:
            raise ServiceError(
                code="task_expired",
                message="Task has expired.",
                details={"task_id": task_id},
                status_code=410,
            )
        if record.status not in _TERMINAL_STATUSES:
            raise ServiceError(
                code="task_not_terminal",
                message="Task is not complete yet.",
                details={"task_id": task_id, "status": record.status.value},
                status_code=409,
            )
        return record.to_result_response()

    async def cancel(self, task_id: str) -> TaskCancelResponse:
        record = await self._get_record_or_error(task_id)
        if record.status == TaskStatus.EXPIRED:
            raise ServiceError(
                code="task_expired",
                message="Task has expired.",
                details={"task_id": task_id},
                status_code=410,
            )
        if record.status == TaskStatus.QUEUED:
            cancelled = await self.store.mark_cancelled(task_id, _CLIENT_CANCELLED_REASON)
            self._cancel_handle(task_id, _CLIENT_CANCELLED_REASON)
            return cancelled.to_cancel_response(
                cancellation_requested=True,
                message=_CLIENT_CANCELLED_REASON,
            )
        if record.status == TaskStatus.RUNNING:
            handle = self._task_handles.get(task_id)
            if handle is not None and not handle.done():
                self._cancel_handle(task_id, _CLIENT_CANCELLED_REASON)
                return record.to_cancel_response(
                    cancellation_requested=True,
                    message="Cancellation requested.",
                )
            cancelled = await self.store.mark_cancelled(task_id, _CLIENT_CANCELLED_REASON)
            return cancelled.to_cancel_response(
                cancellation_requested=True,
                message=_CLIENT_CANCELLED_REASON,
            )
        return record.to_cancel_response(
            cancellation_requested=False,
            message=f"Task is {record.status.value}.",
        )

    async def shutdown(self) -> None:
        active_handles = [
            (task_id, handle)
            for task_id, handle in self._task_handles.items()
            if not handle.done()
        ]
        for task_id, handle in active_handles:
            self._cancel_reasons[task_id] = _SHUTDOWN_CANCELLED_REASON
            handle.cancel()
        if active_handles:
            await asyncio.gather(
                *(handle for _, handle in active_handles),
                return_exceptions=True,
            )
        await self._cancel_active_records(_SHUTDOWN_CANCELLED_REASON)

    async def cleanup_expired(self) -> list[TaskRecord]:
        return await self.store.expire_old_tasks(utc_now())

    async def _run_task(self, task_id: str) -> None:
        try:
            record = await self._mark_running_if_queued(task_id)
            if record is None:
                return

            agent = self.agent_factory(record.tool_profile, record.max_steps)

            async def on_event(event: dict[str, Any]) -> None:
                await self._append_agent_event(task_id, event)

            run_result = await asyncio.wait_for(
                agent.run(record.message, on_event=on_event),
                timeout=record.timeout_seconds,
            )
            messages = _messages_to_json(getattr(agent, "messages", []))
            final_text = _last_assistant_text(messages) or run_result
            await self.store.mark_succeeded(
                task_id,
                final_text=final_text,
                raw_steps=_raw_steps_from_result(run_result),
                messages=messages,
            )
        except asyncio.TimeoutError:
            await self._mark_failed_if_running(
                task_id,
                {
                    "code": "timeout",
                    "message": "Task timed out.",
                    "details": None,
                },
            )
        except asyncio.CancelledError:
            reason = self._cancel_reasons.pop(task_id, _CLIENT_CANCELLED_REASON)
            await self._mark_cancelled_if_active(task_id, reason)
            raise
        except Exception as exc:
            await self._mark_failed_if_running(
                task_id,
                {
                    "code": "agent_error",
                    "message": "Agent execution failed.",
                    "details": {
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                },
            )

    async def _mark_running_if_queued(self, task_id: str) -> TaskRecord | None:
        record = await self.store.get_task(task_id)
        if record is None or record.status != TaskStatus.QUEUED:
            return None
        try:
            return await self.store.mark_running(task_id)
        except ValueError:
            refreshed = await self.store.get_task(task_id)
            if refreshed is None or refreshed.status != TaskStatus.QUEUED:
                return None
            raise

    async def _append_agent_event(self, task_id: str, event: dict[str, Any]) -> None:
        event_type = event["type"]
        payload = {
            str(key): _json_safe(value)
            for key, value in event.items()
            if key != "type"
        }
        await self.store.append_event(task_id, EventName(event_type), payload)

    async def _get_record_or_error(self, task_id: str) -> TaskRecord:
        record = await self.store.get_task(task_id)
        if record is None:
            raise ServiceError(
                code="task_not_found",
                message="Task not found.",
                details={"task_id": task_id},
                status_code=404,
            )
        return record

    async def _mark_failed_if_running(
        self, task_id: str, error: dict[str, Any]
    ) -> None:
        record = await self.store.get_task(task_id)
        if record is None or record.status != TaskStatus.RUNNING:
            return
        await self.store.mark_failed(task_id, error)

    async def _mark_cancelled_if_active(self, task_id: str, reason: str) -> None:
        record = await self.store.get_task(task_id)
        if record is None or record.status not in _ACTIVE_STATUSES:
            return
        await self.store.mark_cancelled(task_id, reason)

    async def _cancel_active_records(self, reason: str) -> None:
        records = await self.store.list_tasks()
        for record in records:
            if record.status in _ACTIVE_STATUSES:
                await self.store.mark_cancelled(record.task_id, reason)

    def _cancel_handle(self, task_id: str, reason: str) -> None:
        self._cancel_reasons[task_id] = reason
        handle = self._task_handles.get(task_id)
        if handle is not None and not handle.done():
            handle.cancel()

    def _discard_handle(self, task_id: str, handle: asyncio.Task[None]) -> None:
        if self._task_handles.get(task_id) is handle:
            self._task_handles.pop(task_id, None)


def _raw_steps_from_result(run_result: str) -> list[dict[str, Any]]:
    return [{"type": "run_result", "content": _json_safe(run_result)}]


def _messages_to_json(messages: Sequence[Any]) -> list[dict[str, Any]]:
    return [_message_to_json(message) for message in messages]


def _message_to_json(message: Any) -> dict[str, Any]:
    dumped = _model_dump_json(message)
    if isinstance(dumped, Mapping):
        return {str(key): _json_safe(value) for key, value in dumped.items()}
    if isinstance(message, Mapping):
        return {str(key): _json_safe(value) for key, value in message.items()}

    output: dict[str, Any] = {}
    for attr in ("role", "content", "name", "tool_call_id", "tool_calls"):
        if hasattr(message, attr):
            value = getattr(message, attr)
            if value is not None:
                output[attr] = _json_safe(value)
    if output:
        return output
    return {"content": _json_safe(message)}


def _model_dump_json(value: Any) -> Any:
    model_dump = getattr(value, "model_dump", None)
    if not callable(model_dump):
        return None
    try:
        return model_dump(mode="json")
    except TypeError:
        return model_dump()


def _last_assistant_text(messages: Sequence[dict[str, Any]]) -> str | None:
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if content is None:
            continue
        text = content if isinstance(content, str) else str(content)
        if text.strip():
            return text
    return None


def _json_safe(value: Any) -> Any:
    dumped = _model_dump_json(value)
    if dumped is not None:
        return _json_safe(dumped)
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return serialize_utc_z(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_json_safe(item) for item in value]
    return str(value)


__all__ = [
    "AgentEventCallback",
    "AgentFactory",
    "ServiceError",
    "TaskAgent",
    "TaskService",
    "TaskServiceError",
]
