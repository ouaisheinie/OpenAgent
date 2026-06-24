# OpenManus Web Service Adapter + React Chat Frontend

## TL;DR
> **Summary**: Add a thin FastAPI task API around the existing OpenManus agent core, then add a standalone Vite React TypeScript chat frontend under `frontEnd/` using pnpm. V1 is single-process, in-memory, polling-first, and web-safe by default.
> **Deliverables**:
> - FastAPI app under `app/api/` with task submission, status, result, cancel, health, and lifecycle event endpoints.
> - Web-safe `WebManus` tool profiles that avoid CLI-blocking and high-risk tools in V1.
> - Standalone pnpm/Vite/React/TypeScript frontend in `frontEnd/` with a chat UI, polling, result/error rendering, and cancellation.
> - Backend pytest coverage and frontend build/test/Playwright verification.
> **Effort**: Large
> **Parallel**: YES - 2 implementation waves + final verification wave
> **Critical Path**: Task 1 → Task 2 → Task 5 → Task 6 → Task 8 → Task 9 → Task 10 → Final Verification

## Context
### Original Request
The user first asked whether this CLI-style repository can be rewritten as a web service. After feasibility analysis, the user asked to follow the first-version web-service approach and additionally requested a React + TypeScript frontend using pnpm inside the root-level `frontEnd/` directory.

### Interview Summary
- Preserve the existing OpenManus agent/tool/flow core; do not rewrite the project.
- Add a FastAPI adapter layer instead of productizing the experimental A2A service.
- Use one backend task per user message in V1; no persistent backend conversation memory.
- Store frontend chat history locally for display only.
- Use in-memory task storage only; server restart loses tasks.
- Use polling/result retrieval for V1; lifecycle events are returned as JSON replay from `/api/tasks/{task_id}/events`, and SSE/token streaming are out of scope.
- Create a standalone pnpm-managed frontend in `frontEnd/`; do not create a root pnpm workspace.

### Metis Review (gaps addressed)
- Exact API schema, status transitions, cancellation semantics, tool allowlist, frontend UX scope, CORS/dev ports, runtime bounds, and executable QA were identified as required plan details.
- The plan locks these decisions instead of leaving them to the executor.

## Work Objectives
### Core Objective
Expose OpenManus through a local web API and a React chat frontend while keeping V1 safe, testable, and small enough for a first service version.

### Deliverables
- `app/api/__init__.py`, `app/api/main.py`, `app/api/models.py`, `app/api/routes.py`, `app/api/service.py`, and `app/api/task_store.py`.
- `app/agent/web_manus.py` and any minimal event/callback compatibility changes needed in `app/agent/base.py` and `app/agent/toolcall.py`.
- Tests under `tests/api/` using mocked agents; no real LLM calls.
- `frontEnd/` Vite React TypeScript pnpm app, including app source, styles, tests, and Playwright browser QA setup.
- Root ignore/CI/dependency automation updates only where explicitly listed in TODOs.

### Definition of Done (verifiable conditions with commands)
- `pytest tests/api -q` passes with mocked agent/service tests.
- `python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000` starts the backend.
- `curl -s http://127.0.0.1:8000/health` returns `{"status":"ok"}`.
- With `OPENMANUS_API_MOCK_AGENT=1` backend running, `curl -s -X POST http://127.0.0.1:8000/api/tasks -H 'Content-Type: application/json' -d '{"message":"Hello"}'` returns HTTP 202 JSON containing `task_id` and `status` without a real LLM call.
- `pnpm --dir frontEnd install` completes and creates/updates `frontEnd/pnpm-lock.yaml`.
- `pnpm --dir frontEnd typecheck`, `pnpm --dir frontEnd test`, and `pnpm --dir frontEnd build` pass.
- Browser QA opens `http://127.0.0.1:5173`, submits `Hello from QA`, shows user message, running state, final assistant response, and supports cancellation.

### Must Have
- Deterministic JSON response shapes for every endpoint.
- Explicit task statuses: `queued`, `running`, `succeeded`, `failed`, `cancelled`, `expired`.
- Legal transitions: `queued → running → succeeded|failed|cancelled`; `queued → cancelled`; terminal `succeeded|failed|cancelled → expired`; no transition out of `expired`.
- Defaults: backend `127.0.0.1:8000`, frontend `127.0.0.1:5173`, terminal TTL `3600s`, max running tasks `4`, max runtime `1800s`, max input length `20000` characters, event buffer max `200` events per task.
- Capacity means max active non-terminal tasks (`queued` + `running`) is `4`; the 5th active submission returns HTTP 429 and is not stored.
- CORS allowlist: `http://127.0.0.1:5173` and `http://localhost:5173` only in V1.
- One fresh `WebManus` instance per task; never share one agent across concurrent tasks.
- `agent.cleanup()` must run exactly once on success, failure, cancellation, timeout, and service shutdown.

### Must NOT Have (guardrails, AI slop patterns, scope boundaries)
- No database, Redis, Celery/RQ, cloud deployment, auth, account system, or multi-worker correctness in V1.
- No root `package.json`, root `pnpm-workspace.yaml`, or conversion of the repository into a pnpm monorepo.
- No changes to `app/tool/chart_visualization/package.json` or `app/tool/chart_visualization/package-lock.json`.
- No SSE or true token streaming in V1; `app/llm.py` streaming prints to stdout and must not be repurposed hastily.
- No web exposure of terminal-blocking/high-risk tools: `AskHuman`, `Bash`, Daytona shell/sandbox tools, `ComputerUseTool`, or MCP auto-connect.
- No real LLM calls in automated tests.
- No vague acceptance criteria requiring manual visual confirmation.

## Verification Strategy
> ZERO HUMAN INTERVENTION - all verification is agent-executed.
- Test decision: tests-after for this architecture addition; backend uses `pytest`/`pytest-asyncio` with mocked agents, frontend uses Vitest/Testing Library and Playwright browser QA.
- QA policy: Every task has agent-executed scenarios.
- Evidence: `.omo/evidence/task-{N}-{slug}.{ext}`.

## API Contract (V1, exact)
### Common Rules
- Datetimes are timezone-aware UTC ISO-8601 strings ending in `Z`, e.g. `2026-06-23T12:34:56.123456Z`.
- All non-2xx errors use `{"error":{"code": string, "message": string, "details": object|null}}`.
- Validation errors return HTTP 422 with code `validation_error`.
- Unknown task IDs return HTTP 404 with code `task_not_found`.
- `since` for events defaults to `0`; negative or non-integer `since` returns HTTP 422 `validation_error`.

