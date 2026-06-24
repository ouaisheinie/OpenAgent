from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, FastAPI, Query, Request, Response, status as http_status
from fastapi.responses import JSONResponse

from app.api.models import (
    ApiError,
    TaskCancelResponse,
    TaskCreateRequest,
    TaskCreateResponse,
    TaskEventsResponse,
    TaskResultResponse,
    TaskStatus,
    TaskStatusResponse,
)
from app.api.service import ServiceError, TaskService


router = APIRouter()


def attach_routes(app: FastAPI) -> None:
    app.include_router(router)


def _service(request: Request) -> TaskService:
    return cast(TaskService, request.app.state.task_service)


def _api_error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ApiError(code=code, message=message, details=details).model_dump(
            mode="json"
        ),
    )


def _service_error_response(error: ServiceError) -> JSONResponse:
    return _api_error_response(
        status_code=error.status_code,
        code=error.code,
        message=error.message,
        details=error.details,
    )


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post(
    "/api/tasks",
    response_model=TaskCreateResponse,
    status_code=http_status.HTTP_202_ACCEPTED,
)
async def create_task(
    request_body: TaskCreateRequest,
    request: Request,
) -> TaskCreateResponse | JSONResponse:
    try:
        return await _service(request).submit(request_body)
    except ServiceError as error:
        return _service_error_response(error)


@router.get("/api/tasks/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(
    task_id: str,
    request: Request,
) -> TaskStatusResponse | JSONResponse:
    try:
        return await _service(request).get_status(task_id)
    except ServiceError as error:
        return _service_error_response(error)


@router.get("/api/tasks/{task_id}/result", response_model=TaskResultResponse)
async def get_task_result(
    task_id: str,
    request: Request,
) -> TaskResultResponse | JSONResponse:
    try:
        return await _service(request).get_result(task_id)
    except ServiceError as error:
        return _service_error_response(error)


@router.post("/api/tasks/{task_id}/cancel", response_model=TaskCancelResponse)
async def cancel_task(
    task_id: str,
    request: Request,
    response: Response,
) -> TaskCancelResponse | JSONResponse:
    try:
        cancel_response = await _service(request).cancel(task_id)
        if (
            cancel_response.status == TaskStatus.RUNNING
            and cancel_response.cancellation_requested
        ):
            response.status_code = http_status.HTTP_202_ACCEPTED
        return cancel_response
    except ServiceError as error:
        return _service_error_response(error)


@router.get("/api/tasks/{task_id}/events", response_model=TaskEventsResponse)
async def get_task_events(
    task_id: str,
    request: Request,
    since: int = Query(default=0, ge=0),
) -> TaskEventsResponse | JSONResponse:
    service = _service(request)
    try:
        status_response = await service.get_status(task_id)
        if status_response.status == TaskStatus.EXPIRED:
            raise ServiceError(
                code="task_expired",
                message="Task has expired.",
                details={"task_id": task_id},
                status_code=http_status.HTTP_410_GONE,
            )
        return await service.store.events_since(task_id, since_id=since)
    except ServiceError as error:
        return _service_error_response(error)
    except KeyError:
        return _api_error_response(
            status_code=http_status.HTTP_404_NOT_FOUND,
            code="task_not_found",
            message="Task not found.",
            details={"task_id": task_id},
        )


__all__ = ["attach_routes", "router"]
