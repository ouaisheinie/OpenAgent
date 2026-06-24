import { mkdirSync } from 'node:fs';
import { env } from 'node:process';

import { expect, test, type Page, type Request, type Route } from '@playwright/test';

const API_BASE_URL = 'http://127.0.0.1:8000';
const TASKS_ROUTE = `${API_BASE_URL}/api/tasks*`;
const TASKS_NESTED_ROUTE = `${API_BASE_URL}/api/tasks/**`;
const CREATED_AT = '2026-06-24T10:00:00.000000Z';
const UPDATED_AT = '2026-06-24T10:00:01.000000Z';
const STARTED_AT = '2026-06-24T10:00:02.000000Z';
const COMPLETED_AT = '2026-06-24T10:00:03.000000Z';
const EVIDENCE_SCREENSHOT = '../.omo/evidence/task-10-e2e-chat.png';

type TaskStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'expired';

interface MockApiRequest {
  method: string;
  path: string;
  body: unknown;
}

interface MockApiResponse {
  status?: number;
  body: unknown;
}

if (env.VITEST !== undefined) {
  const { expect: expectVitest, test: testVitest } = await import('vitest');
  testVitest('is reserved for Playwright e2e execution', () => {
    expectVitest(env.VITEST).toBeDefined();
  });
} else {
test('submits a message and renders the final assistant result without console errors', async ({
  page,
}) => {
  const unexpectedErrors = collectUnexpectedErrors(page);
  const taskId = 'task-e2e-happy';
  let statusRequestCount = 0;
  const requests = await interceptTasksApi(page, (request) => {
    if (request.method === 'POST' && request.path === '/api/tasks') {
      return jsonApiResponse(taskCreateResponse(taskId), 202);
    }

    if (request.method === 'GET' && request.path === `/api/tasks/${taskId}`) {
      statusRequestCount += 1;
      if (statusRequestCount === 1) {
        return jsonApiResponse(
          taskStatusResponse(taskId, 'running', {
            agent_state: 'working',
            current_step: 1,
            latest_event_id: 2,
          }),
        );
      }

      return jsonApiResponse(
        taskStatusResponse(taskId, 'succeeded', {
          current_step: 2,
          latest_event_id: 3,
        }),
      );
    }

    if (request.method === 'GET' && request.path === `/api/tasks/${taskId}/result`) {
      return jsonApiResponse(
        taskResultResponse(taskId, 'succeeded', {
          final_text: 'QA response from mocked route interception.',
          messages: [
            {
              role: 'assistant',
              content: 'QA response from mocked route interception.',
            },
          ],
        }),
      );
    }

    throw unexpectedRequestError(request);
  });

  await page.goto('/');
  await page.getByRole('textbox', { name: 'Message' }).fill('Hello from QA');
  await page.getByRole('button', { name: /send message/i }).click();

  await expect(page.locator('.message--operator')).toContainText('Hello from QA');
  await expect(page.getByLabel(/task status: queued/i)).toBeVisible();
  await expect(page.getByRole('button', { name: /cancel task/i })).toBeEnabled();
  await expect(page.getByLabel(/task status: running/i)).toBeVisible();
  await expect(page.getByText('working')).toBeVisible();
  await expect(page.locator('.message--agent')).toContainText(
    'QA response from mocked route interception.',
  );
  await expect(page.getByLabel(/task status: succeeded/i)).toBeVisible();
  await expect(page.getByText('Completed task')).toBeVisible();

  mkdirSync('../.omo/evidence', { recursive: true });
  await page.screenshot({ path: EVIDENCE_SCREENSHOT, fullPage: true });

  expect(requests.at(0)?.body).toEqual({ message: 'Hello from QA' });
  expect(requests.map((request) => `${request.method} ${request.path}`)).toEqual([
    'POST /api/tasks',
    `GET /api/tasks/${taskId}`,
    `GET /api/tasks/${taskId}`,
    `GET /api/tasks/${taskId}/result`,
  ]);
  expect(unexpectedErrors).toEqual([]);
});

test('blocks empty submit and renders a structured submit error bubble', async ({ page }) => {
  const requests = await interceptTasksApi(page, (request) => {
    if (request.method === 'POST' && request.path === '/api/tasks') {
      return jsonApiResponse(apiErrorResponse('capacity_exceeded', 'Task capacity exceeded.'), 429);
    }

    throw unexpectedRequestError(request);
  });

  await page.goto('/');
  const messageBox = page.getByRole('textbox', { name: 'Message' });
  const sendButton = page.getByRole('button', { name: /send message/i });

  await expect(sendButton).toBeDisabled();
  await messageBox.fill('   ');
  await messageBox.press('Enter');
  await expect(sendButton).toBeDisabled();
  expect(requests).toHaveLength(0);

  await messageBox.fill('Trigger structured error');
  await sendButton.click();

  await expect(page.getByRole('alert')).toContainText(
    'capacity_exceeded: Task capacity exceeded.',
  );
  await expect(page.getByRole('status')).toContainText('capacity_exceeded');
  expect(requests.map((request) => `${request.method} ${request.path}`)).toEqual([
    'POST /api/tasks',
  ]);
});

test('cancels an active task through the intercepted cancel endpoint', async ({ page }) => {
  const taskId = 'task-e2e-cancel';
  let cancellationRequested = false;
  const requests = await interceptTasksApi(page, (request) => {
    if (request.method === 'POST' && request.path === '/api/tasks') {
      return jsonApiResponse(taskCreateResponse(taskId), 202);
    }

    if (request.method === 'GET' && request.path === `/api/tasks/${taskId}`) {
      if (cancellationRequested) {
        return jsonApiResponse(
          taskStatusResponse(taskId, 'cancelled', {
            current_step: 1,
            latest_event_id: 3,
            error: {
              code: 'task_cancelled',
              message: 'Task cancelled by QA.',
              details: null,
            },
          }),
        );
      }

      return jsonApiResponse(
        taskStatusResponse(taskId, 'running', {
          agent_state: 'working',
          current_step: 1,
          latest_event_id: 2,
        }),
      );
    }

    if (request.method === 'POST' && request.path === `/api/tasks/${taskId}/cancel`) {
      cancellationRequested = true;
      return jsonApiResponse(
        {
          task_id: taskId,
          status: 'running',
          cancellation_requested: true,
          message: 'Cancellation requested.',
        },
        202,
      );
    }

    if (request.method === 'GET' && request.path === `/api/tasks/${taskId}/result`) {
      return jsonApiResponse(
        taskResultResponse(taskId, 'cancelled', {
          error: {
            code: 'task_cancelled',
            message: 'Task cancelled by QA.',
            details: null,
          },
        }),
      );
    }

    throw unexpectedRequestError(request);
  });

  await page.goto('/');
  await page.getByRole('textbox', { name: 'Message' }).fill('Cancel this QA task');
  await page.getByRole('button', { name: /send message/i }).click();

  await expect(page.getByRole('button', { name: /cancel task/i })).toBeEnabled();
  await expect(page.getByLabel(/task status: running/i)).toBeVisible();
  await page.getByRole('button', { name: /cancel task/i }).click();
  await expect(page.locator('.message--system').last()).toContainText('Cancellation requested.');

  await expect(page.getByRole('alert')).toContainText('task_cancelled: Task cancelled by QA.');
  await expect(page.getByLabel(/task status: cancelled/i)).toBeVisible();
  await expect(page.getByText('Cancelled task')).toBeVisible();
  await expect(page.getByRole('button', { name: /cancel task/i })).toBeHidden();
  expect(
    requests.some(
      (request) =>
        request.method === 'POST' && request.path === `/api/tasks/${taskId}/cancel`,
    ),
  ).toBe(true);
  expect(requests.map((request) => `${request.method} ${request.path}`)).toEqual([
    'POST /api/tasks',
    `GET /api/tasks/${taskId}`,
    `POST /api/tasks/${taskId}/cancel`,
    `GET /api/tasks/${taskId}`,
    `GET /api/tasks/${taskId}/result`,
  ]);
});
}