### Endpoints and Responses
| Endpoint | Success | Error Statuses | Response Body |
| --- | --- | --- | --- |
| `GET /health` | 200 | none expected | `{"status":"ok"}` |
| `POST /api/tasks` | 202 | 403 `tool_profile_disabled`, 422 `validation_error`, 429 `capacity_exceeded` | `TaskCreateResponse`: `task_id`, `status:"queued"`, `created_at`, `updated_at`, `links:{status,result,cancel,events}` |
| `GET /api/tasks/{task_id}` | 200 | 404 `task_not_found` | `TaskStatusResponse`: `task_id`, `status`, `agent_state`, `current_step`, `max_steps`, `created_at`, `updated_at`, `started_at`, `completed_at`, `expires_at`, `error`, `latest_event_id`, `links` |
| `GET /api/tasks/{task_id}/result` | 200 for `succeeded|failed|cancelled` | 404 `task_not_found`, 409 `task_not_terminal`, 410 `task_expired` | `TaskResultResponse`: `task_id`, `status`, `final_text`, `raw_steps`, `messages`, `error`, `created_at`, `completed_at` |
| `POST /api/tasks/{task_id}/cancel` | 200 for queued/terminal, 202 for running | 404 `task_not_found`, 410 `task_expired` | `TaskCancelResponse`: `task_id`, `status`, `cancellation_requested`, `message` |
| `GET /api/tasks/{task_id}/events?since=<id>` | 200 | 404 `task_not_found`, 410 `task_expired`, 422 `validation_error` | `{"task_id": string, "events": TaskEvent[], "latest_event_id": int}` |

### TaskCreateRequest
`message: str` (`1..20000` chars), `max_steps: int = 20` (`1..30`), `timeout_seconds: int = 1800` (`1..1800`), `tool_profile: "chat" | "browser" | "dev_local" = "chat"`, `metadata: dict = {}`.

`dev_local` is accepted by the schema only so trusted local deployments can opt in later. V1 default is disabled. Unless environment variable `OPENMANUS_API_ENABLE_DEV_LOCAL=1` is set, `POST /api/tasks` with `tool_profile:"dev_local"` returns HTTP 403 with error code `tool_profile_disabled` and the task is not stored.

### Event Names
`task.queued`, `task.started`, `agent.step.started`, `agent.step.completed`, `task.succeeded`, `task.failed`, `task.cancelled`, `task.expired`. Event payloads are JSON objects and must never contain API keys or raw secrets.

### TaskEvent Schema
`TaskEvent` fields are exact: `event_id: int` (1-based monotonically increasing per task), `task_id: str` (UUID4 string), `type: EventName`, `created_at: str` (UTC ISO-8601 ending in `Z`), and `payload: dict = {}`. Frontend `frontEnd/src/types.ts` must mirror this shape exactly.

### Cancel Semantics
- Queued task: `POST /cancel` immediately marks `cancelled`, returns HTTP 200 with `cancellation_requested:true`.
- Running task: `POST /cancel` calls `asyncio_task.cancel()`, returns HTTP 202 with `cancellation_requested:true`, and polling eventually returns `cancelled` unless the task already finished first.
- Succeeded/failed/cancelled task: `POST /cancel` is a no-op, returns HTTP 200 with `cancellation_requested:false` and current status.
- Expired task: `POST /cancel` returns HTTP 410 `task_expired`.

## Execution Strategy
### Parallel Execution Waves
> Target: 5-8 tasks per wave. <3 per wave (except final) = under-splitting.
> Extract shared dependencies as Wave-1 tasks for max parallelism.

Wave 1: Tasks 1, 2, 3, 4, and 7 establish API contracts, task state, web-safe agent profiles, minimal agent event hooks, and isolated frontend tooling. Task 2 starts as soon as Task 1 lands; Tasks 3, 4, and 7 are independent.
Wave 2: Tasks 5, 6, 8, 9, and 10 wire backend service/routes, frontend API/client UI, automated frontend QA, and repo automation polish. This wave follows the critical path but can run non-conflicting test and polish work in parallel once dependencies land.

### Dependency Matrix (full, all tasks)
| Task | Blocks | Blocked By |
| --- | --- | --- |
| 1 API contracts/config | 2, 5, 6, 8 | None |
| 2 Task store/events | 5, 6 | 1 |
| 3 Web-safe agent profiles | 5, 10 | None |
| 4 Agent event/cancellation hooks | 5 | None |
| 5 Task orchestration service | 6, 10 | 1, 2, 3, 4 |
| 6 FastAPI app/routes | 8, 10 | 1, 2, 5 |
| 7 Frontend scaffold/tooling | 8, 9 | None |
| 8 Frontend API client/hooks | 9 | 6, 7 |
| 9 Chat UI | 10 | 7, 8 |
| 10 Repo automation/docs | Final Verification | 3, 5, 6, 9 |

### Agent Dispatch Summary (wave → task count → categories)
| Wave | Task Count | Categories |
| --- | ---: | --- |
| 1 | 5 | `unspecified-high` x4, `visual-engineering` x1 |
| 2 | 5 | `unspecified-high` x3, `visual-engineering` x1, `quick` x1 |
| Final | 4 review agents | `oracle`, `unspecified-high`, `unspecified-high`, `deep` |

## TODOs
> Implementation + Test = ONE task. Never separate.
> EVERY task MUST have: Agent Profile + Parallelization + QA Scenarios.

