import {
  type FormEvent,
  type KeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

import {
  ApiRequestError,
  apiBaseUrl,
  cancelTask,
  createTask,
  getTask,
  getTaskResult,
} from './api';
import type {
  AgentStatus,
  ChatMessage,
  ChatRole,
  TaskCancelResponse,
  TaskCreateResponse,
  TaskResultResponse,
  TaskStatus,
  TaskStatusResponse,
} from './types';

const POLL_INTERVAL_MS = 1000;
const DEFAULT_MAX_STEPS = 20;

const activeStatuses = new Set<TaskStatus>(['queued', 'running']);
const terminalStatuses = new Set<TaskStatus>([
  'succeeded',
  'failed',
  'cancelled',
  'expired',
]);

const v1Limitations = [
  'One backend task is created for each submitted message.',
  'Transcript is local browser-session only.',
  'V1 ships without authentication, streaming, or account state.',
];

type TranscriptRole = ChatRole | 'error';
type TranscriptMessage = Omit<ChatMessage, 'role'> & { role: TranscriptRole };

const initialTranscript: TranscriptMessage[] = [];

function App() {
  const [draft, setDraft] = useState('');
  const [messages, setMessages] = useState<TranscriptMessage[]>(initialTranscript);
  const [activeTask, setActiveTask] = useState<TaskStatusResponse | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isCancelling, setIsCancelling] = useState(false);
  const formRef = useRef<HTMLFormElement | null>(null);
  const messageSequenceRef = useRef(initialTranscript.length);
  const controllersRef = useRef<Set<AbortController>>(new Set());
  const activeTaskRef = useRef<TaskStatusResponse | null>(null);
  const terminalMessageTaskIdsRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    activeTaskRef.current = activeTask;
  }, [activeTask]);

  useEffect(() => {
    const trackedControllers = controllersRef.current;

    return () => {
      trackedControllers.forEach((controller) => controller.abort());
      trackedControllers.clear();
    };
  }, []);

  const appendMessage = useCallback(
    (role: TranscriptRole, label: string, content: string) => {
      setMessages((currentMessages) => {
        const sequence = messageSequenceRef.current;
        messageSequenceRef.current += 1;

        return [
          ...currentMessages,
          {
            id: `${role}-${sequence}`,
            role,
            label,
            timestamp: formatTranscriptTime(sequence),
            content,
          },
        ];
      });
    },
    [],
  );

  const runApiRequest = useCallback(
    async <TResponse,>(
      request: (signal: AbortSignal) => Promise<TResponse>,
    ): Promise<TResponse> => {
      const controller = new AbortController();
      controllersRef.current.add(controller);

      try {
        return await request(controller.signal);
      } finally {
        controllersRef.current.delete(controller);
      }
    },
    [],
  );

  const handleTerminalTask = useCallback(
    async (task: TaskStatusResponse) => {
      if (terminalMessageTaskIdsRef.current.has(task.task_id)) {
        return;
      }

      terminalMessageTaskIdsRef.current.add(task.task_id);

      if (task.status === 'expired') {
        const message = formatStructuredError(task.error, 'Task expired.');
        setErrorMessage(message);
        appendMessage('error', 'Error', message);
        return;
      }

      try {
        const result = await runApiRequest((signal) =>
          getTaskResult(task.task_id, { signal }),
        );
        setActiveTask((currentTask) =>
          currentTask?.task_id === task.task_id
            ? statusResponseFromResult(currentTask, result)
            : currentTask,
        );

        const message = formatResultMessage(result);
        if (result.status === 'succeeded') {
          setErrorMessage(null);
          appendMessage('agent', 'Assistant', message);
          return;
        }

        setErrorMessage(message);
        appendMessage('error', 'Error', message);
      } catch (error) {
        if (isAbortError(error)) {
          return;
        }

        const message = formatRequestError(error);
        setErrorMessage(message);
        appendMessage('error', 'Error', message);
      }
    },
    [appendMessage, runApiRequest],
  );

  const activeTaskId = activeTask?.task_id ?? null;
  const activeTaskStatus = activeTask?.status ?? null;
  const isTaskActive = activeTaskStatus === null ? false : isActiveStatus(activeTaskStatus);
  const trimmedDraft = draft.trim();
  const isSubmitDisabled = trimmedDraft.length === 0 || isTaskActive || isSubmitting;
  const statusBadgeLabel = isSubmitting
    ? 'Submitting'
    : activeTaskStatus
      ? formatStatusLabel(activeTaskStatus)
      : 'Idle';
  const latestMessage = messages.length > 0 ? messages[messages.length - 1] : null;
  const liveAnnouncement = formatLiveAnnouncement(statusBadgeLabel, latestMessage, errorMessage);

  useEffect(() => {
    if (activeTaskId === null || activeTaskStatus === null || !isActiveStatus(activeTaskStatus)) {
      return;
    }

    let stopped = false;
    let pollTimer: number | undefined;

    const pollTask = async () => {
      try {
        const statusResponse = await runApiRequest((signal) =>
          getTask(activeTaskId, { signal }),
        );
        if (stopped || !shouldApplyPolledStatus(activeTaskRef.current, activeTaskId)) {
          return;
        }

        setActiveTask(statusResponse);

        if (isTerminalStatus(statusResponse.status)) {
          await handleTerminalTask(statusResponse);
          return;
        }

        pollTimer = window.setTimeout(pollTask, POLL_INTERVAL_MS);
      } catch (error) {
        if (stopped || isAbortError(error)) {
          return;
        }

        const message = formatRequestError(error);
        setErrorMessage(message);
        appendMessage('error', 'Error', message);
        setActiveTask((currentTask) =>
          currentTask?.task_id === activeTaskId
            ? {
                ...currentTask,
                status: 'failed',
                completed_at: new Date().toISOString(),
                error: {
                  code: 'polling_failed',
                  message,
                  details: null,
                },
              }
            : currentTask,
        );
      }
    };

    pollTimer = window.setTimeout(pollTask, POLL_INTERVAL_MS);

    return () => {
      stopped = true;
      if (pollTimer !== undefined) {
        window.clearTimeout(pollTimer);
      }
    };
  }, [activeTaskId, activeTaskStatus, appendMessage, handleTerminalTask, runApiRequest]);

  const statusCards = useMemo<AgentStatus[]>(
    () => [
      {
        id: 'runtime',
        label: 'Runtime',
        value: statusBadgeLabel,
        tone: isSubmitting || isTaskActive ? 'pending' : 'stable',
      },
      {
        id: 'adapter',
        label: 'Adapter',
        value: formatAdapterSummary(activeTask),
        tone: isTaskActive ? 'pending' : 'stable',
      },
      {
        id: 'session',
        label: 'Session',
        value: activeTask?.task_id ?? 'Local browser transcript',
        tone: isTaskActive ? 'pending' : 'stable',
      },
    ],
    [activeTask, isSubmitting, isTaskActive, statusBadgeLabel],
  );

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (isSubmitDisabled) {
      return;
    }

    const message = trimmedDraft;
    setDraft('');
    setErrorMessage(null);
    setActiveTask(null);
    appendMessage('operator', 'User', message);
    setIsSubmitting(true);
    let aborted = false;

    try {
      const response = await runApiRequest((signal) => createTask(message, { signal }));
      setActiveTask(statusResponseFromCreate(response));
      appendMessage(
        'system',
        'System',
        `Task ${response.task_id} entered ${formatStatusLabel(response.status)} state. Polling every 1000 ms.`,
      );
    } catch (error) {
      if (isAbortError(error)) {
        aborted = true;
        return;
      }

      const formattedError = formatRequestError(error);
      setErrorMessage(formattedError);
      appendMessage('error', 'Error', formattedError);
    } finally {
      if (!aborted) {
        setIsSubmitting(false);
      }
    }
  };

  const handleDraftKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (
      event.key !== 'Enter' ||
      event.shiftKey ||
      event.altKey ||
      event.ctrlKey ||
      event.metaKey ||
      event.nativeEvent.isComposing
    ) {
      return;
    }

    event.preventDefault();
    if (!isSubmitDisabled) {
      formRef.current?.requestSubmit();
    }
  };

  const handleCancel = async () => {
    if (activeTask === null || !isActiveStatus(activeTask.status) || isCancelling) {
      return;
    }

    const taskToCancel = activeTask;
    setIsCancelling(true);
    setErrorMessage(null);
    let aborted = false;

    try {
      const response = await runApiRequest((signal) =>
        cancelTask(taskToCancel.task_id, { signal }),
      );
      const taskAfterCancel = statusResponseFromCancel(taskToCancel, response);
      setActiveTask(taskAfterCancel);

      if (isTerminalStatus(response.status)) {
        await handleTerminalTask(taskAfterCancel);
        return;
      }

      appendMessage('system', 'System', response.message);
    } catch (error) {
      if (isAbortError(error)) {
        aborted = true;
        return;
      }

      const message = formatRequestError(error);
      setErrorMessage(message);
      appendMessage('error', 'Error', message);
    } finally {
      if (!aborted) {
        setIsCancelling(false);
      }
    }
  };

  return (
    <main className="console-shell">
      <section className="console-panel" aria-labelledby="console-title">
        <div className="console-eyebrow">
          <span className="signal-dot" aria-hidden="true" />
          Local agent operations
        </div>

        <header className="console-header">
          <div className="console-heading">
            <p className="kicker">OpenManus V1 command center</p>
            <h1 id="console-title">Task Relay Console</h1>
            <p className="console-summary">
              A focused chat interface for dispatching one local OpenManus task,
              watching lifecycle status, and retaining the transcript in this session.
            </p>
          </div>

          <aside className="api-card" aria-label="Configured API base URL">
            <span>API base</span>
            <code>{apiBaseUrl}</code>
          </aside>
        </header>

        <div className="status-strip">
          <span className="task-status" aria-label={`Task status: ${statusBadgeLabel}`}>
            Status: <strong>{statusBadgeLabel}</strong>
          </span>
          <span className="task-status__detail">
            {isTaskActive && activeTask ? `Active task ${activeTask.task_id}` : 'No active backend task'}
          </span>
        </div>
        <p className="sr-only" role="status" aria-live="polite" aria-atomic="true">
          {liveAnnouncement}
        </p>

        <div className="status-grid" aria-label="Agent status summary">
          {statusCards.map((status) => (
            <article className={`status-card status-card--${status.tone}`} key={status.id}>
              <span>{status.label}</span>
              <strong>{status.value}</strong>
            </article>
          ))}
        </div>

        <section className="chat-card" aria-labelledby="chat-title">
          <header className="chat-card__header">
            <div>
              <p className="kicker">Transcript</p>
              <h2 id="chat-title">Operator Channel</h2>
            </div>
            <span className="chat-card__badge">V1 lifecycle only</span>
          </header>

          <div className="transcript-frame" aria-label="Chat transcript">
            {messages.length === 0 ? (
              <section className="empty-state" aria-labelledby="empty-state-title">
                <p className="empty-state__eyebrow">Standing by</p>
                <h3 id="empty-state-title">V1 limitations before first dispatch</h3>
                <ul>
                  {v1Limitations.map((limitation) => (
                    <li key={limitation}>{limitation}</li>
                  ))}
                </ul>
              </section>
            ) : (
              <ol className="message-list">
                {messages.map((message) => (
                  <li
                    className={`message message--${message.role}`}
                    key={message.id}
                    role={message.role === 'error' ? 'alert' : undefined}
                  >
                    <div className="message__meta">
                      <span>{message.label}</span>
                      <time>{message.timestamp}</time>
                    </div>
                    <p>{message.content}</p>
                  </li>
                ))}
              </ol>
            )}
          </div>

          <form
            ref={formRef}
            className="composer"
            aria-label="Message composer"
            onSubmit={handleSubmit}
          >
            <div className="composer__field">
              <label htmlFor="message-draft">Message</label>
              <textarea
                id="message-draft"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={handleDraftKeyDown}
                placeholder="Describe a single task for the local OpenManus adapter"
                rows={4}
                disabled={isSubmitting}
                aria-describedby="message-help"
              />
              <p id="message-help" className="composer__help">
                Enter sends the message. Shift+Enter inserts a newline.
              </p>
            </div>

            <div className="composer__actions">
              <button className="button button--primary" type="submit" disabled={isSubmitDisabled}>
                {isSubmitting ? 'Sending message' : 'Send message'}
              </button>
              {isTaskActive ? (
                <button
                  className="button button--danger"
                  type="button"
                  onClick={handleCancel}
                  disabled={isCancelling}
                >
                  {isCancelling ? 'Cancelling task' : 'Cancel task'}
                </button>
              ) : null}
            </div>
          </form>
        </section>
      </section>
    </main>
  );
}

