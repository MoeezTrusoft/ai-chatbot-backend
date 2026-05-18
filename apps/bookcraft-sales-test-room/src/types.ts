export type ApiConfig = {
  baseUrl: string;
  sessionServiceUrl: string;
};

export type BrowserSession = {
  customerId: string;
  chatToken?: string;
  expiresAt?: number;
  source: 'local' | 'session-service' | 'manual' | 'none';
};

export type ChatBubble = {
  text: string;
  bubble_index?: number;
  rich_segments?: unknown[];
};

export type ChatIntent = {
  query_primary?: string | null;
  query_secondary?: string[];
  service_primary?: string | null;
  service_secondary?: string[];
  funnel_stage?: string | null;
  needs_clarification?: boolean;
  confidence?: number | null;
  rationale?: string;
  evidence?: string[];
  [key: string]: unknown;
};

export type ChatTurnResponse = {
  thread_id: string;
  bubbles: ChatBubble[];
  intent?: ChatIntent;
  language_status?: string;
  debug_event_ids?: string[];
  [key: string]: unknown;
};

export type ChatTurnPayload = {
  message: string;
  customer_id: string;
  thread_id?: string | null;
  correlation_id?: string | null;
};

export type LiveTrace = {
  message_preview?: string;
  assistant_preview?: string;
  elapsed_ms?: number;
  intent?: Record<string, unknown> | null;
  decision?: Record<string, unknown> | null;
  runtime_atoms?: Record<string, unknown> | null;
  action_plan?: Record<string, unknown> | null;
  assistant?: Record<string, unknown> | null;
  components?: Record<string, unknown> | null;
  recorded_at?: string;
  [key: string]: unknown;
};

export type LiveTraceResponse = {
  trace_path?: string;
  count?: number;
  traces: LiveTrace[];
  thread_id?: string;
};

export type ThreadTurn = {
  id: string;
  customerText: string;
  assistantText: string;
  createdAt: string;
  elapsedMs?: number;
  response?: ChatTurnResponse;
  trace?: LiveTrace;
  traceStatus?: 'available' | 'not-configured' | 'unavailable';
};

export type TestThread = {
  id: string;
  title: string;
  threadId?: string;
  customerId?: string;
  createdAt: string;
  updatedAt: string;
  turns: ThreadTurn[];
};