- [x] 1. Define API contracts and runtime bounds

  **What to do**: Create `app/api/__init__.py` and `app/api/models.py`. Define deterministic Pydantic models and enums exactly matching the `API Contract (V1, exact)` section: `TaskStatus`, `ToolProfile`, `TaskCreateRequest`, `TaskLinks`, `TaskCreateResponse`, `TaskStatusResponse`, `TaskResultResponse`, `TaskCancelResponse`, `TaskEvent`, `ApiError`, and `TaskEventsResponse`. Encode V1 defaults in one importable constant block inside `models.py`: backend host `127.0.0.1`, port `8000`, frontend origins `http://127.0.0.1:5173` and `http://localhost:5173`, max input length `20000`, default/max runtime `1800`, default/max steps `20/30`, max active tasks `4`, task TTL `3600`, event buffer max `200`. Add a helper for UTC `Z` datetime serialization. Add `tests/api/test_models.py` covering validation, default values, enum serialization, datetime format, and error/result shapes.
  **Must NOT do**: Do not add a database model, ORM, root settings framework, auth fields, user IDs, or persistent conversation schema. Do not change `app/schema.py`.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` - Reason: API contract decisions affect all backend and frontend work.
  - Skills: [] - No specialized skill required.
  - Omitted: [`frontend-design`] - Backend schema work only.

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: 2, 5, 6, 8 | Blocked By: None

  **References** (executor has NO interview context - be exhaustive):
  - Pattern: `app/schema.py:32-39` - Existing enum style for `AgentState`; mirror string enum clarity but use lowercase API statuses.
  - Pattern: `app/schema.py:54-97` - Existing Pydantic message serialization pattern to reuse for optional API `messages` output.
  - Dependency: `requirements.txt:8-15` - FastAPI and uvicorn already exist in runtime dependencies.
  - Dependency: `requirements.txt:28-32` - pytest, pytest-asyncio, and httpx already exist for API tests.
  - Boundary: `protocol/a2a/app/main.py:27-31` - Existing A2A capabilities are non-streaming; API contracts must not promise token streaming.

  **Acceptance Criteria** (agent-executable only):
  - [ ] `python -m pytest tests/api/test_models.py -q` passes.
  - [ ] Tests prove `ApiError` serializes as `{"error":{"code":"validation_error","message":"...","details":...}}`.
  - [ ] Tests prove datetimes serialize as UTC strings ending with `Z`.
  - [ ] Tests prove `TaskEvent` serializes with exactly `event_id`, `task_id`, `type`, `created_at`, and `payload` fields.
  - [ ] `python - <<'PY'
from app.api.models import TaskCreateRequest, TaskStatus, ToolProfile
r = TaskCreateRequest(message='hello')
assert r.max_steps == 20
assert r.timeout_seconds == 1800
assert r.tool_profile == ToolProfile.CHAT
assert TaskStatus.QUEUED.value == 'queued'
print('models-ok')
PY` prints `models-ok`.

  **QA Scenarios** (MANDATORY - task incomplete without these):
  ```
  Scenario: Valid default task request
    Tool: Bash
    Steps: Run the inline Python command from the acceptance criteria.
    Expected: Process exits 0 and prints exactly `models-ok`.
    Evidence: .omo/evidence/task-1-api-models.txt

  Scenario: Invalid oversized message
    Tool: Bash
    Steps: Instantiate TaskCreateRequest with message length 20001 in a pytest or inline Python check.
    Expected: Pydantic validation error mentions the message length constraint; process exits 0 after asserting the error.
    Evidence: .omo/evidence/task-1-api-models-error.txt
  ```

  **Commit**: NO | Message: `feat(api): define web task contracts` | Files: [`app/api/__init__.py`, `app/api/models.py`, `tests/api/test_models.py`]

- [x] 2. Implement in-memory task store and lifecycle event buffer

  **What to do**: Create `app/api/task_store.py` with an `InMemoryTaskStore` and `TaskRecord` that use the Task 1 models. Provide async-safe methods: `create_task(request)`, `get_task(task_id)`, `list_tasks()`, `mark_running(task_id)`, `mark_succeeded(task_id, final_text, raw_steps, messages)`, `mark_failed(task_id, error)`, `mark_cancelled(task_id, reason)`, `expire_old_tasks(now)`, `append_event(task_id, type, payload)`, `events_since(task_id, since_id)`, and `active_non_terminal_count()`. Use an `asyncio.Lock` to guard state; use UUID4 string task IDs; event IDs are monotonically increasing integers per task. Capacity counts `queued` + `running` tasks only. Add `tests/api/test_task_store.py` for lifecycle transitions, event buffer trimming to 200, TTL expiry after 3600s, unknown IDs, and active capacity helpers.
  **Must NOT do**: Do not introduce Redis, SQLite, files, database migrations, global mutable state outside the store instance, or multi-process guarantees.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` - Reason: Correct state transitions and concurrency safety are central to the web adapter.
  - Skills: [] - No specialized skill required.
  - Omitted: [`frontend-design`] - Backend storage work only.

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: 5, 6 | Blocked By: 1

  **References**:
  - API Contract: `app/api/models.py` - Created by Task 1; import statuses and response models from here.
  - Existing In-Memory Precedent: `protocol/a2a/app/main.py:83-90` - A2A uses in-memory task/push notification stores; V1 REST API is also intentionally in-memory.
  - State Boundary: `app/schema.py:32-39` - Existing agent states are not sufficient for HTTP task states; API store owns API task status.
  - Test Convention: `requirements.txt:28-29` - pytest and pytest-asyncio are available.

  **Acceptance Criteria**:
  - [ ] `python -m pytest tests/api/test_task_store.py -q` passes.
  - [ ] Store refuses illegal transition `succeeded → running` by raising a deterministic `ValueError` tested by `tests/api/test_task_store.py`.
  - [ ] `events_since(task_id, since_id)` returns only events with ID greater than `since_id` and preserves event order.
  - [ ] `active_non_terminal_count()` counts only `queued` and `running`, not terminal or expired tasks.

  **QA Scenarios**:
  ```
  Scenario: Happy lifecycle
    Tool: Bash
    Steps: Run `python -m pytest tests/api/test_task_store.py -q -k lifecycle`.
    Expected: queued -> running -> succeeded assertions pass and result fields are preserved.
    Evidence: .omo/evidence/task-2-store-lifecycle.txt

  Scenario: Expired terminal task
    Tool: Bash
    Steps: Run `python -m pytest tests/api/test_task_store.py -q -k expiry` with a mocked clock older than TTL.
    Expected: terminal task status becomes `expired`; non-terminal running task is not expired by TTL cleanup.
    Evidence: .omo/evidence/task-2-store-expiry.txt
  ```

  **Commit**: NO | Message: `feat(api): add in-memory task store` | Files: [`app/api/task_store.py`, `tests/api/test_task_store.py`]

