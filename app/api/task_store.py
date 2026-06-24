from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable
from uuid import uuid4

from app.api.models import (
    EVENT_BUFFER_MAX,
    TASK_TTL_SECONDS,
    EventName,
    TaskCancelResponse,
    TaskCreateRequest,
    TaskCreateResponse,
    TaskEvent,
    TaskEventsResponse,
    TaskLinks,
    TaskResultResponse,
    TaskStatus,
    TaskStatusResponse,
    ToolProfile,
    utc_now,
)


_ACTIVE_STATUSES = frozenset({TaskStatus.QUEUED, TaskStatus.RUNNING})
_TERMINAL_STATUSES = frozenset(
    {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}
)


def _links_for(task_id: str) -> TaskLinks:
    root = f"/api/tasks/{task_id}"
    return TaskLinks(
        status=root,
        result=f"{root}/result",
        cancel=f"{root}/cancel",
        events=f"{root}/events",
    )


def _allowed_next_statuses(status: TaskStatus) -> frozenset[TaskStatus]:
    if status == TaskStatus.QUEUED:
        return frozenset({TaskStatus.RUNNING, TaskStatus.CANCELLED})
    if status == TaskStatus.RUNNING:
        return frozenset(
            {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}
        )
    if status in _TERMINAL_STATUSES:
        return frozenset({TaskStatus.EXPIRED})
    return frozenset()


def _normalise_error(error: Any) -> dict[str, Any]:
    if isinstance(error, dict):
        code = str(error.get("code") or "task_failed")
        message = str(error.get("message") or code)
        return {
            "code": code,
            "message": message,
            "details": error.get("details"),
        }
    return {
        "code": "task_failed",
        "message": str(error) if error else "Task failed",
        "details": None,
    }


@dataclass(slots=True)
class TaskRecord:
    task_id: str
    message: str
    max_steps: int
    timeout_seconds: int
    tool_profile: ToolProfile
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    status: TaskStatus = TaskStatus.QUEUED
    agent_state: str | None = None
    current_step: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    expires_at: datetime | None = None
    final_text: str | None = None
    raw_steps: list[Any] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    error: dict[str, Any] | None = None
    cancellation_reason: str | None = None
    events: list[TaskEvent] = field(default_factory=list)
    _next_event_id: int = 1

    @classmethod
    def from_request(
        cls, task_id: str, request: TaskCreateRequest, now: datetime
    ) -> "TaskRecord":
        return cls(
            task_id=task_id,
            message=request.message,
            max_steps=request.max_steps,
            timeout_seconds=request.timeout_seconds,
            tool_profile=request.tool_profile,
            metadata=dict(request.metadata),
            created_at=now,
            updated_at=now,
        )

    @property
    def latest_event_id(self) -> int:
        return self._next_event_id - 1

    @property
    def links(self) -> TaskLinks:
        return _links_for(self.task_id)

    def add_event(
        self,
        event_type: EventName | str,
        payload: dict[str, Any],
        created_at: datetime,
    ) -> TaskEvent:
        event = TaskEvent(
            event_id=self._next_event_id,
            task_id=self.task_id,
            type=EventName(event_type),
            created_at=created_at,
            payload=dict(payload),
        )
        self._next_event_id += 1
        self.events.append(event)
        if len(self.events) > EVENT_BUFFER_MAX:
            self.events = self.events[-EVENT_BUFFER_MAX:]
        self._apply_event_progress(event)
        return event

    def events_after(self, since_id: int) -> list[TaskEvent]:
        return [event for event in self.events if event.event_id > since_id]

    def to_create_response(self) -> TaskCreateResponse:
        return TaskCreateResponse(
            task_id=self.task_id,
            status=self.status,
            created_at=self.created_at,
            updated_at=self.updated_at,
            links=self.links,
        )

    def to_status_response(self) -> TaskStatusResponse:
        return TaskStatusResponse(
            task_id=self.task_id,
            status=self.status,
            agent_state=self.agent_state,
            current_step=self.current_step,
            max_steps=self.max_steps,
            created_at=self.created_at,
            updated_at=self.updated_at,
            started_at=self.started_at,
            completed_at=self.completed_at,
            expires_at=self.expires_at,
            error=self.error,
            latest_event_id=self.latest_event_id,
            links=self.links,
        )

    def to_result_response(self) -> TaskResultResponse:
        return TaskResultResponse(
            task_id=self.task_id,
            status=self.status,
            final_text=self.final_text,
            raw_steps=list(self.raw_steps),
            messages=[dict(message) for message in self.messages],
            error=self.error,
            created_at=self.created_at,
            completed_at=self.completed_at,
        )

    def to_cancel_response(
        self,
        cancellation_requested: bool | None = None,
        message: str | None = None,
    ) -> TaskCancelResponse:
        if cancellation_requested is None:
            cancellation_requested = self.status == TaskStatus.CANCELLED
        if message is None:
            message = self.cancellation_reason or f"Task is {self.status.value}"
        return TaskCancelResponse(
            task_id=self.task_id,
            status=self.status,
            cancellation_requested=cancellation_requested,
            message=message,
        )

    def to_events_response(
        self, events: Iterable[TaskEvent] | None = None
    ) -> TaskEventsResponse:
        return TaskEventsResponse(
            task_id=self.task_id,
            events=list(self.events if events is None else events),
            latest_event_id=self.latest_event_id,
        )

    def _apply_event_progress(self, event: TaskEvent) -> None:
        if event.type not in {
            EventName.AGENT_STEP_STARTED,
            EventName.AGENT_STEP_COMPLETED,
        }:
            return
        current_step = event.payload.get("current_step")
        if isinstance(current_step, int):
            self.current_step = current_step
        agent_state = event.payload.get("agent_state")
        if isinstance(agent_state, str):
            self.agent_state = agent_state


class InMemoryTaskStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._tasks: dict[str, TaskRecord] = {}

    async def create_task(self, request: TaskCreateRequest) -> TaskRecord:
        async with self._lock:
            now = utc_now()
            task_id = str(uuid4())
            record = TaskRecord.from_request(task_id, request, now)
            record.add_event(EventName.TASK_QUEUED, {"status": "queued"}, now)
            self._tasks[task_id] = record
            return record

    async def get_task(self, task_id: str) -> TaskRecord | None:
        async with self._lock:
            return self._tasks.get(task_id)

    async def list_tasks(self) -> list[TaskRecord]:
        async with self._lock:
            return list(self._tasks.values())

    async def mark_running(self, task_id: str) -> TaskRecord:
        async with self._lock:
            record = self._require_task(task_id)
            now = utc_now()
            self._transition(record, TaskStatus.RUNNING, now)
            record.started_at = now
            record.add_event(EventName.TASK_STARTED, {"status": "running"}, now)
            return record

    async def mark_succeeded(
        self,
        task_id: str,
        final_text: str,
        raw_steps: list[Any],
        messages: list[dict[str, Any]],
    ) -> TaskRecord:
        async with self._lock:
            record = self._require_task(task_id)
            now = utc_now()
            self._transition(record, TaskStatus.SUCCEEDED, now)
            record.completed_at = now
            record.expires_at = now + timedelta(seconds=TASK_TTL_SECONDS)
            record.final_text = final_text
            record.raw_steps = list(raw_steps)
            record.messages = [dict(message) for message in messages]
            record.error = None
            record.add_event(EventName.TASK_SUCCEEDED, {"status": "succeeded"}, now)
            return record

    async def mark_failed(self, task_id: str, error: Any) -> TaskRecord:
        async with self._lock:
            record = self._require_task(task_id)
            now = utc_now()
            normalised_error = _normalise_error(error)
            self._transition(record, TaskStatus.FAILED, now)
            record.completed_at = now
            record.expires_at = now + timedelta(seconds=TASK_TTL_SECONDS)
            record.error = normalised_error
            record.add_event(
                EventName.TASK_FAILED,
                {"status": "failed", "error": normalised_error},
                now,
            )
            return record

    async def mark_cancelled(self, task_id: str, reason: str) -> TaskRecord:
        async with self._lock:
            record = self._require_task(task_id)
            now = utc_now()
            self._transition(record, TaskStatus.CANCELLED, now)
            record.completed_at = now
            record.expires_at = now + timedelta(seconds=TASK_TTL_SECONDS)
            record.cancellation_reason = reason
            record.error = {
                "code": "task_cancelled",
                "message": reason,
                "details": None,
            }
            record.add_event(
                EventName.TASK_CANCELLED,
                {"status": "cancelled", "reason": reason},
                now,
            )
            return record

    async def expire_old_tasks(self, now: datetime) -> list[TaskRecord]:
        async with self._lock:
            expired_records: list[TaskRecord] = []
            for record in self._tasks.values():
                if record.status not in _TERMINAL_STATUSES:
                    continue
                if record.expires_at is None or record.expires_at > now:
                    continue
                previous_status = record.status.value
                self._transition(record, TaskStatus.EXPIRED, now)
                record.add_event(
                    EventName.TASK_EXPIRED,
                    {"status": "expired", "previous_status": previous_status},
                    now,
                )
                expired_records.append(record)
            return expired_records

    async def append_event(
        self, task_id: str, type: EventName | str, payload: dict[str, Any]
    ) -> TaskEvent:
        async with self._lock:
            record = self._require_task(task_id)
            now = utc_now()
            event = record.add_event(type, payload, now)
            record.updated_at = now
            return event

    async def events_since(self, task_id: str, since_id: int) -> TaskEventsResponse:
        async with self._lock:
            record = self._require_task(task_id)
            return record.to_events_response(record.events_after(since_id))

    async def active_non_terminal_count(self) -> int:
        async with self._lock:
            return sum(1 for task in self._tasks.values() if task.status in _ACTIVE_STATUSES)

    def _require_task(self, task_id: str) -> TaskRecord:
        try:
            return self._tasks[task_id]
        except KeyError as exc:
            raise KeyError(f"Unknown task_id: {task_id}") from exc

    def _transition(
        self, record: TaskRecord, new_status: TaskStatus, now: datetime
    ) -> None:
        if new_status not in _allowed_next_statuses(record.status):
            raise ValueError(
                f"Cannot transition task {record.task_id} "
                f"from {record.status.value} to {new_status.value}"
            )
        record.status = new_status
        record.updated_at = now


__all__ = ["InMemoryTaskStore", "TaskRecord"]
