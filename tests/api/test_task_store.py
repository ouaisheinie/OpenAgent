from datetime import timedelta
from uuid import UUID

import pytest

from app.api.models import (
    EVENT_BUFFER_MAX,
    TASK_TTL_SECONDS,
    EventName,
    TaskCreateRequest,
    TaskStatus,
    ToolProfile,
)
from app.api.task_store import InMemoryTaskStore


def make_request(message: str = "hello") -> TaskCreateRequest:
    return TaskCreateRequest(
        message=message,
        max_steps=5,
        timeout_seconds=30,
        tool_profile=ToolProfile.BROWSER,
        metadata={"trace_id": "trace-1"},
    )


def assert_uuid4(value: str) -> None:
    parsed = UUID(value, version=4)

    assert str(parsed) == value
    assert parsed.version == 4


@pytest.mark.asyncio
async def test_lifecycle_create_running_succeeded_preserves_result_and_events() -> None:
    store = InMemoryTaskStore()

    record = await store.create_task(make_request())

    assert_uuid4(record.task_id)
    assert record.status == TaskStatus.QUEUED
    assert record.message == "hello"
    assert record.max_steps == 5
    assert record.timeout_seconds == 30
    assert record.tool_profile == ToolProfile.BROWSER
    assert record.metadata == {"trace_id": "trace-1"}
    assert record.to_create_response().model_dump(mode="json") == {
        "task_id": record.task_id,
        "status": "queued",
        "created_at": record.created_at.isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        ),
        "updated_at": record.updated_at.isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        ),
        "links": {
            "status": f"/api/tasks/{record.task_id}",
            "result": f"/api/tasks/{record.task_id}/result",
            "cancel": f"/api/tasks/{record.task_id}/cancel",
            "events": f"/api/tasks/{record.task_id}/events",
        },
    }

    await store.mark_running(record.task_id)
    await store.mark_succeeded(
        record.task_id,
        final_text="done",
        raw_steps=[{"step": 1, "text": "done"}],
        messages=[{"role": "assistant", "content": "done"}],
    )

    fetched = await store.get_task(record.task_id)
    assert fetched is record
    assert record.status == TaskStatus.SUCCEEDED
    assert record.started_at is not None
    assert record.completed_at is not None
    assert record.expires_at == record.completed_at + timedelta(seconds=TASK_TTL_SECONDS)
    assert record.to_status_response().status == TaskStatus.SUCCEEDED
    assert record.to_status_response().latest_event_id == 3
    assert record.to_result_response().model_dump(mode="json") == {
        "task_id": record.task_id,
        "status": "succeeded",
        "final_text": "done",
        "raw_steps": [{"step": 1, "text": "done"}],
        "messages": [{"role": "assistant", "content": "done"}],
        "error": None,
        "created_at": record.created_at.isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        ),
        "completed_at": record.completed_at.isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        ),
    }

    all_events = await store.events_since(record.task_id, since_id=0)
    filtered_events = await store.events_since(record.task_id, since_id=1)

    assert [event.event_id for event in all_events.events] == [1, 2, 3]
    assert [event.type for event in all_events.events] == [
        EventName.TASK_QUEUED,
        EventName.TASK_STARTED,
        EventName.TASK_SUCCEEDED,
    ]
    assert [event.event_id for event in filtered_events.events] == [2, 3]
    assert all_events.latest_event_id == 3
    assert await store.active_non_terminal_count() == 0


@pytest.mark.asyncio
async def test_lifecycle_illegal_succeeded_to_running_raises_value_error() -> None:
    store = InMemoryTaskStore()
    record = await store.create_task(make_request())
    await store.mark_running(record.task_id)
    await store.mark_succeeded(record.task_id, "done", [], [])

    with pytest.raises(ValueError, match="Cannot transition task .* from succeeded to running"):
        await store.mark_running(record.task_id)

    assert record.status == TaskStatus.SUCCEEDED
    assert (await store.events_since(record.task_id, 0)).latest_event_id == 3


@pytest.mark.asyncio
async def test_cancelled_tasks_record_deterministic_cancel_response_and_error() -> None:
    store = InMemoryTaskStore()
    queued = await store.create_task(make_request("queued"))
    running = await store.create_task(make_request("running"))
    await store.mark_running(running.task_id)

    await store.mark_cancelled(queued.task_id, "client cancelled")
    await store.mark_cancelled(running.task_id, "shutdown")

    assert queued.status == TaskStatus.CANCELLED
    assert running.status == TaskStatus.CANCELLED
    assert queued.error == {
        "code": "task_cancelled",
        "message": "client cancelled",
        "details": None,
    }
    assert queued.to_cancel_response(cancellation_requested=True).model_dump() == {
        "task_id": queued.task_id,
        "status": TaskStatus.CANCELLED,
        "cancellation_requested": True,
        "message": "client cancelled",
    }