- [x] 3. Add web-safe agent profiles with explicit tool allowlist

  **What to do**: Create `app/agent/web_manus.py`. Implement `WebManus(Manus)` that reuses Manus prompts while avoiding MCP auto-connect. Provide `build_web_tools(profile: ToolProfile, allow_dev_local: bool = False) -> ToolCollection` and `WebManus.create_for_web(profile, max_steps)` factory. Allowlist exactly: `chat` = `Terminate`; `browser` = `BrowserUseTool`, `Terminate`; `dev_local` = `PythonExecute`, `StrReplaceEditor`, `BrowserUseTool`, `Terminate` only when `allow_dev_local=True`. Ensure `AskHuman`, `Bash`, Daytona shell/sandbox tools, `ComputerUseTool`, and `MCPClientTool` never appear in any default web profile. Override MCP initialization so `Manus.think()` cannot lazily connect MCP in web mode. Add `tests/api/test_web_manus_guardrails.py` for all profiles and denied tools.
  **Must NOT do**: Do not edit `app/tool/ask_human.py`; do not remove tools from normal CLI `Manus`; do not enable `dev_local` by default; do not auto-connect MCP servers.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` - Reason: Security/tool exposure correctness is critical before exposing web requests.
  - Skills: [] - No specialized skill required.
  - Omitted: [`security-research`] - This is an implementation guardrail task, not a full vulnerability audit.

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: 5, 10 | Blocked By: None

  **References**:
  - Existing Tool Set: `app/agent/manus.py:33-42` - Current CLI Manus includes Python, browser, editor, AskHuman, and Terminate.
  - MCP Auto-Connect: `app/agent/manus.py:59-65` and `app/agent/manus.py:140-144` - Override this behavior in web mode.
  - Blocking Tool: `app/tool/ask_human.py:20-21` - Uses blocking terminal `input()` and must be excluded.
  - Tool Collection: `app/tool/tool_collection.py:15-23` - Use `ToolCollection` to expose schemas to the LLM.
  - Unknown Tool Behavior: `app/agent/toolcall.py:171-173` - Unknown tools already produce deterministic error text.

  **Acceptance Criteria**:
  - [ ] `python -m pytest tests/api/test_web_manus_guardrails.py -q` passes.
  - [ ] A test asserts `build_web_tools(ToolProfile.CHAT).tool_map.keys()` equals `{Terminate().name}`.
  - [ ] A test asserts `dev_local` without `allow_dev_local=True` raises a deterministic permission error.
  - [ ] A test asserts no allowed profile contains tool names `ask_human`, `bash`, `computer_use`, `sandbox_shell`, or any MCP client tool.

  **QA Scenarios**:
  ```
  Scenario: Chat profile safe allowlist
    Tool: Bash
    Steps: Run `python -m pytest tests/api/test_web_manus_guardrails.py -q -k chat_profile`.
    Expected: Only terminate is exposed and no MCP initialization is attempted.
    Evidence: .omo/evidence/task-3-web-manus-chat.txt

  Scenario: Unsafe dev_local blocked by default
    Tool: Bash
    Steps: Run `python -m pytest tests/api/test_web_manus_guardrails.py -q -k dev_local_requires_flag`.
    Expected: dev_local request without trusted flag raises the expected permission error.
    Evidence: .omo/evidence/task-3-web-manus-dev-local-error.txt
  ```

  **Commit**: NO | Message: `feat(agent): add web-safe manus profiles` | Files: [`app/agent/web_manus.py`, `tests/api/test_web_manus_guardrails.py`]

- [x] 4. Add optional agent progress callbacks and cancellation-safe cleanup

  **What to do**: Minimally adapt `app/agent/base.py` and `app/agent/toolcall.py` so `run()` accepts optional `on_event: Callable[[dict], Awaitable[None]] | None = None` without breaking existing CLI callers. Emit `agent.step.started` before each step and `agent.step.completed` after each step with `current_step` and `max_steps`. Move `SANDBOX_CLIENT.cleanup()` in `BaseAgent.run()` into a `finally` so cleanup happens on cancellation/failure. Keep `ToolCallAgent.run()` cleanup in a `finally`; the API service must rely on `WebManus.run()` owning cleanup and must not call `agent.cleanup()` separately after awaiting `run()`. Add tests using a tiny fake agent subclass; do not call real LLMs.
  **Must NOT do**: Do not rewrite the ReAct loop, change existing CLI behavior, or add token streaming. Do not print events to stdout.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` - Reason: This touches core agent execution and must preserve CLI compatibility.
  - Skills: [] - No specialized skill required.
  - Omitted: [`debugging`] - No runtime bug is being diagnosed; this is planned adaptation.

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: 5 | Blocked By: None

  **References**:
  - Base Loop: `app/agent/base.py:116-154` - Main step loop and current sandbox cleanup location.
  - Tool Cleanup: `app/agent/toolcall.py:229-250` - Tool cleanup already occurs in `ToolCallAgent.run()` finally.
  - Step Execution: `app/agent/toolcall.py:131-164` - Tool execution remains unchanged; events are lifecycle-only.
  - LLM Streaming Caveat: `app/llm.py:435-448` - Token streaming currently prints to stdout and must remain out of V1.
  - Tool Requests Non-Streaming: `app/llm.py:731-733` - Tool-call LLM requests are always non-streaming.

  **Acceptance Criteria**:
  - [ ] Existing CLI signature compatibility remains: `await agent.run('hello')` still works in tests.
  - [ ] `python -m pytest tests/api/test_agent_events.py -q` passes.
  - [ ] Cancellation test proves `SANDBOX_CLIENT.cleanup()` or its monkeypatched replacement is awaited once when `run()` is cancelled.
  - [ ] Event test proves callback receives `agent.step.started` then `agent.step.completed` for each fake step.

  **QA Scenarios**:
  ```
  Scenario: Event callback order
    Tool: Bash
    Steps: Run `python -m pytest tests/api/test_agent_events.py -q -k event_order`.
    Expected: started/completed event sequence matches step count and includes current_step/max_steps.
    Evidence: .omo/evidence/task-4-agent-events.txt

  Scenario: Cancellation cleanup
    Tool: Bash
    Steps: Run `python -m pytest tests/api/test_agent_events.py -q -k cancellation_cleanup`.
    Expected: cancelled run raises/captures CancelledError and cleanup mock is awaited exactly once.
    Evidence: .omo/evidence/task-4-agent-cancel-cleanup.txt
  ```

  **Commit**: NO | Message: `feat(agent): emit lifecycle events for web tasks` | Files: [`app/agent/base.py`, `app/agent/toolcall.py`, `tests/api/test_agent_events.py`]

