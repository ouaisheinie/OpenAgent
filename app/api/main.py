from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.models import ALLOWED_FRONTEND_ORIGINS, ApiError, EventName, ToolProfile
from app.api.routes import attach_routes
from app.api.service import AgentEventCallback, TaskAgent, TaskService


class _MockAgent:
    def __init__(self, tool_profile: ToolProfile, max_steps: int) -> None:
        self.tool_profile = tool_profile
        self.max_steps = max_steps
        self.messages: list[dict[str, Any]] = []

    async def run(
        self,
        message: str,
        on_event: AgentEventCallback | None = None,
    ) -> str:
        content = f"mocked response: {message}"
        if on_event is not None:
            await on_event(
                {
                    "type": EventName.AGENT_STEP_STARTED.value,
                    "current_step": 1,
                    "max_steps": self.max_steps,
                    "agent_state": "mocking",
                    "tool_profile": self.tool_profile.value,
                }
            )
            await on_event(
                {
                    "type": EventName.AGENT_STEP_COMPLETED.value,
                    "current_step": 1,
                    "max_steps": self.max_steps,
                    "agent_state": "mocking",
                    "tool_profile": self.tool_profile.value,
                }
            )
        self.messages = [{"role": "assistant", "content": content}]
        return content


def _mock_agent_factory(tool_profile: ToolProfile, max_steps: int) -> TaskAgent:
    return _MockAgent(tool_profile=tool_profile, max_steps=max_steps)


def _build_task_service() -> TaskService:
    if os.getenv("OPENMANUS_API_MOCK_AGENT") == "1":
        return TaskService(agent_factory=_mock_agent_factory)
    return TaskService()


def _validation_error_response(error: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=ApiError(
            code="validation_error",
            message="Request validation failed.",
            details={"errors": jsonable_encoder(error.errors())},
        ).model_dump(mode="json"),
    )


def create_app(task_service: TaskService | None = None) -> FastAPI:
    owns_service = task_service is None

    @asynccontextmanager
    async def lifespan(fastapi_app: FastAPI) -> AsyncIterator[None]:
        service = task_service if task_service is not None else _build_task_service()
        fastapi_app.state.task_service = service
        try:
            yield
        finally:
            if owns_service:
                await service.shutdown()

    fastapi_app = FastAPI(
        title="OpenManus Web API",
        version="0.1.0",
        lifespan=lifespan,
    )
    fastapi_app.add_middleware(
        CORSMiddleware,
        allow_origins=list(ALLOWED_FRONTEND_ORIGINS),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @fastapi_app.exception_handler(RequestValidationError)
    async def request_validation_exception_handler(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        return _validation_error_response(error)

    attach_routes(fastapi_app)
    return fastapi_app


app = create_app()


__all__ = ["app", "create_app"]
