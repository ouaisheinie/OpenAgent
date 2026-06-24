from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    model_validator,
)


API_BACKEND_HOST = "127.0.0.1"
API_BACKEND_PORT = 8000
BACKEND_HOST = API_BACKEND_HOST
BACKEND_PORT = API_BACKEND_PORT
FRONTEND_ORIGINS = ("*",)
ALLOWED_FRONTEND_ORIGINS = FRONTEND_ORIGINS
MAX_INPUT_LENGTH = 20_000
DEFAULT_RUNTIME_SECONDS = 1_800
MAX_RUNTIME_SECONDS = 1_800
DEFAULT_TIMEOUT_SECONDS = DEFAULT_RUNTIME_SECONDS
MAX_TIMEOUT_SECONDS = MAX_RUNTIME_SECONDS
DEFAULT_MAX_STEPS = 20
MAX_STEPS = 30
MAX_ACTIVE_TASKS = 4
TASK_TTL_SECONDS = 3_600
EVENT_BUFFER_MAX = 200


def serialize_utc_z(value: datetime) -> str:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        utc_value = value.replace(tzinfo=timezone.utc)
    else:
        utc_value = value.astimezone(timezone.utc)
    return utc_value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


UTCDateTime = Annotated[
    datetime,
    PlainSerializer(serialize_utc_z, return_type=str, when_used="always"),
]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ToolProfile(str, Enum):
    CHAT = "chat"
    BROWSER = "browser"
    DEV_LOCAL = "dev_local"


class EventName(str, Enum):
    TASK_QUEUED = "task.queued"
    TASK_STARTED = "task.started"
    AGENT_STEP_STARTED = "agent.step.started"
    AGENT_STEP_COMPLETED = "agent.step.completed"
    TASK_SUCCEEDED = "task.succeeded"
    TASK_FAILED = "task.failed"
    TASK_CANCELLED = "task.cancelled"
    TASK_EXPIRED = "task.expired"


class TaskCreateRequest(ApiModel):
    message: str = Field(min_length=1, max_length=MAX_INPUT_LENGTH)
    max_steps: int = Field(default=DEFAULT_MAX_STEPS, ge=1, le=MAX_STEPS)
    timeout_seconds: int = Field(
        default=DEFAULT_TIMEOUT_SECONDS,
        ge=1,
        le=MAX_TIMEOUT_SECONDS,
    )
    tool_profile: ToolProfile = ToolProfile.CHAT
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskLinks(ApiModel):
    status: str
    result: str
    cancel: str
    events: str


class TaskCreateResponse(ApiModel):
    task_id: str
    status: TaskStatus = TaskStatus.QUEUED
    created_at: UTCDateTime
    updated_at: UTCDateTime
    links: TaskLinks


class TaskStatusResponse(ApiModel):
    task_id: str
    status: TaskStatus
    agent_state: str | None = None
    current_step: int = Field(default=0, ge=0)
    max_steps: int = Field(default=DEFAULT_MAX_STEPS, ge=1, le=MAX_STEPS)
    created_at: UTCDateTime
    updated_at: UTCDateTime
    started_at: UTCDateTime | None = None
    completed_at: UTCDateTime | None = None
    expires_at: UTCDateTime | None = None
    error: dict[str, Any] | None = None
    latest_event_id: int = Field(default=0, ge=0)
    links: TaskLinks


class TaskResultResponse(ApiModel):
    task_id: str
    status: TaskStatus
    final_text: str | None = None
    raw_steps: list[Any] = Field(default_factory=list)
    messages: list[dict[str, Any]] = Field(default_factory=list)
    error: dict[str, Any] | None = None
    created_at: UTCDateTime
    completed_at: UTCDateTime | None = None


class TaskCancelResponse(ApiModel):
    task_id: str
    status: TaskStatus
    cancellation_requested: bool
    message: str


class TaskEvent(ApiModel):
    event_id: int = Field(ge=1)
    task_id: str
    type: EventName
    created_at: UTCDateTime
    payload: dict[str, Any] = Field(default_factory=dict)


class TaskEventsResponse(ApiModel):
    task_id: str
    events: list[TaskEvent] = Field(default_factory=list)
    latest_event_id: int = Field(default=0, ge=0)


class ApiErrorDetail(ApiModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ApiError(ApiModel):
    error: ApiErrorDetail

    @model_validator(mode="before")
    @classmethod
    def wrap_error(cls, data: Any) -> Any:
        if isinstance(data, dict) and "error" not in data:
            return {"error": data}
        if (
            isinstance(data, dict)
            and set(data) == {"error"}
            and isinstance(data["error"], dict)
        ):
            return data
        return data

    @property
    def code(self) -> str:
        return self.error.code

    @property
    def message(self) -> str:
        return self.error.message

    @property
    def details(self) -> dict[str, Any] | None:
        return self.error.details


__all__ = [
    "ALLOWED_FRONTEND_ORIGINS",
    "API_BACKEND_HOST",
    "API_BACKEND_PORT",
    "BACKEND_HOST",
    "BACKEND_PORT",
    "DEFAULT_MAX_STEPS",
    "DEFAULT_RUNTIME_SECONDS",
    "DEFAULT_TIMEOUT_SECONDS",
    "EVENT_BUFFER_MAX",
    "FRONTEND_ORIGINS",
    "MAX_ACTIVE_TASKS",
    "MAX_INPUT_LENGTH",
    "MAX_RUNTIME_SECONDS",
    "MAX_STEPS",
    "MAX_TIMEOUT_SECONDS",
    "TASK_TTL_SECONDS",
    "ApiError",
    "EventName",
    "TaskCancelResponse",
    "TaskCreateRequest",
    "TaskCreateResponse",
    "TaskEvent",
    "TaskEventsResponse",
    "TaskLinks",
    "TaskResultResponse",
    "TaskStatus",
    "TaskStatusResponse",
    "ToolProfile",
    "serialize_utc_z",
    "utc_now",
]
