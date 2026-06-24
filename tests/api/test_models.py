from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.api.models import (
    API_BACKEND_HOST,
    API_BACKEND_PORT,
    DEFAULT_MAX_STEPS,
    DEFAULT_TIMEOUT_SECONDS,
    EVENT_BUFFER_MAX,
    FRONTEND_ORIGINS,
    MAX_ACTIVE_TASKS,
    MAX_INPUT_LENGTH,
    MAX_STEPS,
    MAX_TIMEOUT_SECONDS,
    TASK_TTL_SECONDS,
    ApiError,
    EventName,
    TaskCreateRequest,
    TaskCreateResponse,
    TaskEvent,
    TaskLinks,
    TaskResultResponse,
    TaskStatus,
    ToolProfile,
    serialize_utc_z,
)


def make_links() -> TaskLinks:
    return TaskLinks(
        status="/api/tasks/task-1",
        result="/api/tasks/task-1/result",
        cancel="/api/tasks/task-1/cancel",
        events="/api/tasks/task-1/events",
    )


def test_task_create_request_defaults_and_runtime_constants() -> None:
    request = TaskCreateRequest(message="hello")

    assert API_BACKEND_HOST == "127.0.0.1"
    assert API_BACKEND_PORT == 8000
    assert FRONTEND_ORIGINS == ("http://127.0.0.1:5173", "http://localhost:5173")
    assert MAX_INPUT_LENGTH == 20_000
    assert DEFAULT_TIMEOUT_SECONDS == 1_800
    assert MAX_TIMEOUT_SECONDS == 1_800
    assert DEFAULT_MAX_STEPS == 20
    assert MAX_STEPS == 30
    assert MAX_ACTIVE_TASKS == 4
    assert TASK_TTL_SECONDS == 3_600
    assert EVENT_BUFFER_MAX == 200
    assert request.model_dump(mode="json") == {
        "message": "hello",
        "max_steps": 20,
        "timeout_seconds": 1_800,
        "tool_profile": "chat",
        "metadata": {},
    }
    assert request.tool_profile == ToolProfile.CHAT


def test_enum_values_are_v1_contract_values() -> None:
    assert [status.value for status in TaskStatus] == [
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "expired",
    ]
    assert [profile.value for profile in ToolProfile] == ["chat", "browser", "dev_local"]
    assert [event_name.value for event_name in EventName] == [
        "task.queued",
        "task.started",
        "agent.step.started",
        "agent.step.completed",
        "task.succeeded",
        "task.failed",
        "task.cancelled",
        "task.expired",
    ]


def test_oversized_message_validation_mentions_length_constraint() -> None:
    with pytest.raises(ValidationError) as validation_error:
        TaskCreateRequest(message="x" * (MAX_INPUT_LENGTH + 1))

    errors = validation_error.value.errors()
    assert errors[0]["loc"] == ("message",)
    assert errors[0]["type"] == "string_too_long"
    assert errors[0]["ctx"] == {"max_length": MAX_INPUT_LENGTH}


def test_api_error_shape_is_exact() -> None:
    api_error = ApiError(
        code="validation_error",
        message="Message is too long",
        details={"field": "message"},
    )

    assert api_error.model_dump(mode="json") == {
        "error": {
            "code": "validation_error",
            "message": "Message is too long",
            "details": {"field": "message"},
        }
    }
    assert ApiError.model_validate(api_error.model_dump()).model_dump() == api_error.model_dump()


def test_datetime_serialization_is_utc_z() -> None:
    source_time = datetime(
        2026,
        6,
        23,
        14,
        34,
        56,
        123456,
        tzinfo=timezone(timedelta(hours=2)),
    )
    response = TaskCreateResponse(
        task_id="task-1",
        created_at=source_time,
        updated_at=source_time,
        links=make_links(),
    )

    serialized = response.model_dump(mode="json")
    python_serialized = response.model_dump()
    assert serialize_utc_z(source_time) == "2026-06-23T12:34:56.123456Z"
    assert serialized["created_at"] == "2026-06-23T12:34:56.123456Z"
    assert serialized["updated_at"] == "2026-06-23T12:34:56.123456Z"
    assert python_serialized["created_at"] == "2026-06-23T12:34:56.123456Z"
    assert python_serialized["updated_at"] == "2026-06-23T12:34:56.123456Z"


def test_task_event_exact_fields() -> None:
    created_at = datetime(2026, 6, 23, 12, 34, 56, 123456, tzinfo=timezone.utc)
    event = TaskEvent(
        event_id=1,
        task_id="task-1",
        type=EventName.TASK_QUEUED,
        created_at=created_at,
        payload={"position": 1},
    )

    expected_event = {
        "event_id": 1,
        "task_id": "task-1",
        "type": "task.queued",
        "created_at": "2026-06-23T12:34:56.123456Z",
        "payload": {"position": 1},
    }
    assert event.model_dump() == expected_event
    assert event.model_dump(mode="json") == expected_event
    assert list(event.model_dump(mode="json")) == [
        "event_id",
        "task_id",
        "type",
        "created_at",
        "payload",
    ]


def test_result_response_shape_uses_json_ready_enums_and_datetimes() -> None:
    created_at = datetime(2026, 6, 23, 12, 34, 56, 123456, tzinfo=timezone.utc)
    completed_at = datetime(2026, 6, 23, 12, 35, 56, 123456, tzinfo=timezone.utc)
    response = TaskResultResponse(
        task_id="task-1",
        status=TaskStatus.SUCCEEDED,
        final_text="done",
        raw_steps=[{"step": 1}],
        messages=[{"role": "assistant", "content": "done"}],
        error=None,
        created_at=created_at,
        completed_at=completed_at,
    )

    assert response.model_dump(mode="json") == {
        "task_id": "task-1",
        "status": "succeeded",
        "final_text": "done",
        "raw_steps": [{"step": 1}],
        "messages": [{"role": "assistant", "content": "done"}],
        "error": None,
        "created_at": "2026-06-23T12:34:56.123456Z",
        "completed_at": "2026-06-23T12:35:56.123456Z",
    }