async function interceptTasksApi(
  page: Page,
  handler: (request: MockApiRequest) => MockApiResponse | Promise<MockApiResponse>,
): Promise<MockApiRequest[]> {
  const requests: MockApiRequest[] = [];
  const routeHandler = async (route: Route, request: Request) => {
    const mockRequest = mockRequestFromPlaywrightRequest(request);
    requests.push(mockRequest);
    const response = await handler(mockRequest);

    await route.fulfill({
      status: response.status ?? 200,
      contentType: 'application/json',
      body: JSON.stringify(response.body),
    });
  };

  await page.route(TASKS_ROUTE, routeHandler);
  await page.route(TASKS_NESTED_ROUTE, routeHandler);

  return requests;
}

function collectUnexpectedErrors(page: Page): string[] {
  const unexpectedErrors: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') {
      unexpectedErrors.push(message.text());
    }
  });
  page.on('pageerror', (error) => {
    unexpectedErrors.push(error.message);
  });

  return unexpectedErrors;
}

function mockRequestFromPlaywrightRequest(request: Request): MockApiRequest {
  const url = new URL(request.url());
  return {
    method: request.method().toUpperCase(),
    path: url.pathname,
    body: parseRequestBody(request),
  };
}

function parseRequestBody(request: Request): unknown {
  const postData = request.postData();
  if (postData === null || postData.trim() === '') {
    return null;
  }

  return JSON.parse(postData) as unknown;
}

function jsonApiResponse(body: unknown, status?: number): MockApiResponse {
  return { status, body };
}

function unexpectedRequestError(request: MockApiRequest): Error {
  return new Error(`Unexpected API request: ${request.method} ${request.path}`);
}

function taskLinks(taskId: string) {
  return {
    status: `/api/tasks/${taskId}`,
    result: `/api/tasks/${taskId}/result`,
    cancel: `/api/tasks/${taskId}/cancel`,
    events: `/api/tasks/${taskId}/events`,
  };
}

function taskCreateResponse(taskId: string) {
  return {
    task_id: taskId,
    status: 'queued',
    created_at: CREATED_AT,
    updated_at: UPDATED_AT,
    links: taskLinks(taskId),
  };
}

function taskStatusResponse(
  taskId: string,
  status: TaskStatus,
  overrides: Record<string, unknown> = {},
) {
  const isStarted = status !== 'queued';
  const isTerminal = ['succeeded', 'failed', 'cancelled', 'expired'].includes(status);

  return {
    task_id: taskId,
    status,
    agent_state: null,
    current_step: status === 'running' ? 1 : 0,
    max_steps: 20,
    created_at: CREATED_AT,
    updated_at: UPDATED_AT,
    started_at: isStarted ? STARTED_AT : null,
    completed_at: isTerminal ? COMPLETED_AT : null,
    expires_at: null,
    error: null,
    latest_event_id: 0,
    links: taskLinks(taskId),
    ...overrides,
  };
}

function taskResultResponse(
  taskId: string,
  status: TaskStatus,
  overrides: Record<string, unknown> = {},
) {
  return {
    task_id: taskId,
    status,
    final_text: null,
    raw_steps: [],
    messages: [],
    error: null,
    created_at: CREATED_AT,
    completed_at: COMPLETED_AT,
    ...overrides,
  };
}

function apiErrorResponse(code: string, message: string) {
  return {
    error: {
      code,
      message,
      details: null,
    },
  };
}
