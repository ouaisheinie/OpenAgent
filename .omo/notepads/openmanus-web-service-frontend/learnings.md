# Learnings

## 2026-06-23 Task: start-work
- Plan path: `.omo/plans/openmanus-web-service-frontend.md`.
- V1 scope: FastAPI adapter plus React TypeScript pnpm frontend in `frontEnd/`.
- QA must not call real LLMs; use mocked agent mode or Playwright route interception.

## 2026-06-23 Task 7: frontend scaffold
- `frontEnd/` is now an isolated pnpm Vite React TypeScript app; keep future frontend package manager commands scoped with `pnpm --dir frontEnd`.
- The API skeleton defaults `VITE_API_BASE_URL` to `http://127.0.0.1:8000`, so early UI tests do not need a running backend.
- Root package manager files remain absent, and `app/tool/chart_visualization/` stayed on its existing npm/package-lock boundary.

## 2026-06-23 Task 1: API contracts and runtime bounds
- `app/api/models.py` now owns the V1 API constants, task enums, event names, request/response contracts, and UTC microsecond `Z` datetime serialization.
- `ApiError` accepts `code`/`message`/`details` construction while serializing to the required `{"error": {"code", "message", "details"}}` wrapper.
- The local shell has no bare `python` executable, so verification used `uv run --offline ... python` with cached `pydantic`/`pytest` packages and wrote evidence under `.omo/evidence/`.

## 2026-06-23 Task 4: agent events and cleanup
- `BaseAgent.run()` is the right backend seam for `on_event`; fake-agent tests can validate step event ordering without LLM calls.
- Sandbox cleanup must stay inside `BaseAgent.run()` so future service callers do not need a second cleanup path after cancellation or failure.

## 2026-06-24 Task 3: web-safe Manus profiles
- `app/agent/web_manus.py` is the web boundary for agent tool safety: use `build_web_tools()` or `WebManus.create_for_web()` instead of `Manus.create()` so MCP auto-connect stays disabled.
- The mandated minimal `uv run --with pytest --with pytest-asyncio --with pydantic ...` environment does not include the full OpenManus dependency chain, so guardrail tests use local import stubs and do not call LLM, browser, shell, or MCP services.

## 2026-06-24 Task 2: in-memory task store
- `app/api/task_store.py` now owns `TaskRecord`/`InMemoryTaskStore`; task IDs are UUID4 strings and event IDs are per-task 1-based counters trimmed to the newest 200 events.
- Terminal task expiry is driven by each record's `expires_at` (`completed_at + TASK_TTL_SECONDS`), while queued/running tasks remain non-terminal and count toward active capacity.

## 2026-06-24 Task 5: backend task orchestration service
- `app/api/service.py` keeps WebManus behind a lazy default factory; tests can import TaskService with only `pytest`, `pytest-asyncio`, and `pydantic` installed.
- Task orchestration now relies on injected fake agents in tests and never calls `agent.cleanup()` separately, preserving cleanup ownership inside `agent.run()`.
- Required Task 5 evidence files were written under `.omo/evidence/` for submit success, timeout handling, and shutdown cancellation.

## 2026-06-24 Task 6: FastAPI routes and CORS
- `app/api/main.py` owns the FastAPI app factory, strict frontend CORS allowlist, validation error wrapper, and `OPENMANUS_API_MOCK_AGENT=1` fake-agent mode for curl QA without real LLM calls.
- `app/api/routes.py` keeps the V1 API lifecycle-only REST surface on top of `TaskService`; events are JSON polling only and expired event reads return `task_expired` 410.
- Route tests and curl evidence use injected or mocked agents only; evidence files are `.omo/evidence/task-6-routes-happy.txt` and `.omo/evidence/task-6-routes-404.txt`.

## 2026-06-24 Task 8: frontend API polling and cancellation
- Frontend API models in `frontEnd/src/types.ts` mirror the V1 task lifecycle JSON with `unknown`/`Record<string, unknown>` for backend `Any` fields and no `any` response models.
- `frontEnd/src/App.tsx` owns local-only chat transcript state; active tasks poll `/api/tasks/{task_id}` every 1000ms and fetch `/result` only when terminal statuses need a result payload.
- Frontend tests mock `fetch`; fake-timer polling/cancel tests use synchronous DOM events, while user-event remains for real-timer form validation/error cases.

## 2026-06-24 Task 8 lint follow-up
- React hook cleanup effects that close over mutable refs should copy `ref.current` into a stable local variable inside the effect, then use that local in cleanup to satisfy `react-hooks/exhaustive-deps` without changing unmount behavior.

## 2026-06-24 Task 9: React chat interface
- `frontEnd/src/App.tsx` now keeps the Task 8 create/poll/result/cancel lifecycle while rendering local transcript roles as user, assistant, system, and error bubbles without changing the shared API models.
- The V1 chat composer uses a labelled textarea where Enter submits and Shift+Enter inserts a newline; browser QA should keep intercepting `http://127.0.0.1:8000/api/tasks*` because the frontend does not rely on a Vite proxy.
- Task 9 browser evidence screenshots are `.omo/evidence/task-9-chat-submit.png` and `.omo/evidence/task-9-chat-error.png`; the mocked 503 error path intentionally appears as a browser network error while still rendering the structured error bubble.

## 2026-06-24 Task 9 follow-up: stale terminal status
- `frontEnd/src/App.tsx` should clear the prior terminal task as soon as a new create-task submit starts; otherwise a failed create request can render a fresh error bubble while the status badge still reports the old succeeded task.
- Terminal adapter summaries should use explicit terminal wording such as `Completed task`, `Failed task`, `Cancelled task`, or `Expired task`; only queued/running tasks should show live agent state or step progress.

## 2026-06-24 Task 10: repo automation and frontend e2e
- Frontend automation remains isolated under `frontEnd/`: CI uses `corepack enable`, `pnpm install --frozen-lockfile`, then typecheck, lint, Vitest, and build without root Node workspace files.
- Playwright e2e coverage in `frontEnd/e2e/chat.spec.ts` route-intercepts `http://127.0.0.1:8000/api/tasks*` plus nested task endpoints, so browser tests require no running backend, real OpenManus agent, or LLM credentials.
- Task 10 evidence paths are `.omo/evidence/task-10-e2e-chat.png` for the happy browser path and `.omo/evidence/task-10-quality-gate.txt` for verification output.

## 2026-06-24 Task 10 follow-up: Playwright artifact cleanup
- Root `.gitignore` now ignores `frontEnd/playwright-report/` and `frontEnd/test-results/`, so rerunning `pnpm --dir frontEnd test:e2e` can regenerate Playwright HTML reports and last-run metadata without dirtying git status.
- Frontend CI commands intentionally run from the repository root with explicit `pnpm --dir frontEnd ...` invocations to match the Task 10 plan wording.

## 2026-06-24 Final wave fix: running cancel contract
- Running task cancellation is asynchronous: the route returns HTTP 202 while the body can remain `status:"running"`, and frontend cancel handling must keep polling until a later status response reaches `cancelled` before fetching `/result`.
