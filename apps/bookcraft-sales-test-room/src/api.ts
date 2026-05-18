import type { ApiConfig, BrowserSession, ChatTurnPayload, ChatTurnResponse, LiveTraceResponse } from './types';

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function normalizeBaseUrl(baseUrl: string): string {
  return baseUrl.replace(/\/+$/, '');
}

function joinUrl(baseUrl: string, path: string): string {
  if (/^https?:\/\//i.test(path)) return path;
  return `${normalizeBaseUrl(baseUrl)}${path.startsWith('/') ? path : `/${path}`}`;
}

function authHeader(token?: string): Record<string, string> {
  const clean = (token ?? '').trim().replace(/^Bearer\s+/i, '');
  return clean ? { Authorization: `Bearer ${clean}` } : {};
}

export function isUuid(value: string): boolean {
  return UUID_RE.test(value);
}

function cleanOptionalUuid(value: unknown, field: 'thread_id'): string | null | undefined {
  if (value === undefined || value === null || value === '') return value === null ? null : undefined;
  if (typeof value !== 'string' || !isUuid(value.trim())) {
    throw new Error(`${field}: must be a valid UUID`);
  }
  return value.trim();
}

export function cleanChatPayload(input: Partial<ChatTurnPayload>): ChatTurnPayload {
  const message = typeof input.message === 'string' ? input.message.trim() : '';
  const customerId = typeof input.customer_id === 'string' ? input.customer_id.trim() : '';
  if (!message) throw new Error('message: is required');
  if (!customerId || !isUuid(customerId)) throw new Error('customer_id: must be a valid UUID');

  const payload: ChatTurnPayload = {
    message,
    customer_id: customerId
  };

  const threadId = cleanOptionalUuid(input.thread_id, 'thread_id');
  if (threadId !== undefined) payload.thread_id = threadId;

  if (input.correlation_id === null) payload.correlation_id = null;
  if (typeof input.correlation_id === 'string' && input.correlation_id.trim()) {
    payload.correlation_id = input.correlation_id.trim();
  }

  return payload;
}

function locToField(loc: unknown): string {
  if (!Array.isArray(loc)) return '';
  return loc.filter((part) => typeof part === 'string' || typeof part === 'number').slice(1).join('.');
}

export function formatApiError(status: number, statusText: string, body: unknown): string {
  const header = `${status} ${statusText}`.trim();
  const detail = body && typeof body === 'object' && 'detail' in body
    ? (body as { detail: unknown }).detail
    : body;

  if (Array.isArray(detail)) {
    const lines = detail.map((item) => {
      if (!item || typeof item !== 'object') return JSON.stringify(item);
      const record = item as Record<string, unknown>;
      const field = locToField(record.loc) || String(record.type ?? 'validation');
      const message = typeof record.msg === 'string' ? record.msg : JSON.stringify(record);
      return `${field}: ${message}`;
    });
    return [header, ...lines].filter(Boolean).join('\n');
  }

  if (typeof detail === 'string' && detail.trim()) return `${header}\n${detail.trim()}`;
  if (detail !== undefined && detail !== null) return `${header}\n${JSON.stringify(detail, null, 2)}`;
  return header;
}

async function parseResponseBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

async function requestJson<T>(url: string, init: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const body = await parseResponseBody(response);
  if (!response.ok) {
    throw new Error(formatApiError(response.status, response.statusText, body));
  }
  return body as T;
}

type SessionResponse = {
  customer_id: string;
  chat_token: string;
  expires_at: number;
};

export async function ensureChatSession(
  config: ApiConfig,
  currentSession: BrowserSession
): Promise<BrowserSession> {
  if (
    currentSession.chatToken &&
    currentSession.expiresAt &&
    currentSession.expiresAt > Date.now() + 60_000
  ) {
    return currentSession;
  }

  if (!isUuid(currentSession.customerId)) {
    throw new Error('customer_id: must be a valid UUID');
  }

  const sessionUrl = joinUrl(config.sessionServiceUrl, `/api/session?customer_id=${encodeURIComponent(currentSession.customerId)}`);

  let payload: SessionResponse;
  try {
    payload = await requestJson<SessionResponse>(sessionUrl, {
      method: 'GET',
      headers: { Accept: 'application/json' }
    });
  } catch (error) {
    if (error instanceof TypeError) {
      throw new Error('Session service is not running. Start it with npm run session-server.');
    }
    throw error;
  }

  if (!payload.chat_token) throw new Error('Session service did not return chat_token.');
  if (!isUuid(payload.customer_id)) throw new Error('Session service returned an invalid customer_id.');

  return {
    customerId: payload.customer_id,
    chatToken: payload.chat_token.trim().replace(/^Bearer\s+/i, ''),
    expiresAt: payload.expires_at > 2_000_000_000 ? payload.expires_at : payload.expires_at * 1000,
    source: 'session-service'
  };
}

export async function sendChatTurn(
  config: ApiConfig,
  session: BrowserSession,
  message: string,
  threadId?: string
): Promise<ChatTurnResponse> {
  const payload = cleanChatPayload({
    message,
    customer_id: session.customerId,
    thread_id: threadId || undefined
  });

  return requestJson<ChatTurnResponse>(`${normalizeBaseUrl(config.baseUrl)}/api/v1/chat/turn`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...authHeader(session.chatToken)
    },
    body: JSON.stringify(payload)
  });
}

export async function fetchThreadTraces(
  config: ApiConfig,
  threadId: string,
  limit = 20
): Promise<LiveTraceResponse> {
  if (!isUuid(threadId)) throw new Error('thread_id: must be a valid UUID');
  const response = await fetch(joinUrl(config.sessionServiceUrl, `/api/traces/${encodeURIComponent(threadId)}?limit=${limit}`));
  const body = await parseResponseBody(response);
  if (!response.ok) throw new Error(formatApiError(response.status, response.statusText, body));
  return body as LiveTraceResponse;
}

export async function checkChatHealth(config: ApiConfig): Promise<boolean> {
  try {
    const response = await fetch(`${normalizeBaseUrl(config.baseUrl)}/health`);
    return response.ok;
  } catch {
    return false;
  }
}
