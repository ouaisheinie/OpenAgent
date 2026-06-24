import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest
from pydantic import Field

from app.schema import AgentState


class DummyLLM:
    def __init__(self, *args, **kwargs):
        pass


class FakeSandboxClient:
    async def cleanup(self):
        pass


class FakeLogger:
    def debug(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass

    def exception(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass


def load_base_module():
    llm_module = types.ModuleType("app.llm")
    llm_module.LLM = DummyLLM
    logger_module = types.ModuleType("app.logger")
    logger_module.logger = FakeLogger()

    sandbox_package = types.ModuleType("app.sandbox")
    sandbox_package.__path__ = []
    sandbox_client_module = types.ModuleType("app.sandbox.client")
    sandbox_client_module.SANDBOX_CLIENT = FakeSandboxClient()

    module_names = ["app.llm", "app.logger", "app.sandbox", "app.sandbox.client"]
    previous_modules = {name: sys.modules.get(name) for name in module_names}
    try:
        sys.modules["app.llm"] = llm_module
        sys.modules["app.logger"] = logger_module
        sys.modules["app.sandbox"] = sandbox_package
        sys.modules["app.sandbox.client"] = sandbox_client_module

        module_path = Path(__file__).parents[2] / "app" / "agent" / "base.py"
        spec = importlib.util.spec_from_file_location(
            "test_base_agent_module", module_path
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        for name, previous_module in previous_modules.items():
            if previous_module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous_module


base_module = load_base_module()
BaseAgent = base_module.BaseAgent


class FakeAgent(BaseAgent):
    name: str = "fake"
    llm: DummyLLM = Field(default_factory=DummyLLM)
    finish_after: int | None = None
    step_calls: list[int] = Field(default_factory=list)

    async def step(self) -> str:
        self.step_calls.append(self.current_step)
        if self.finish_after is not None and self.current_step >= self.finish_after:
            self.state = AgentState.FINISHED
        return f"fake-result-{self.current_step}"


class WaitingAgent(BaseAgent):
    name: str = "waiting"
    llm: DummyLLM = Field(default_factory=DummyLLM)
    step_started: asyncio.Event
    step_release: asyncio.Event

    async def step(self) -> str:
        self.step_started.set()
        await self.step_release.wait()
        return "released"


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    monkeypatch.setattr(base_module, "LLM", DummyLLM)


@pytest.fixture
def sandbox_cleanup_calls(monkeypatch):
    calls = []

    async def fake_cleanup():
        calls.append("cleanup")

    monkeypatch.setattr(base_module.SANDBOX_CLIENT, "cleanup", fake_cleanup)
    return calls


@pytest.mark.asyncio
async def test_run_emits_step_events_in_order(sandbox_cleanup_calls):
    agent = FakeAgent(max_steps=3, finish_after=2)
    events = []

    async def record_event(event: dict):
        events.append(event)

    result = await agent.run("hello", on_event=record_event)

    assert events == [
        {"type": "agent.step.started", "current_step": 1, "max_steps": 3},
        {"type": "agent.step.completed", "current_step": 1, "max_steps": 3},
        {"type": "agent.step.started", "current_step": 2, "max_steps": 3},
        {"type": "agent.step.completed", "current_step": 2, "max_steps": 3},
    ]
    assert agent.step_calls == [1, 2]
    assert result == "Step 1: fake-result-1\nStep 2: fake-result-2"
    assert sandbox_cleanup_calls == ["cleanup"]


@pytest.mark.asyncio
async def test_run_keeps_prompt_only_cli_compatibility(sandbox_cleanup_calls):
    agent = FakeAgent(max_steps=2, finish_after=1)

    result = await agent.run("hello")

    assert result == "Step 1: fake-result-1"
    assert agent.messages[0].role == "user"
    assert agent.messages[0].content == "hello"
    assert sandbox_cleanup_calls == ["cleanup"]


@pytest.mark.asyncio
async def test_run_cleans_sandbox_when_cancelled(sandbox_cleanup_calls):
    step_started = asyncio.Event()
    step_release = asyncio.Event()
    agent = WaitingAgent(step_started=step_started, step_release=step_release)

    task = asyncio.create_task(agent.run("hello"))
    await asyncio.wait_for(step_started.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert sandbox_cleanup_calls == ["cleanup"]
