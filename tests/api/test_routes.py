import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.main import create_app
from app.api.models import (
    ALLOWED_FRONTEND_ORIGINS,
    EventName,
    TaskCreateRequest,
    TaskStatus,
    ToolProfile,
)
from app.api.service import TaskService


class FakeAgent:
    def __init__(
        self,
        result: str = "run result",
        messages: list[Any] | None = None,
        events: list[dict[str, Any]] | None = None,
        gate: asyncio.Event | None = None,
    ) -> None:
        self.result = result
        self.messages = messages or []
        self.events = events or []
        self.gate = gate
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.received_messages: list[str] = []

    async def run(
        self,
        message: str,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> str:
        self.received_messages.append(message)
        self.started.set()
        for event in self.events:
            if on_event is not None:
                await on_event(event)
        if self.gate is not None:
            try:
                await self.gate.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
        return self.result


class FakeFactory:
    def __init__(self, *agents: FakeAgent) -> None:
        self.agents = list(agents)
        self.calls: list[tuple[ToolProfile, int]] = []

    def __call__(self, tool_profile: ToolProfile, max_steps: int) -> FakeAgent:
        self.calls.append((tool_profile, max_steps))
        if not self.agents:
            raise AssertionError("No fake agent available")
        return self.agents.pop(0)


@asynccontextmanager
async def api_client(service: TaskService | None = None) -> AsyncIterator[AsyncClient]:
    fastapi_app = create_app(service)
    async with fastapi_app.router.lifespan_context(fastapi_app):
        transport = ASGITransport(app=fastapi_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            try:
                yield client
            finally:
                if service is not None:
                    await service.shutdown()


async def wait_for_status(
    client: AsyncClient,
    task_id: str,
    expected_status: str,
    timeout: float = 2.0,
) -> dict[str, Any]:
    async def poll() -> dict[str, Any]:
        while True:
            response = await client.get(f"/api/tasks/{task_id}")
            response.raise_for_status()
            payload = response.json()
            if payload["status"] == expected_status:
                return payload
            await asyncio.sleep(0.01)

    return await asyncio.wait_for(poll(), timeout=timeout)


def assert_error(
    payload: dict[str, Any],
    code: str,
    message: str | None = None,
) -> None:
    assert set(payload) == {"error"}
    assert payload["error"]["code"] == code
    if message is not None:
        assert payload["error"]["message"] == message
    assert "details" in payload["error"]


@pytest.mark.asyncio
async def test_health_endpoint_returns_ok() -> None:
    service = TaskService(agent_factory=FakeFactory())

    async with api_client(service) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_post_status_and_result_lifecycle() -> None:
    gate = asyncio.Event()
    agent = FakeAgent(
        result="raw run result",
        messages=[{"role": "assistant", "content": "final answer"}],
        gate=gate,
    )
    service = TaskService(agent_factory=FakeFactory(agent))

    async with api_client(service) as client:
        create_response = await client.post(
            "/api/tasks",
            json={"message": "write a status", "max_steps": 3, "timeout_seconds": 30},
        )
        create_payload = create_response.json()
        task_id = create_payload["task_id"]

        assert create_response.status_code == 202
        assert create_payload["status"] == "queued"
        assert create_payload["links"] == {
            "status": f"/api/tasks/{task_id}",
            "result": f"/api/tasks/{task_id}/result",
            "cancel": f"/api/tasks/{task_id}/cancel",
            "events": f"/api/tasks/{task_id}/events",
        }
        await asyncio.wait_for(agent.started.wait(), timeout=1)
        assert agent.received_messages == ["write a status"]

        status_response = await client.get(f"/api/tasks/{task_id}")
        result_pending = await client.get(f"/api/tasks/{task_id}/result")

        assert status_response.status_code == 200
        assert status_response.json()["status"] == "running"
        assert result_pending.status_code == 409
        assert_error(result_pending.json(), "task_not_terminal")

        gate.set()
        await wait_for_status(client, task_id, "succeeded")
        result_response = await client.get(f"/api/tasks/{task_id}/result")

    assert result_response.status_code == 200
    assert result_response.json()["status"] == "succeeded"
    assert result_response.json()["final_text"] == "final answer"
    assert result_response.json()["raw_steps"] == [
        {"type": "run_result", "content": "raw run result"}
    ]


@pytest.mark.asyncio
async def test_cancel_running_task() -> None:
    gate = asyncio.Event()
    agent = FakeAgent(gate=gate)
    service = TaskService(agent_factory=FakeFactory(agent))

    async with api_client(service) as client:
        create_response = await client.post("/api/tasks", json={"message": "cancel me"})
        task_id = create_response.json()["task_id"]
        await asyncio.wait_for(agent.started.wait(), timeout=1)

        cancel_response = await client.post(f"/api/tasks/{task_id}/cancel")
        await wait_for_status(client, task_id, "cancelled")
        result_response = await client.get(f"/api/tasks/{task_id}/result")

    cancel_payload = cancel_response.json()
    assert cancel_response.status_code == 202
    assert cancel_payload["status"] == "running"
    assert cancel_payload["cancellation_requested"] is True
    assert cancel_payload["message"] == "Cancellation requested."
    assert result_response.status_code == 200
    assert result_response.json()["status"] == "cancelled"
    assert result_response.json()["error"]["code"] == "task_cancelled"
    assert agent.cancelled.is_set()


@pytest.mark.asyncio
async def test_cancel_queued_and_terminal_tasks_return_200() -> None:
    service = TaskService(agent_factory=FakeFactory())
    queued = await service.store.create_task(TaskCreateRequest(message="cancel queued"))
    terminal = await service.store.create_task(TaskCreateRequest(message="already done"))
    await service.store.mark_running(terminal.task_id)
    await service.store.mark_succeeded(terminal.task_id, "done", [], [])

    async with api_client(service) as client:
        queued_response = await client.post(f"/api/tasks/{queued.task_id}/cancel")
        terminal_response = await client.post(f"/api/tasks/{terminal.task_id}/cancel")

    assert queued_response.status_code == 200
    assert queued_response.json()["status"] == "cancelled"
    assert queued_response.json()["cancellation_requested"] is True
    assert terminal_response.status_code == 200
    assert terminal_response.json()["status"] == "succeeded"
    assert terminal_response.json()["cancellation_requested"] is False


@pytest.mark.asyncio
async def test_events_filtering_and_expired_events_error() -> None:
    agent = FakeAgent(
        events=[
            {
                "type": EventName.AGENT_STEP_STARTED.value,
                "current_step": 1,
                "max_steps": 2,
                "agent_state": "thinking",
            },
            {
                "type": EventName.AGENT_STEP_COMPLETED.value,
                "current_step": 1,
                "max_steps": 2,
            },
        ]
    )
    service = TaskService(agent_factory=FakeFactory(agent))

    async with api_client(service) as client:
        create_response = await client.post("/api/tasks", json={"message": "events"})
        task_id = create_response.json()["task_id"]
        await wait_for_status(client, task_id, "succeeded")

        all_events_response = await client.get(f"/api/tasks/{task_id}/events")
        filtered_response = await client.get(f"/api/tasks/{task_id}/events?since=2")

        record = await service.store.get_task(task_id)
        assert record is not None
        assert record.expires_at is not None
        await service.store.expire_old_tasks(record.expires_at)
        expired_events_response = await client.get(f"/api/tasks/{task_id}/events")

    all_events = all_events_response.json()
    filtered_events = filtered_response.json()

    assert all_events_response.status_code == 200
    assert [event["event_id"] for event in all_events["events"]] == [1, 2, 3, 4, 5]
    assert all_events["latest_event_id"] == 5
    assert filtered_response.status_code == 200
    assert [event["event_id"] for event in filtered_events["events"]] == [3, 4, 5]
    assert [event["type"] for event in filtered_events["events"]] == [
        "agent.step.started",
        "agent.step.completed",
        "task.succeeded",
    ]
    assert expired_events_response.status_code == 410
    assert_error(expired_events_response.json(), "task_expired")


@pytest.mark.asyncio
async def test_dev_local_profile_disabled_returns_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENMANUS_API_ENABLE_DEV_LOCAL", raising=False)
    service = TaskService(agent_factory=FakeFactory())

    async with api_client(service) as client:
        response = await client.post(
            "/api/tasks",
            json={"message": "use local shell", "tool_profile": "dev_local"},
        )

    assert response.status_code == 403
    assert_error(response.json(), "tool_profile_disabled")


@pytest.mark.asyncio
async def test_unknown_task_returns_404() -> None:
    service = TaskService(agent_factory=FakeFactory())

    async with api_client(service) as client:
        status_response = await client.get("/api/tasks/missing")
        events_response = await client.get("/api/tasks/missing/events")

    assert status_response.status_code == 404
    assert_error(status_response.json(), "task_not_found")
    assert events_response.status_code == 404
    assert_error(events_response.json(), "task_not_found")


@pytest.mark.asyncio
async def test_validation_errors_use_api_error_shape() -> None:
    service = TaskService(agent_factory=FakeFactory())

    async with api_client(service) as client:
        body_response = await client.post("/api/tasks", json={"message": ""})
        negative_since_response = await client.get("/api/tasks/missing/events?since=-1")
        text_since_response = await client.get("/api/tasks/missing/events?since=abc")

    assert body_response.status_code == 422
    assert_error(body_response.json(), "validation_error")
    assert negative_since_response.status_code == 422
    assert_error(negative_since_response.json(), "validation_error")
    assert text_since_response.status_code == 422
    assert_error(text_since_response.json(), "validation_error")


@pytest.mark.asyncio
async def test_cors_allows_only_frontend_origins() -> None:
    service = TaskService(agent_factory=FakeFactory())

    async with api_client(service) as client:
        allowed_responses = [
            await client.options(
                "/api/tasks",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type",
                },
            )
            for origin in ALLOWED_FRONTEND_ORIGINS
        ]
        disallowed_response = await client.options(
            "/api/tasks",
            headers={
                "Origin": "http://malicious.local:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

    assert set(ALLOWED_FRONTEND_ORIGINS) == {
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    }
    assert all(response.status_code == 200 for response in allowed_responses)
    assert [
        response.headers["access-control-allow-origin"]
        for response in allowed_responses
    ] == list(ALLOWED_FRONTEND_ORIGINS)
    assert disallowed_response.status_code == 400
    assert "access-control-allow-origin" not in disallowed_response.headers


@pytest.mark.asyncio
async def test_mock_agent_mode_uses_deterministic_fake_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMANUS_API_MOCK_AGENT", "1")

    async with api_client() as client:
        create_response = await client.post("/api/tasks", json={"message": "hello mock"})
        task_id = create_response.json()["task_id"]
        await wait_for_status(client, task_id, "succeeded")
        result_response = await client.get(f"/api/tasks/{task_id}/result")
        events_response = await client.get(f"/api/tasks/{task_id}/events?since=2")

    assert create_response.status_code == 202
    assert result_response.status_code == 200
    assert result_response.json()["final_text"] == "mocked response: hello mock"
    assert result_response.json()["messages"] == [
        {"role": "assistant", "content": "mocked response: hello mock"}
    ]
    assert [event["type"] for event in events_response.json()["events"]] == [
        "agent.step.started",
        "agent.step.completed",
        "task.succeeded",
    ]