function isActiveStatus(status: TaskStatus): boolean {
  return activeStatuses.has(status);
}

function isTerminalStatus(status: TaskStatus): boolean {
  return terminalStatuses.has(status);
}

function shouldApplyPolledStatus(
  currentTask: TaskStatusResponse | null,
  polledTaskId: string,
): boolean {
  return (
    currentTask !== null &&
    currentTask.task_id === polledTaskId &&
    isActiveStatus(currentTask.status)
  );
}

function statusResponseFromCreate(response: TaskCreateResponse): TaskStatusResponse {
  return {
    task_id: response.task_id,
    status: response.status,
    agent_state: null,
    current_step: 0,
    max_steps: DEFAULT_MAX_STEPS,
    created_at: response.created_at,
    updated_at: response.updated_at,
    started_at: null,
    completed_at: null,
    expires_at: null,
    error: null,
    latest_event_id: 0,
    links: response.links,
  };
}

function statusResponseFromResult(
  currentTask: TaskStatusResponse,
  result: TaskResultResponse,
): TaskStatusResponse {
  return {
    ...currentTask,
    status: result.status,
    completed_at: result.completed_at,
    error: result.error,
  };
}

function statusResponseFromCancel(
  currentTask: TaskStatusResponse,
  response: TaskCancelResponse,
): TaskStatusResponse {
  return {
    ...currentTask,
    status: response.status,
    updated_at: new Date().toISOString(),
    completed_at: isTerminalStatus(response.status)
      ? (currentTask.completed_at ?? new Date().toISOString())
      : currentTask.completed_at,
  };
}