- [x] 5. Implement backend task orchestration service

  **What to do**: Create `app/api/service.py` with `TaskService`. It owns an `InMemoryTaskStore`, an `agent_factory` defaulting to `WebManus.create_for_web`, a dict of running `asyncio.Task` handles, and methods `submit(request)`, `get_status(task_id)`, `get_result(task_id)`, `cancel(task_id)`, `shutdown()`, and `cleanup_expired()`. `submit()` first rejects `tool_profile:"dev_local"` with a deterministic service error mapped by routes to HTTP 403 `tool_profile_disabled` unless `OPENMANUS_API_ENABLE_DEV_LOCAL=1`; rejected tasks are not stored. Then `submit()` checks `active_non_terminal_count() < 4`; if not, return/raise a capacity error mapped by routes to HTTP 429 `capacity_exceeded` and do not store the request. If capacity is available, store queued task, schedule execution with `asyncio.create_task`, and return a 202 response. Execution must mark running, emit lifecycle events, call `await asyncio.wait_for(agent.run(message, on_event=...), timeout_seconds)`, derive `final_text` from the last non-empty assistant message (`agent.messages` from `app/agent/base.py:188-196`) falling back to `raw_steps`, and persist result/error. Cleanup is owned by `WebManus.run()`/`ToolCallAgent.run()`; `TaskService` must not call `agent.cleanup()` separately after `run()`. Timeouts become `failed` with error code `timeout`; cancellations become `cancelled`. `shutdown()` must cancel all non-done running asyncio tasks, `await asyncio.gather(..., return_exceptions=True)`, and leave store records in `cancelled` or terminal states. Add `tests/api/test_task_service.py` with mocked agent factory and no real LLM.
  **Must NOT do**: Do not run real `Manus` in tests, do not persist tasks to disk, do not expose multi-turn backend sessions, and do not swallow cancellation without marking task state.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` - Reason: This is the central backend orchestration layer.
  - Skills: [] - No specialized skill required.
  - Omitted: [`frontend-design`] - Backend service task only.

  **Parallelization**: Can Parallel: NO | Wave 2 | Blocks: 6, 10 | Blocked By: 1, 2, 3, 4

  **References**:
  - CLI Pattern: `main.py:16-32` - Existing lifecycle is create agent, run prompt, cleanup in finally.
  - Agent Messages: `app/agent/base.py:188-196` - Use `agent.messages` for final assistant fallback extraction.
  - ToolCall Cleanup: `app/agent/toolcall.py:245-250` - Be careful not to double-clean non-idempotent resources.
  - Web Agent: `app/agent/web_manus.py` - Created by Task 3; use web-safe profile factory only.
  - Task Store: `app/api/task_store.py` - Created by Task 2; source of API task status.

  **Acceptance Criteria**:
  - [ ] `python -m pytest tests/api/test_task_service.py -q` passes.
  - [ ] Submit success test returns a task ID immediately before mocked agent completion.
  - [ ] Result test returns final assistant text when messages include an assistant message, otherwise `raw_steps`.
  - [ ] Failure test stores structured error and emits `task.failed`.
  - [ ] Timeout test marks task `failed` with timeout error and cleanup called once.
  - [ ] Cancel test calls running asyncio task cancellation and marks task `cancelled`.
  - [ ] Burst-concurrency test returns a deterministic capacity error when active non-terminal count is already `4` and confirms the 5th task is not stored.
  - [ ] Disabled `dev_local` test returns/maps `tool_profile_disabled` and confirms the rejected task is not stored.
  - [ ] Shutdown test cancels all active asyncio tasks, awaits them via gather, and records cleanup ownership exactly once through mocked `run()` cleanup counters.

  **QA Scenarios**:
  ```
  Scenario: Mocked agent completes
    Tool: Bash
    Steps: Run `python -m pytest tests/api/test_task_service.py -q -k submit_success`.
    Expected: Service transitions queued -> running -> succeeded, stores final_text, and emits queued/started/completed events.
    Evidence: .omo/evidence/task-5-service-success.txt

  Scenario: Mocked agent timeout
    Tool: Bash
    Steps: Run `python -m pytest tests/api/test_task_service.py -q -k timeout`.
    Expected: Service stores status `failed`, error code `timeout`, and cleanup mock is awaited exactly once.
    Evidence: .omo/evidence/task-5-service-timeout.txt

  Scenario: Shutdown cancels active work
    Tool: Bash
    Steps: Run `python -m pytest tests/api/test_task_service.py -q -k shutdown_cancels_and_awaits`.
    Expected: All running mocked tasks receive cancellation, gather completes, and records end in cancelled or pre-existing terminal states.
    Evidence: .omo/evidence/task-5-service-shutdown.txt
  ```

  **Commit**: NO | Message: `feat(api): orchestrate web agent tasks` | Files: [`app/api/service.py`, `tests/api/test_task_service.py`]

- [x] 6. Wire FastAPI app, routes, CORS, and health endpoint

  **What to do**: Create `app/api/main.py` and `app/api/routes.py`. `main.py` defines `create_app(task_service: TaskService | None = None) -> FastAPI` and module-level `app = create_app()`. It attaches CORS middleware for only `http://127.0.0.1:5173` and `http://localhost:5173`, creates one `TaskService` in lifespan startup when no service is injected, cancels tasks on shutdown, and mounts routes. Add deterministic mocked-agent mode for tests/local QA: when `OPENMANUS_API_MOCK_AGENT=1`, the service uses a fake async agent that never calls a real LLM and returns `mocked response: <message>`. `routes.py` implements: `GET /health`, `POST /api/tasks`, `GET /api/tasks/{task_id}`, `GET /api/tasks/{task_id}/result`, `POST /api/tasks/{task_id}/cancel`, and `GET /api/tasks/{task_id}/events`. The events endpoint returns JSON `{ "events": [TaskEvent], "latest_event_id": int }` filtered by optional `?since=<id>`; do not implement SSE or token streaming in V1. Add `tests/api/test_routes.py` using FastAPI/httpx ASGI client and injected mocked service/agent.
  **Must NOT do**: Do not alter `protocol/a2a/app/main.py`; do not expose A2A JSON-RPC as the React API; do not enable wildcard CORS; do not start uvicorn from import time.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` - Reason: Public API behavior and lifecycle need precise tests.
  - Skills: [] - No specialized skill required.
  - Omitted: [`playwright`] - Backend route tests use httpx/pytest, not browser automation.

  **Parallelization**: Can Parallel: NO | Wave 2 | Blocks: 8, 10 | Blocked By: 1, 2, 5

  **References**:
  - Existing Service Style: `protocol/a2a/app/main.py:27-31` - A2A declares non-streaming; keep REST API explicit about V1 JSON lifecycle-only events.
  - Uvicorn Pattern: `protocol/a2a/app/main.py:103-111` - Reference only for local server command; API app should be importable as `app.api.main:app`.
  - Requirements: `requirements.txt:8-15` and `requirements.txt:32` - FastAPI, uvicorn, and httpx are available.
  - Docker Current State: `Dockerfile:11-13` - Current image starts bash; do not treat Docker deployment as done.

  **Acceptance Criteria**:
  - [ ] `python -m pytest tests/api/test_routes.py -q` passes.
  - [ ] `OPENMANUS_API_MOCK_AGENT=1 python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000` starts without import errors and without real LLM calls.
  - [ ] `curl -s http://127.0.0.1:8000/health` returns exactly `{"status":"ok"}`.
  - [ ] `POST /api/tasks` returns HTTP 202 with `task_id`, `status`, `created_at`, `updated_at`, and links for status/result/cancel/events.
  - [ ] `POST /api/tasks` with `{"message":"x","tool_profile":"dev_local"}` returns HTTP 403 `tool_profile_disabled` unless `OPENMANUS_API_ENABLE_DEV_LOCAL=1` is set.
  - [ ] `GET /api/tasks/not-real` returns HTTP 404 with structured `ApiError` JSON.
  - [ ] `GET /api/tasks/{id}/result` returns HTTP 409 before terminal status and HTTP 200 after `succeeded|failed|cancelled`.

  **QA Scenarios**:
  ```
  Scenario: Route happy path with mocked agent
    Tool: Bash
    Steps: Start `OPENMANUS_API_MOCK_AGENT=1 python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000`; run curl POST `/api/tasks` with `{"message":"Hello from route QA"}`, poll status, then get result.
    Expected: POST returns 202, status eventually `succeeded`, result endpoint returns 200 JSON with `final_text` equal to `mocked response: Hello from route QA`.
    Evidence: .omo/evidence/task-6-routes-happy.txt

  Scenario: Unknown task error
    Tool: Bash
    Steps: Run `curl -i http://127.0.0.1:8000/api/tasks/not-real`.
    Expected: HTTP status 404 and JSON body includes deterministic error code such as `task_not_found`.
    Evidence: .omo/evidence/task-6-routes-404.txt
  ```

  **Commit**: NO | Message: `feat(api): expose task routes` | Files: [`app/api/main.py`, `app/api/routes.py`, `tests/api/test_routes.py`]

- [x] 7. Scaffold isolated pnpm Vite React TypeScript frontend

  **What to do**: Populate the empty `frontEnd/` directory with a standalone Vite React TypeScript project using pnpm. Required files: `frontEnd/package.json`, `frontEnd/pnpm-lock.yaml`, `frontEnd/index.html`, `frontEnd/vite.config.ts`, `frontEnd/tsconfig.json`, `frontEnd/tsconfig.node.json`, `frontEnd/eslint.config.js`, `frontEnd/src/main.tsx`, `frontEnd/src/App.tsx`, `frontEnd/src/styles.css`, `frontEnd/src/types.ts`, `frontEnd/src/api.ts`, `frontEnd/src/__tests__/App.test.tsx`, and `frontEnd/playwright.config.ts`. Scripts: `dev`, `build`, `preview`, `lint`, `typecheck`, `test`, and `test:e2e`. Set Node engine `>=18`. Use `VITE_API_BASE_URL` defaulting to `http://127.0.0.1:8000`; do not rely on a Vite proxy in V1.
  **Must NOT do**: Do not add root `package.json`, root `pnpm-workspace.yaml`, or modify `app/tool/chart_visualization/package.json` / `package-lock.json`.

  **Recommended Agent Profile**:
  - Category: `visual-engineering` - Reason: Frontend structure and design foundation should be intentional, accessible, and polished.
  - Skills: [`frontend-design`] - Needed for distinctive, production-grade UI direction without generic AI aesthetics.
  - Omitted: [`fanka-lp-basic-rule`] - Not a Fanka landing page.

  **Parallelization**: Can Parallel: YES | Wave 1 | Blocks: 8, 9 | Blocked By: None

  **References**:
  - Target Directory: `frontEnd/` - Exists and is empty; all frontend project files go here.
  - Existing Node Isolation: `app/tool/chart_visualization/package.json:1-18` - Separate npm package; do not convert or reuse its lockfile.
  - Node Version Guidance: `app/tool/chart_visualization/README.md` - Existing docs require Node >=18; match that baseline.
  - Repo Ignore: `.gitignore:23-24` - `dist/` is already ignored globally.
  - Editor Style: `.vscode/settings.json:17-19` - Final newline and trim trailing whitespace are expected.

  **Acceptance Criteria**:
  - [ ] `pnpm --dir frontEnd install` completes and produces `frontEnd/pnpm-lock.yaml`.
  - [ ] `pnpm --dir frontEnd typecheck` passes.
  - [ ] `pnpm --dir frontEnd lint` passes.
  - [ ] `pnpm --dir frontEnd test` passes with at least one App smoke test.
  - [ ] `pnpm --dir frontEnd build` passes.
  - [ ] `test -f frontEnd/package.json && test ! -f package.json` verifies the frontend is isolated from the root.

  **QA Scenarios**:
  ```
  Scenario: Frontend project builds in isolation
    Tool: Bash
    Steps: Run `pnpm --dir frontEnd install && pnpm --dir frontEnd typecheck && pnpm --dir frontEnd build`.
    Expected: All commands exit 0; no root package.json or pnpm workspace is created.
    Evidence: .omo/evidence/task-7-frontend-build.txt

  Scenario: Existing chart package remains untouched
    Tool: Bash
    Steps: Run `git diff -- app/tool/chart_visualization/package.json app/tool/chart_visualization/package-lock.json` after scaffolding.
    Expected: No diff for both chart visualization package files.
    Evidence: .omo/evidence/task-7-chart-package-untouched.txt
  ```

  **Commit**: NO | Message: `feat(frontend): scaffold chat app` | Files: [`frontEnd/package.json`, `frontEnd/pnpm-lock.yaml`, `frontEnd/index.html`, `frontEnd/vite.config.ts`, `frontEnd/tsconfig.json`, `frontEnd/tsconfig.node.json`, `frontEnd/eslint.config.js`, `frontEnd/src/**`, `frontEnd/playwright.config.ts`]

