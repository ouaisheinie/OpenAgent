import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import type {
  ApiErrorResponse,
  TaskCreateResponse,
  TaskLinks,
  TaskResultResponse,
  TaskStatus,
  TaskStatusResponse,
} from '../types';

const API_BASE_URL = 'http://127.0.0.1:8000';
const CREATED_AT = '2026-06-24T10:00:00.000000Z';
const UPDATED_AT = '2026-06-24T10:00:01.000000Z';
const STARTED_AT = '2026-06-24T10:00:02.000000Z';
const COMPLETED_AT = '2026-06-24T10:00:03.000000Z';
const POLL_INTERVAL_MS = 1000;

type FetchInput = Parameters<typeof fetch>[0];
type FetchInit = Parameters<typeof fetch>[1];

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe('App', () => {
  it('renders the accessible OpenManus chat command center empty state', () => {
    vi.stubGlobal('fetch', vi.fn<typeof fetch>());

    render(<App />);

    expect(
      screen.getByRole('heading', { level: 1, name: /task relay console/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/operator channel/i)).toBeInTheDocument();
    expect(screen.getByText(API_BASE_URL)).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Message' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /send message/i })).toBeDisabled();
    expect(screen.queryByRole('button', { name: /cancel task/i })).not.toBeInTheDocument();
    expect(screen.getByLabelText(/task status: idle/i)).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent(/task status: idle/i);
    expect(screen.getByText(/one backend task is created/i)).toBeInTheDocument();
    expect(screen.getByText(/local browser-session only/i)).toBeInTheDocument();
    expect(screen.getByText(/without authentication, streaming/i)).toBeInTheDocument();
  });

  it('submits with Enter and polling renders final assistant text', async () => {
    vi.useFakeTimers();
    const taskId = 'task-polling';
    const statusResponses = [
      taskStatusResponse(taskId, 'running', {
        agent_state: 'thinking',
        current_step: 1,
        latest_event_id: 2,
      }),
      taskStatusResponse(taskId, 'succeeded', {
        current_step: 2,
        latest_event_id: 3,
      }),
    ];
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = requestUrl(input);
      const method = requestMethod(init);

      if (url === `${API_BASE_URL}/api/tasks` && method === 'POST') {
        return jsonResponse(taskCreateResponse(taskId), { status: 202 });
      }
      if (url === `${API_BASE_URL}/api/tasks/${taskId}` && method === 'GET') {
        const nextStatus = statusResponses.shift();
        if (nextStatus === undefined) {
          throw new Error('Unexpected polling request.');
        }
        return jsonResponse(nextStatus);
      }
      if (url === `${API_BASE_URL}/api/tasks/${taskId}/result` && method === 'GET') {
        return jsonResponse(
          taskResultResponse(taskId, 'succeeded', {
            final_text: 'Final answer from mocked backend.',
            messages: [
              {
                role: 'assistant',
                content: 'Final answer from mocked backend.',
              },
            ],
          }),
        );
      }

      throw new Error(`Unexpected request: ${method} ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);
    const messageBox = screen.getByRole('textbox', { name: 'Message' });
    fireEvent.change(messageBox, {
      target: { value: 'Plan a release' },
    });
    fireEvent.keyDown(messageBox, { key: 'Enter', code: 'Enter' });
    await flushAsyncWork();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.getByText('Plan a release')).toBeInTheDocument();
    expect(screen.getByText(/entered queued state/i, { selector: '.message p' })).toBeInTheDocument();
    expect(screen.getByLabelText(/task status: queued/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /cancel task/i })).toBeEnabled();
    expect(parseRequestBody(fetchMock.mock.calls[0]?.[1])).toEqual({
      message: 'Plan a release',
    });
    expect(requestHeader(fetchMock.mock.calls[0]?.[1], 'Accept')).toBe('application/json');
    expect(requestHeader(fetchMock.mock.calls[0]?.[1], 'Content-Type')).toBe(
      'application/json',
    );

    await advancePolling();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(screen.getByLabelText(/task status: running/i)).toBeInTheDocument();
    expect(screen.getByText('thinking')).toBeInTheDocument();

    await advancePolling();
    expect(screen.getByText('Final answer from mocked backend.')).toBeInTheDocument();
    expect(screen.getByLabelText(/task status: succeeded/i)).toBeInTheDocument();
    expect(screen.getByText('Completed task')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent(/final answer from mocked backend/i);
    expect(screen.queryByRole('button', { name: /cancel task/i })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(4);

    await advancePolling(3);
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it('keeps Shift+Enter as a newline without submitting', async () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();

    render(<App />);
    const messageBox = screen.getByRole('textbox', { name: 'Message' });

    await user.type(messageBox, 'Line one');
    fireEvent.keyDown(messageBox, { key: 'Enter', code: 'Enter', shiftKey: true });
    fireEvent.change(messageBox, { target: { value: 'Line one\nLine two' } });

    expect(messageBox).toHaveValue('Line one\nLine two');
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /send message/i })).toBeEnabled();
  });

  it('renders structured backend submit errors as deterministic error bubbles', async () => {
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = requestUrl(input);
      const method = requestMethod(init);
      if (url === `${API_BASE_URL}/api/tasks` && method === 'POST') {
        return jsonResponse(apiErrorResponse('capacity_exceeded', 'Task capacity exceeded.'), {
          status: 429,
        });
      }

      throw new Error(`Unexpected request: ${method} ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();

    render(<App />);
    await user.type(screen.getByRole('textbox', { name: 'Message' }), 'Start too much work');
    await user.click(screen.getByRole('button', { name: /send message/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'capacity_exceeded: Task capacity exceeded.',
    );
    expect(screen.getByRole('status')).toHaveTextContent(/capacity_exceeded/i);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('clears a previous succeeded task when a later create request fails', async () => {
    vi.useFakeTimers();
    const taskId = 'task-success-before-error';
    let createRequestCount = 0;
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = requestUrl(input);
      const method = requestMethod(init);

      if (url === `${API_BASE_URL}/api/tasks` && method === 'POST') {
        createRequestCount += 1;
        if (createRequestCount === 1) {
          return jsonResponse(taskCreateResponse(taskId), { status: 202 });
        }

        return jsonResponse(apiErrorResponse('capacity_exceeded', 'Task capacity exceeded.'), {
          status: 429,
        });
      }
      if (url === `${API_BASE_URL}/api/tasks/${taskId}` && method === 'GET') {
        return jsonResponse(
          taskStatusResponse(taskId, 'succeeded', {
            current_step: 2,
            latest_event_id: 3,
          }),
        );
      }
      if (url === `${API_BASE_URL}/api/tasks/${taskId}/result` && method === 'GET') {
        return jsonResponse(
          taskResultResponse(taskId, 'succeeded', {
            final_text: 'First task succeeded.',
          }),
        );
      }

      throw new Error(`Unexpected request: ${method} ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);
    const messageBox = screen.getByRole('textbox', { name: 'Message' });
    fireEvent.change(messageBox, { target: { value: 'First task' } });
    fireEvent.click(screen.getByRole('button', { name: /send message/i }));
    await flushAsyncWork();
    await advancePolling();

    expect(screen.getByText('First task succeeded.')).toBeInTheDocument();
    expect(screen.getByLabelText(/task status: succeeded/i)).toBeInTheDocument();
    expect(screen.getByText('Completed task')).toBeInTheDocument();

    fireEvent.change(messageBox, { target: { value: 'Second task fails to create' } });
    fireEvent.click(screen.getByRole('button', { name: /send message/i }));
    await flushAsyncWork();

    expect(screen.getByRole('alert')).toHaveTextContent(
      'capacity_exceeded: Task capacity exceeded.',
    );
    expect(screen.getByLabelText(/task status: idle/i)).toBeInTheDocument();
    expect(screen.getByText('FastAPI lifecycle')).toBeInTheDocument();
    expect(screen.getByText('Local browser transcript')).toBeInTheDocument();
    expect(screen.queryByLabelText(/task status: succeeded/i)).not.toBeInTheDocument();
    expect(screen.queryByText('Completed task')).not.toBeInTheDocument();
    expect(screen.queryByText('Step 2/20')).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it('cancel active task keeps polling until backend reports cancelled', async () => {
    vi.useFakeTimers();
    const taskId = 'task-cancel';
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = requestUrl(input);
      const method = requestMethod(init);

      if (url === `${API_BASE_URL}/api/tasks` && method === 'POST') {
        return jsonResponse(taskCreateResponse(taskId), { status: 202 });
      }
      if (url === `${API_BASE_URL}/api/tasks/${taskId}/cancel` && method === 'POST') {
        return jsonResponse(
          {
            task_id: taskId,
            status: 'running',
            cancellation_requested: true,
            message: 'Cancellation requested.',
          },
          { status: 202 },
        );
      }
      if (url === `${API_BASE_URL}/api/tasks/${taskId}` && method === 'GET') {
        return jsonResponse(
          taskStatusResponse(taskId, 'cancelled', {
            current_step: 1,
            latest_event_id: 3,
            error: {
              code: 'task_cancelled',
              message: 'Task cancelled.',
              details: null,
            },
          }),
        );
      }
      if (url === `${API_BASE_URL}/api/tasks/${taskId}/result` && method === 'GET') {
        return jsonResponse(
          taskResultResponse(taskId, 'cancelled', {
            error: {
              code: 'task_cancelled',
              message: 'Task cancelled.',
              details: null,
            },
          }),
        );
      }

      throw new Error(`Unexpected request: ${method} ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);
    fireEvent.change(screen.getByRole('textbox', { name: 'Message' }), {
      target: { value: 'Cancel this task' },
    });
    fireEvent.click(screen.getByRole('button', { name: /send message/i }));
    await flushAsyncWork();
    expect(screen.getByRole('button', { name: /cancel task/i })).toBeEnabled();

    fireEvent.click(screen.getByRole('button', { name: /cancel task/i }));
    await flushAsyncWork();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(screen.getByLabelText(/task status: running/i)).toBeInTheDocument();
    expect(screen.getByText('Cancellation requested.')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();

    await advancePolling();

    expect(fetchMock).toHaveBeenCalledTimes(4);
    expect(screen.getByRole('alert')).toHaveTextContent('task_cancelled: Task cancelled.');
    expect(screen.getByLabelText(/task status: cancelled/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /cancel task/i })).not.toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(
        ([input, init]) =>
          requestUrl(input) === `${API_BASE_URL}/api/tasks/${taskId}/cancel` &&
          requestMethod(init) === 'POST',
      ),
    ).toBe(true);
    expect(fetchMock.mock.calls.map(([input, init]) => `${requestMethod(init)} ${requestUrl(input)}`))
      .toEqual([
        `POST ${API_BASE_URL}/api/tasks`,
        `POST ${API_BASE_URL}/api/tasks/${taskId}/cancel`,
        `GET ${API_BASE_URL}/api/tasks/${taskId}`,
        `GET ${API_BASE_URL}/api/tasks/${taskId}/result`,
      ]);

    await advancePolling(3);
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it('blocks empty submit without network calls', async () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();

    render(<App />);
    const messageBox = screen.getByRole('textbox', { name: 'Message' });
    const sendButton = screen.getByRole('button', { name: /send message/i });

    expect(sendButton).toBeDisabled();
    await user.type(messageBox, '   ');
    expect(sendButton).toBeDisabled();
    fireEvent.keyDown(messageBox, { key: 'Enter', code: 'Enter' });
    fireEvent.submit(screen.getByRole('form', { name: /message composer/i }));
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

async function advancePolling(times = 1): Promise<void> {
  for (let index = 0; index < times; index += 1) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
      await Promise.resolve();
      await Promise.resolve();
    });
  }
}

async function flushAsyncWork(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function taskLinks(taskId: string): TaskLinks {
  return {
    status: `/api/tasks/${taskId}`,
    result: `/api/tasks/${taskId}/result`,
    cancel: `/api/tasks/${taskId}/cancel`,
    events: `/api/tasks/${taskId}/events`,
  };
}

function taskCreateResponse(
  taskId: string,
  overrides: Partial<TaskCreateResponse> = {},
): TaskCreateResponse {
  return {
    task_id: taskId,
    status: 'queued',
    created_at: CREATED_AT,
    updated_at: UPDATED_AT,
    links: taskLinks(taskId),
    ...overrides,
  };
}

function taskStatusResponse(
  taskId: string,
  status: TaskStatus,
  overrides: Partial<TaskStatusResponse> = {},
): TaskStatusResponse {
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
  overrides: Partial<TaskResultResponse> = {},
): TaskResultResponse {
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

function apiErrorResponse(code: string, message: string): ApiErrorResponse {
  return {
    error: {
      code,
      message,
      details: null,
    },
  };
}

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  const headers = new Headers(init.headers);
  headers.set('Content-Type', 'application/json');

  return new Response(JSON.stringify(body), {
    ...init,
    headers,
  });
}

function requestUrl(input: FetchInput): string {
  if (typeof input === 'string') {
    return input;
  }
  if (input instanceof URL) {
    return input.toString();
  }
  return input.url;
}

function requestMethod(init: FetchInit): string {
  return init?.method?.toUpperCase() ?? 'GET';
}

function requestHeader(init: FetchInit, name: string): string | null {
  return new Headers(init?.headers).get(name);
}

function parseRequestBody(init: FetchInit): unknown {
  if (typeof init?.body !== 'string') {
    throw new Error('Expected a JSON string request body.');
  }

  return JSON.parse(init.body) as unknown;
}