function formatTranscriptTime(sequence: number): string {
  return `00:${String(sequence).padStart(2, '0')}`;
}

function formatStatusLabel(status: TaskStatus): string {
  return `${status.slice(0, 1).toUpperCase()}${status.slice(1)}`;
}

function formatAdapterSummary(task: TaskStatusResponse | null): string {
  if (task === null) {
    return 'FastAPI lifecycle';
  }
  if (task.agent_state !== null && isActiveStatus(task.status)) {
    return task.agent_state;
  }
  if (task.status === 'succeeded') {
    return 'Completed task';
  }
  if (task.status === 'failed') {
    return 'Failed task';
  }
  if (task.status === 'cancelled') {
    return 'Cancelled task';
  }
  if (task.status === 'expired') {
    return 'Expired task';
  }
  return `Step ${task.current_step}/${task.max_steps}`;
}

function formatResultMessage(result: TaskResultResponse): string {
  if (result.final_text?.trim()) {
    return result.final_text;
  }

  return formatStructuredError(result.error, `Task ${result.status}.`);
}

function formatLiveAnnouncement(
  statusBadgeLabel: string,
  latestMessage: TranscriptMessage | null,
  errorMessage: string | null,
): string {
  const statusText = `Task status: ${statusBadgeLabel}.`;
  if (errorMessage !== null) {
    return `${statusText} Error: ${errorMessage}`;
  }
  if (latestMessage !== null) {
    return `${statusText} Latest transcript update from ${latestMessage.label}: ${latestMessage.content}`;
  }
  return `${statusText} No transcript messages yet.`;
}

function formatStructuredError(
  error: Record<string, unknown> | null,
  fallback: string,
): string {
  if (error === null) {
    return fallback;
  }

  const code = typeof error.code === 'string' ? error.code : null;
  const message = typeof error.message === 'string' ? error.message : null;

  if (code !== null && message !== null) {
    return `${code}: ${message}`;
  }
  if (message !== null) {
    return message;
  }
  if (code !== null) {
    return code;
  }
  return fallback;
}

function formatRequestError(error: unknown): string {
  if (error instanceof ApiRequestError) {
    return `${error.code}: ${error.message}`;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return 'OpenManus API request failed.';
}

function isAbortError(error: unknown): boolean {
  return isRecord(error) && error.name === 'AbortError';
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export default App;