- [x] 8. Build frontend API client, polling state, and cancellation logic

  **What to do**: Implement `frontEnd/src/types.ts` and `frontEnd/src/api.ts` matching the backend Task 1 models exactly. Implement typed functions `createTask(message)`, `getTask(taskId)`, `getTaskResult(taskId)`, `cancelTask(taskId)`, and `getEvents(taskId, since?)` for JSON lifecycle event replay. In `frontEnd/src/App.tsx` or a dedicated hook file, implement task lifecycle state: local user message append, submit disabled while empty/running, poll `GET /api/tasks/{id}` every 1000ms while `queued|running`, fetch result on terminal status, render structured errors, and cancel active task via `POST /api/tasks/{id}/cancel`. Use `AbortController` to cancel in-flight fetches on unmount.
  **Must NOT do**: Do not call Python or agent code directly from frontend; do not store API keys in frontend; do not implement login/auth; do not fake success without using backend response shape.

  **Recommended Agent Profile**:
  - Category: `unspecified-high` - Reason: API/client state correctness determines whether the UI works against the backend.
  - Skills: [] - TypeScript client work only.
  - Omitted: [`frontend-design`] - Visual polish is handled in Task 9.

  **Parallelization**: Can Parallel: NO | Wave 2 | Blocks: 9 | Blocked By: 6, 7

  **References**:
  - API Models: `app/api/models.py` - Created by Task 1; frontend types must mirror names and JSON shapes.
  - API Routes: `app/api/routes.py` - Created by Task 6; client paths must match exactly.
  - Frontend Scaffold: `frontEnd/src/App.tsx` and `frontEnd/src/api.ts` - Created by Task 7.
  - V1 Scope: This plan's Must NOT Have section - no token streaming or auth.

  **Acceptance Criteria**:
  - [ ] `pnpm --dir frontEnd typecheck` passes with no `any` for API response models unless explicitly justified in comments.
  - [ ] Frontend `TaskEvent` type has exactly `event_id`, `task_id`, `type`, `created_at`, and `payload` fields matching backend JSON.
  - [ ] `pnpm --dir frontEnd test -- --run` passes tests for submit, polling success, error rendering, and cancellation using mocked fetch.
  - [ ] Fetch client uses `VITE_API_BASE_URL` fallback `http://127.0.0.1:8000`.
  - [ ] Polling stops after `succeeded`, `failed`, `cancelled`, or `expired`.

  **QA Scenarios**:
  ```
  Scenario: Polling success with mocked fetch
    Tool: Bash
    Steps: Run `pnpm --dir frontEnd test -- --run -t polling`.
    Expected: Mocked queued/running/succeeded sequence renders final assistant text and stops polling.
    Evidence: .omo/evidence/task-8-frontend-polling.txt

  Scenario: Cancel active task
    Tool: Bash
    Steps: Run `pnpm --dir frontEnd test -- --run -t cancel`.
    Expected: Cancel button calls `/api/tasks/{task_id}/cancel`, active request is aborted or state is terminal, and UI shows cancelled state.
    Evidence: .omo/evidence/task-8-frontend-cancel.txt
  ```

  **Commit**: NO | Message: `feat(frontend): connect chat client to task api` | Files: [`frontEnd/src/types.ts`, `frontEnd/src/api.ts`, `frontEnd/src/App.tsx`, `frontEnd/src/__tests__/**`]

