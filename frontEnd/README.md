# OpenManus Frontend

`frontEnd/` is an isolated Vite, React, and TypeScript chat UI for the V1 OpenManus task API. Run all package commands with `pnpm --dir frontEnd` from the repository root so no root Node workspace files are needed.

## Local Development

Install dependencies from the checked-in lockfile:

```bash
pnpm --dir frontEnd install --frozen-lockfile
```

Start the Vite dev server on `http://127.0.0.1:5173`:

```bash
pnpm --dir frontEnd dev
```

Build and preview the production bundle locally:

```bash
pnpm --dir frontEnd build
pnpm --dir frontEnd preview
```

## Backend URL

The browser client defaults to the local FastAPI API at `http://127.0.0.1:8000`. Override it only for local testing with `VITE_API_BASE_URL`:

```bash
VITE_API_BASE_URL=http://127.0.0.1:8000 pnpm --dir frontEnd dev
```

The frontend calls these V1 endpoints directly:

- `POST /api/tasks`
- `GET /api/tasks/{task_id}`
- `GET /api/tasks/{task_id}/result`
- `POST /api/tasks/{task_id}/cancel`

## Mock-Agent Backend Mode

Use mocked-agent mode for local browser checks that must not call a real agent or LLM:

```bash
OPENMANUS_API_MOCK_AGENT=1 python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
```

If this repository is being verified through `uv`, keep the same environment variable and run the equivalent `uv run python -m uvicorn ...` command.

## Test Commands

Run the same frontend quality gates used by CI:

```bash
pnpm --dir frontEnd typecheck
pnpm --dir frontEnd lint
pnpm --dir frontEnd test -- --run
pnpm --dir frontEnd build
```

Run browser e2e coverage with mocked route interception, without a running backend:

```bash
pnpm --dir frontEnd test:e2e
```

## V1 Limitations

- One backend task is created for each submitted message.
- The transcript is local to the current browser session and is not persisted.
- Task status uses polling; token streaming is not part of V1.
- Authentication, accounts, multi-user state, and shared history are outside V1.
- Automated browser tests intercept `http://127.0.0.1:8000/api/tasks*` and must not call real agents or LLMs.
