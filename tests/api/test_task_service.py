import asyncio
from typing import Any, Awaitable, Callable

import pytest

from app.api.models import (
    MAX_ACTIVE_TASKS,
    EventName,
    TaskCreateRequest,
    TaskStatus,
    ToolProfile,
)
from app.api.service import ServiceError, TaskService
from app.api.task_store import InMemoryTaskStore, TaskRecord


class ObjectMessage:
    def __init__(self, role: str, content: str) -> None:
        self.role = role
        self.content = content


class DumpMessage:
    def __init__(self, role: str, content: str) -> None:
        self.role = role
        self.content = content

    def model_dump(self, mode: str = "python") -> dict[str, str]:
        assert mode in {"json", "python"}
        return {"role": self.role, "content": self.content}


class FakeAgent:
    def __init__(
        self,
        result: str = "run result",
        messages: list[Any] | None = None,
        events: list[dict[str, Any]] | None = None,
        gate: asyncio.Event | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.result = result
        self.messages = messages or []
        self.events = events or []
        self.gate = gate
        self.failure = failure
        self.run_calls = 0
        self.cleanup_calls = 0
        self.received_messages: list[str] = []
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def run(
        self,
        message: str,
        on_event: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> str:
        self.run_calls += 1
        self.received_messages.append(message)
        self.started.set()
        for event in self.events:
            await on_event(event)
        if self.failure is not None:
            raise self.failure
        if self.gate is not None:
            try:
                await self.gate.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
        return self.result

    async def cleanup(self) -> None:
        self.cleanup_calls += 1


class FakeFactory:
    def __init__(self, *agents: FakeAgent) -> None:
        self.agents = list(agents)
        self.calls: list[tuple[ToolProfile, int]] = []

    def __call__(self, profile: ToolProfile, max_steps: int) -> FakeAgent:
        self.calls.append((profile, max_steps))
        return self.agents.pop(0)


def make_request(
    message: str = "hello",
    *,
    max_steps: int = 3,
    timeout_seconds: int = 30,
    tool_profile: ToolProfile = ToolProfile.CHAT,
) -> TaskCreateRequest:
    return TaskCreateRequest(
        message=message,
        max_steps=max_steps,
        timeout_seconds=timeout_seconds,
        tool_profile=tool_profile,
    )


async def wait_for_status(
    service: TaskService,
    task_id: str,
    status: TaskStatus,
    timeout: float = 2.0,
) -> TaskRecord:
    async def poll() -> TaskRecord:
        while True:
            record = await service.store.get_task(task_id)
            if record is not None and record.status == status:
                return record
            await asyncio.sleep(0.01)

    return await asyncio.wait_for(poll(), timeout=timeout)


@pytest.mark.asyncio
async def test_submit_success_returns_immediately_and_stores_result() -> None:
    gate = asyncio.Event()
    agent = FakeAgent(
        result="run summary",
        messages=[
            {"role": "assistant", "content": ""},
            ObjectMessage("assistant", "final answer"),
        ],
        events=[
            {
                "type": "agent.step.started",
                "current_step": 1,
                "max_steps": 3,
                "agent_state": "thinking",
            },
            {"type": "agent.step.completed", "current_step": 1, "max_steps": 3},
        ],
        gate=gate,
    )
    factory = FakeFactory(agent)
    service = TaskService(agent_factory=factory)

    response = await service.submit(make_request("write a haiku"))

    assert response.status == TaskStatus.QUEUED
    assert factory.calls == []
    await asyncio.wait_for(agent.started.wait(), timeout=1)
    assert factory.calls == [(ToolProfile.CHAT, 3)]
    assert agent.received_messages == ["write a haiku"]
    status = await service.get_status(response.task_id)
    assert status.status == TaskStatus.RUNNING
    assert status.current_step == 1
    assert status.agent_state == "thinking"
    with pytest.raises(ServiceError) as not_terminal:
        await service.get_result(response.task_id)
    assert not_terminal.value.code == "task_not_terminal"

    gate.set()
    await wait_for_status(service, response.task_id, TaskStatus.SUCCEEDED)
    result = await service.get_result(response.task_id)
    events = await service.store.events_since(response.task_id, since_id=0)

    assert result.final_text == "final answer"
    assert result.raw_steps == [{"type": "run_result", "content": "run summary"}]
    assert result.messages == [
        {"role": "assistant", "content": ""},
        {"role": "assistant", "content": "final answer"},
    ]
    assert [event.type for event in events.events] == [
        EventName.TASK_QUEUED,
        EventName.TASK_STARTED,
        EventName.AGENT_STEP_STARTED,
        EventName.AGENT_STEP_COMPLETED,
        EventName.TASK_SUCCEEDED,
    ]
    assert events.events[2].payload == {
        "current_step": 1,
        "max_steps": 3,
        "agent_state": "thinking",
    }


@pytest.mark.asyncio
async def test_get_result_falls_back_to_run_string_without_assistant_message() -> None:
    agent = FakeAgent(
        result="raw run string",
        messages=[DumpMessage("user", "hello")],
    )
    service = TaskService(agent_factory=FakeFactory(agent))

    response = await service.submit(make_request())
    await wait_for_status(service, response.task_id, TaskStatus.SUCCEEDED)
    result = await service.get_result(response.task_id)

    assert result.final_text == "raw run string"
    assert result.raw_steps == [{"type": "run_result", "content": "raw run string"}]
    assert result.messages == [{"role": "user", "content": "hello"}]


@pytest.mark.asyncio
async def test_agent_failure_marks_failed_and_emits_task_failed() -> None:
    agent = FakeAgent(failure=RuntimeError("boom"))
    service = TaskService(agent_factory=FakeFactory(agent))

    response = await service.submit(make_request())
    await wait_for_status(service, response.task_id, TaskStatus.FAILED)
    result = await service.get_result(response.task_id)
    events = await service.store.events_since(response.task_id, since_id=0)

    assert result.error is not None
    assert result.error["code"] == "agent_error"
    assert result.error["message"] == "Agent execution failed."
    assert events.events[-1].type == EventName.TASK_FAILED
    assert events.events[-1].payload["error"]["code"] == "agent_error"


@pytest.mark.asyncio
async def test_timeout_marks_failed_with_deterministic_error() -> None:
    agent = FakeAgent(gate=asyncio.Event())
    service = TaskService(agent_factory=FakeFactory(agent))

    response = await service.submit(make_request(timeout_seconds=1))

    await wait_for_status(service, response.task_id, TaskStatus.FAILED, timeout=2.5)
    result = await service.get_result(response.task_id)

    assert agent.cancelled.is_set()
    assert result.error == {
        "code": "timeout",
        "message": "Task timed out.",
        "details": None,
    }


@pytest.mark.asyncio
async def test_cancel_running_task_marks_cancelled() -> None:
    agent = FakeAgent(gate=asyncio.Event())
    service = TaskService(agent_factory=FakeFactory(agent))
    response = await service.submit(make_request())
    await asyncio.wait_for(agent.started.wait(), timeout=1)

    cancel_response = await service.cancel(response.task_id)

    assert cancel_response.status == TaskStatus.RUNNING
    assert cancel_response.cancellation_requested is True
    assert cancel_response.message == "Cancellation requested."
    await wait_for_status(service, response.task_id, TaskStatus.CANCELLED)
    result = await service.get_result(response.task_id)
    assert agent.cancelled.is_set()
    assert result.error is not None
    assert result.error["code"] == "task_cancelled"


@pytest.mark.asyncio
async def test_cancel_queued_task_marks_cancelled_without_agent_handle() -> None:
    store = InMemoryTaskStore()
    record = await store.create_task(make_request())
    service = TaskService(store=store, agent_factory=FakeFactory())

    response = await service.cancel(record.task_id)

    assert response.status == TaskStatus.CANCELLED
    assert response.cancellation_requested is True
    assert response.message == "Task cancelled."


@pytest.mark.asyncio
async def test_capacity_rejection_does_not_store_fifth_active_task() -> None:
    agents = [FakeAgent(gate=asyncio.Event()) for _ in range(MAX_ACTIVE_TASKS)]
    service = TaskService(agent_factory=FakeFactory(*agents))

    responses = [
        await service.submit(make_request(f"task {index}"))
        for index in range(MAX_ACTIVE_TASKS)
    ]
    await asyncio.gather(*(agent.started.wait() for agent in agents))

    with pytest.raises(ServiceError) as capacity_error:
        await service.submit(make_request("fifth task"))

    assert capacity_error.value.code == "capacity_exceeded"
    assert len(await service.store.list_tasks()) == MAX_ACTIVE_TASKS
    assert await service.store.active_non_terminal_count() == MAX_ACTIVE_TASKS
    assert {response.status for response in responses} == {TaskStatus.QUEUED}
    await service.shutdown()


@pytest.mark.asyncio
async def test_disabled_dev_local_rejection_does_not_store_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENMANUS_API_ENABLE_DEV_LOCAL", raising=False)
    service = TaskService(agent_factory=FakeFactory())

    with pytest.raises(ServiceError) as disabled_error:
        await service.submit(make_request(tool_profile=ToolProfile.DEV_LOCAL))

    assert disabled_error.value.code == "tool_profile_disabled"
    assert await service.store.list_tasks() == []


@pytest.mark.asyncio
async def test_get_status_result_and_cancel_service_errors() -> None:
    store = InMemoryTaskStore()
    service = TaskService(store=store, agent_factory=FakeFactory())

    for method in (service.get_status, service.get_result, service.cancel):
        with pytest.raises(ServiceError) as not_found:
            await method("missing")
        assert not_found.value.code == "task_not_found"

    record = await store.create_task(make_request())
    with pytest.raises(ServiceError) as not_terminal:
        await service.get_result(record.task_id)
    assert not_terminal.value.code == "task_not_terminal"

    await store.mark_running(record.task_id)
    await store.mark_succeeded(record.task_id, "done", [], [])
    assert record.expires_at is not None
    await store.expire_old_tasks(record.expires_at)

    expired_status = await service.get_status(record.task_id)
    assert expired_status.status == TaskStatus.EXPIRED
    for method in (service.get_result, service.cancel):
        with pytest.raises(ServiceError) as expired:
            await method(record.task_id)
        assert expired.value.code == "task_expired"


@pytest.mark.asyncio
async def test_shutdown_cancels_and_awaits_active_work() -> None:
    agent = FakeAgent(gate=asyncio.Event())
    service = TaskService(agent_factory=FakeFactory(agent))
    response = await service.submit(make_request())
    await asyncio.wait_for(agent.started.wait(), timeout=1)

    await service.shutdown()
    record = await service.store.get_task(response.task_id)

    assert record is not None
    assert record.status == TaskStatus.CANCELLED
    assert record.cancellation_reason == "Task cancelled during shutdown."
    assert agent.run_calls == 1
    assert agent.cancelled.is_set()
    assert agent.cleanup_calls == 0