- [x] 9. Design and implement the React chat interface

  **What to do**: Implement a polished but focused chat UI in `frontEnd/src/App.tsx` and `frontEnd/src/styles.css`. Required UX: message transcript with user/assistant/system/error bubbles, textarea input with Enter-to-submit and Shift+Enter newline, submit button, cancel button visible only for active task, task status badge, disabled state during submit, empty state explaining V1 limitations, and accessible live region for status changes. Use a distinctive visual direction suitable for an agent control console: refined dark command-center theme, high-contrast readable typography, subtle grid/noise background, clear focus rings, responsive layout for desktop and mobile. Keep local browser-session message history only; no persistence required.
  **Must NOT do**: Do not build a full ChatGPT clone with accounts, sidebars, model picker, file upload, plugin marketplace, or settings screen. Do not use generic purple-gradient AI styling. Do not add external fonts that require network access at runtime unless self-hosted or system-safe fallback is provided.

  **Recommended Agent Profile**:
  - Category: `visual-engineering` - Reason: Requires accessible, distinctive frontend UI implementation.
  - Skills: [`frontend-design`] - Required to avoid generic AI aesthetics and produce production-grade UI.
  - Omitted: [`web-design-guidelines`] - Use only if a separate UI audit is requested; task includes its own QA.

  **Parallelization**: Can Parallel: NO | Wave 2 | Blocks: 10 | Blocked By: 7, 8

  **References**:
  - Frontend Source: `frontEnd/src/App.tsx` and `frontEnd/src/styles.css` - Created by Task 7.
  - Frontend API State: `frontEnd/src/api.ts` and `frontEnd/src/types.ts` - Created/updated by Task 8.
  - Editor Style: `.vscode/settings.json:17-19` - Final newline and whitespace cleanup expected.
  - PR Evidence Convention: `.github/PULL_REQUEST_TEMPLATE.md` - If present, screenshots/logs should be attached for frontend changes.

  **Acceptance Criteria**:
  - [ ] `pnpm --dir frontEnd lint` passes.
  - [ ] `pnpm --dir frontEnd test -- --run` passes UI interaction tests.
  - [ ] `pnpm --dir frontEnd build` passes.
  - [ ] Keyboard test confirms Enter submits and Shift+Enter inserts newline.
  - [ ] Accessibility test confirms textarea has an accessible label, status changes use `aria-live`, and buttons have discernible names.
  - [ ] Focus-style test uses Playwright `getComputedStyle()` to assert focused textarea and buttons have either non-`none` outline or non-`none` box-shadow.
  - [ ] Responsive test at `390x844` and `1440x900` asserts no horizontal overflow (`document.documentElement.scrollWidth <= window.innerWidth`) and textarea, submit button, transcript, and status badge are visible.

  **QA Scenarios**:
  ```
  Scenario: Chat submit interaction
    Tool: Playwright
    Steps: Open `http://127.0.0.1:5173` with Playwright route interception for `/api/tasks*`; locate textarea by label `Message`; type `Hello from QA`; press Enter; wait for user bubble and running status.
    Expected: User bubble contains `Hello from QA`, submit button disables while active, and a status badge shows `queued` or `running`.
    Evidence: .omo/evidence/task-9-chat-submit.png

  Scenario: Empty and error states
    Tool: Playwright
    Steps: Open frontend with mocked backend returning 500 for submit; click submit with empty textarea, then type `force error` and submit.
    Expected: Empty submit is blocked with no network request; backend error renders a visible error bubble with deterministic text and input re-enables.
    Evidence: .omo/evidence/task-9-chat-error.png
  ```

  **Commit**: NO | Message: `feat(frontend): implement chat interface` | Files: [`frontEnd/src/App.tsx`, `frontEnd/src/styles.css`, `frontEnd/src/__tests__/**`]

- [x] 10. Add integration QA, repo ignore updates, and frontend automation hooks

  **What to do**: Add missing ignore patterns to root `.gitignore`: `.vite/`, `coverage/`, and `*.tsbuildinfo` without removing existing Python rules. Add `.github/workflows/frontend-ci.yaml` that checks out code, sets up Node >=18 with pnpm, runs `pnpm --dir frontEnd install --frozen-lockfile`, `pnpm --dir frontEnd typecheck`, `pnpm --dir frontEnd lint`, `pnpm --dir frontEnd test -- --run`, and `pnpm --dir frontEnd build`. Add a Dependabot npm entry for `/frontEnd` only. Add or update a small `frontEnd/README.md` documenting dev commands and backend URL. Add Playwright e2e tests under `frontEnd/e2e/chat.spec.ts` for submit, result, error, and cancel flows using mocked network route interception.
  **Must NOT do**: Do not modify existing Python release workflows except by adding a new frontend workflow. Do not add Git LFS assets or large media. Do not add a root workspace or touch chart visualization package locks.

  **Recommended Agent Profile**:
  - Category: `quick` - Reason: Repo automation and docs are straightforward once frontend/backend tasks exist.
  - Skills: [`playwright`] - Needed for browser QA flow definitions and screenshots.
  - Omitted: [`git-master`] - No git commit operation requested.

  **Parallelization**: Can Parallel: NO | Wave 2 | Blocks: Final Verification | Blocked By: 3, 5, 6, 9

  **References**:
  - Current Ignore: `.gitignore:23-24` and `.gitignore:50-63` - `dist` and Python coverage are already covered; add frontend-specific patterns without duplicates.
  - Current Pre-commit CI: `.github/workflows/pre-commit.yaml:21-26` - Existing CI runs pre-commit only; add separate frontend CI.
  - Current Dependabot: `.github/dependabot.yml:1-58` - Add npm ecosystem entry for `/frontEnd` only.
  - Pre-commit Hooks: `.pre-commit-config.yaml:7-13` - YAML, whitespace, EOF, and large-file hooks will inspect new files.
  - Existing Docker: `Dockerfile:1-13` - Do not claim Docker deployment is complete in frontend README.

  **Acceptance Criteria**:
  - [ ] `python -m pytest tests/api -q` passes.
  - [ ] `pnpm --dir frontEnd install --frozen-lockfile` passes after lockfile generation.
  - [ ] `pnpm --dir frontEnd typecheck && pnpm --dir frontEnd lint && pnpm --dir frontEnd test -- --run && pnpm --dir frontEnd build` passes.
  - [ ] `pnpm --dir frontEnd test:e2e` passes with Playwright mocked network route interception.
  - [ ] `pre-commit run --all-files` passes.
  - [ ] `git diff -- app/tool/chart_visualization/package.json app/tool/chart_visualization/package-lock.json` shows no changes.

  **QA Scenarios**:
  ```
  Scenario: End-to-end chat with mocked backend
    Tool: Playwright
    Steps: Run `pnpm --dir frontEnd test:e2e -- --project=chromium`; test intercepts `/api/tasks*`, submits `Hello from QA`, returns queued/running/succeeded/result.
    Expected: Browser shows user message, running state, final assistant response, and no console errors.
    Evidence: .omo/evidence/task-10-e2e-chat.png

  Scenario: Full local quality gate
    Tool: Bash
    Steps: Run `python -m pytest tests/api -q && pnpm --dir frontEnd typecheck && pnpm --dir frontEnd lint && pnpm --dir frontEnd test -- --run && pnpm --dir frontEnd build`.
    Expected: All commands exit 0; failures include actionable output and no real LLM/network API calls are made.
    Evidence: .omo/evidence/task-10-quality-gate.txt
  ```

  **Commit**: NO | Message: `ci(frontend): add chat app validation` | Files: [`.gitignore`, `.github/workflows/frontend-ci.yaml`, `.github/dependabot.yml`, `frontEnd/README.md`, `frontEnd/e2e/**`, `frontEnd/playwright.config.ts`]

## Final Verification Wave (MANDATORY — after ALL implementation tasks)
> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.
> **Do NOT auto-proceed after verification. Wait for user's explicit approval before marking work complete.**
> **Never mark F1-F4 as checked before getting user's okay.** Rejection or user feedback -> fix -> re-run -> present again -> wait for okay.
- [x] F1. Plan Compliance Audit — oracle
- [x] F2. Code Quality Review — unspecified-high
- [x] F3. Agent-Executed Browser QA — unspecified-high (+ playwright for frontend)
- [x] F4. Scope Fidelity Check — deep

## Commit Strategy
- Do not commit automatically. The user has not requested commits.
- If the user later asks for a commit, inspect `git status`, `git diff`, and recent log first; stage only intended backend/frontend/CI/doc files.
- Suggested eventual commit message: `feat(api): add web task service and chat frontend`.

## Success Criteria
- All TODO acceptance criteria pass.
- Final Verification Wave agents all approve.
- The user explicitly approves the consolidated final verification results.
- No source files outside listed implementation scope are changed except generated lockfiles/config files explicitly listed in this plan.
