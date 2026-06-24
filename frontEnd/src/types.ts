export type ChatRole = 'agent' | 'operator' | 'system';

export type AgentTone = 'pending' | 'stable';

export interface ChatMessage {
  id: string;
  role: ChatRole;
  label: string;
  timestamp: string;
  content: string;
}

export interface AgentStatus {
  id: string;
  label: string;
  value: string;
  tone: AgentTone;
}

export type TaskStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'expired';

export type ToolProfile = 'chat' | 'browser' | 'dev_local';

export type EventName =
  | 'task.queued'
  | 'task.started'
  | 'agent.step.started'
  | 'agent.step.completed'
  | 'task.succeeded'
  | 'task.failed'
  | 'task.cancelled'
  | 'task.expired';

export interface TaskCreateRequest {
  message: string;
  max_steps?: number;
  timeout_seconds?: number;
  tool_profile?: ToolProfile;
  metadata?: Record<string, unknown>;
}

export interface TaskLinks {
  status: string;
  result: string;
  cancel: string;
  events: string;
}

export interface TaskCreateResponse {
  task_id: string;
  status: TaskStatus;
  created_at: string;
  updated_at: string;
  links: TaskLinks;
}

export interface TaskStatusResponse {
  task_id: string;
  status: TaskStatus;
  agent_state: string | null;
  current_step: number;
  max_steps: number;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  completed_at: string | null;
  expires_at: string | null;
  error: Record<string, unknown> | null;
  latest_event_id: number;
  links: TaskLinks;
}

export interface TaskResultResponse {
  task_id: string;
  status: TaskStatus;
  final_text: string | null;
  raw_steps: unknown[];
  messages: Array<Record<string, unknown>>;
  error: Record<string, unknown> | null;
  created_at: string;
  completed_at: string | null;
}

export interface TaskCancelResponse {
  task_id: string;
  status: TaskStatus;
  cancellation_requested: boolean;
  message: string;
}

export interface TaskEvent {
  event_id: number;
  task_id: string;
  type: EventName;
  created_at: string;
  payload: Record<string, unknown>;
}

export interface TaskEventsResponse {
  task_id: string;
  events: TaskEvent[];
  latest_event_id: number;
}

export interface ApiError {
  code: string;
  message: string;
  details: Record<string, unknown> | null;
}

export interface ApiErrorResponse {
  error: ApiError;
}