@pytest.mark.asyncio
async def test_events_since_filters_order_and_trims_to_newest_buffer() -> None:
    store = InMemoryTaskStore()
    record = await store.create_task(make_request())
    started = await store.append_event(
        record.task_id,
        EventName.AGENT_STEP_STARTED,
        {"current_step": 1, "max_steps": 5},
    )
    completed = await store.append_event(
        record.task_id,
        EventName.AGENT_STEP_COMPLETED,
        {"current_step": 1, "max_steps": 5},
    )

    filtered = await store.events_since(record.task_id, since_id=1)

    assert [event.event_id for event in filtered.events] == [started.event_id, completed.event_id]
    assert [event.type for event in filtered.events] == [
        EventName.AGENT_STEP_STARTED,
        EventName.AGENT_STEP_COMPLETED,
    ]
    assert filtered.latest_event_id == 3
    assert record.current_step == 1

    for index in range(EVENT_BUFFER_MAX + 5):
        await store.append_event(
            record.task_id,
            EventName.AGENT_STEP_COMPLETED,
            {"current_step": index + 2},
        )

    buffered = await store.events_since(record.task_id, since_id=0)
    after_latest_stored = await store.append_event(
        record.task_id,
        EventName.AGENT_STEP_COMPLETED,
        {"current_step": EVENT_BUFFER_MAX + 7},
    )

    assert len(buffered.events) == EVENT_BUFFER_MAX
    assert [event.event_id for event in buffered.events] == list(range(9, 209))
    assert [event.event_id for event in (await store.events_since(record.task_id, 207)).events] == [
        208,
        209,
    ]
    assert after_latest_stored.event_id == 209


@pytest.mark.asyncio
async def test_unknown_task_ids_return_none_or_raise_key_error() -> None:
    store = InMemoryTaskStore()

    assert await store.get_task("missing") is None
    assert await store.list_tasks() == []
    with pytest.raises(KeyError, match="Unknown task_id: missing"):
        await store.mark_running("missing")
    with pytest.raises(KeyError, match="Unknown task_id: missing"):
        await store.append_event("missing", EventName.TASK_STARTED, {})
    with pytest.raises(KeyError, match="Unknown task_id: missing"):
        await store.events_since("missing", since_id=0)


@pytest.mark.asyncio
async def test_active_non_terminal_count_counts_only_queued_and_running() -> None:
    store = InMemoryTaskStore()
    queued = await store.create_task(make_request("queued"))
    running = await store.create_task(make_request("running"))
    succeeded = await store.create_task(make_request("succeeded"))
    cancelled = await store.create_task(make_request("cancelled"))

    await store.mark_running(running.task_id)
    await store.mark_running(succeeded.task_id)
    await store.mark_succeeded(succeeded.task_id, "done", [], [])
    await store.mark_cancelled(cancelled.task_id, "client cancelled")

    assert await store.active_non_terminal_count() == 2

    await store.mark_cancelled(queued.task_id, "client cancelled")
    await store.mark_cancelled(running.task_id, "shutdown")

    assert await store.active_non_terminal_count() == 0


@pytest.mark.asyncio
async def test_expiry_marks_only_terminal_tasks_after_ttl_and_blocks_later_transitions() -> None:
    store = InMemoryTaskStore()
    succeeded = await store.create_task(make_request("succeeded"))
    failed = await store.create_task(make_request("failed"))
    cancelled = await store.create_task(make_request("cancelled"))
    running = await store.create_task(make_request("running"))

    await store.mark_running(succeeded.task_id)
    await store.mark_succeeded(succeeded.task_id, "done", [], [])
    await store.mark_running(failed.task_id)
    await store.mark_failed(failed.task_id, {"code": "agent_error", "message": "boom"})
    await store.mark_cancelled(cancelled.task_id, "client cancelled")
    await store.mark_running(running.task_id)

    terminal_expires_at = [succeeded.expires_at, failed.expires_at, cancelled.expires_at]
    assert all(expires_at is not None for expires_at in terminal_expires_at)
    earliest_expiry = min(expires_at for expires_at in terminal_expires_at if expires_at)
    latest_expiry = max(expires_at for expires_at in terminal_expires_at if expires_at)
    before_ttl = earliest_expiry - timedelta(microseconds=1)
    assert await store.expire_old_tasks(before_ttl) == []
    assert succeeded.status == TaskStatus.SUCCEEDED

    expired = await store.expire_old_tasks(latest_expiry)

    assert {record.task_id for record in expired} == {
        succeeded.task_id,
        failed.task_id,
        cancelled.task_id,
    }
    assert succeeded.status == TaskStatus.EXPIRED
    assert failed.status == TaskStatus.EXPIRED
    assert cancelled.status == TaskStatus.EXPIRED
    assert running.status == TaskStatus.RUNNING
    assert (await store.events_since(succeeded.task_id, 0)).events[-1].type == EventName.TASK_EXPIRED

    with pytest.raises(ValueError, match="Cannot transition task .* from expired to running"):
        await store.mark_running(succeeded.task_id)
    with pytest.raises(ValueError, match="Cannot transition task .* from expired to cancelled"):
        await store.mark_cancelled(succeeded.task_id, "too late")
