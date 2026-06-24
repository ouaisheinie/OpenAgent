import type {
  ApiError,
  ApiErrorResponse,
  TaskCancelResponse,
  TaskCreateRequest,
  TaskCreateResponse,
  TaskEventsResponse,
  TaskResultResponse,
  TaskStatusResponse,
} from './types';

export const DEFAULT_API_BASE_URL = 'http://127.0.0.1:8000';

export function normalizeApiBaseUrl(value: string | undefined): string {
  return (value?.trim() || DEFAULT_API_BASE_URL).replace(/\/+$/, '');
}

export const apiBaseUrl = normalizeApiBaseUrl(import.meta.env.VITE_API_BASE_URL);

export interface ApiRequestOptions {
  signal?: AbortSignal;
}

export class ApiRequestError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown> | null;

  constructor(status: number, error: ApiError) {
    super(error.message);
    this.name = 'ApiRequestError';
    this.status = status;
    this.code = error.code;
    this.details = error.details;
  }
}

export async function requestJson<TResponse>(
  path: string,
  init?: RequestInit,
): Promise<TResponse> {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  const response = await fetch(`${apiBaseUrl}${normalizedPath}`, withJsonAccept(init));

  if (!response.ok) {
    const errorResponse = await readApiErrorResponse(response);
    throw new ApiRequestError(
      response.status,
      errorResponse?.error ?? {
        code: `http_${response.status}`,
        message: `OpenManus API request failed: ${response.status}`,
        details: null,
      },
    );
  }

  return response.json() as Promise<TResponse>;
}

export function createTask(
  message: string,
  options?: ApiRequestOptions,
): Promise<TaskCreateResponse> {
  const request: TaskCreateRequest = { message };

  return requestJson<TaskCreateResponse>('/api/tasks', {
    method: 'POST',
    headers: jsonBodyHeaders(),
    body: JSON.stringify(request),
    signal: options?.signal,
  });
}

export function getTask(
  taskId: string,
  options?: ApiRequestOptions,
): Promise<TaskStatusResponse> {
  return requestJson<TaskStatusResponse>(`/api/tasks/${encodeURIComponent(taskId)}`, {
    signal: options?.signal,
  });
}

export function getTaskResult(
  taskId: string,
  options?: ApiRequestOptions,
): Promise<TaskResultResponse> {
  return requestJson<TaskResultResponse>(
    `/api/tasks/${encodeURIComponent(taskId)}/result`,
    {
      signal: options?.signal,
    },
  );
}

export function cancelTask(
  taskId: string,
  options?: ApiRequestOptions,
): Promise<TaskCancelResponse> {
  return requestJson<TaskCancelResponse>(
    `/api/tasks/${encodeURIComponent(taskId)}/cancel`,
    {
      method: 'POST',
      headers: jsonBodyHeaders(),
      signal: options?.signal,
    },
  );
}

export function getEvents(
  taskId: string,
  since?: number,
  options?: ApiRequestOptions,
): Promise<TaskEventsResponse> {
  const query = since === undefined ? '' : `?since=${encodeURIComponent(String(since))}`;

  return requestJson<TaskEventsResponse>(
    `/api/tasks/${encodeURIComponent(taskId)}/events${query}`,
    {
      signal: options?.signal,
    },
  );
}

function withJsonAccept(init?: RequestInit): RequestInit {
  const headers = new Headers(init?.headers);
  headers.set('Accept', 'application/json');

  return {
    ...init,
    headers,
  };
}

function jsonBodyHeaders(): Headers {
  const headers = new Headers();
  headers.set('Content-Type', 'application/json');
  return headers;
}

async function readApiErrorResponse(response: Response): Promise<ApiErrorResponse | null> {
  const payload = await readJsonPayload(response);
  return isApiErrorResponse(payload) ? payload : null;
}

async function readJsonPayload(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text.trim()) {
    return null;
  }

  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}

function isApiErrorResponse(value: unknown): value is ApiErrorResponse {
  if (!isRecord(value) || !isRecord(value.error)) {
    return false;
  }

  const { code, message, details } = value.error;
  return (
    typeof code === 'string' &&
    typeof message === 'string' &&
    (details === null || isRecord(details))
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
