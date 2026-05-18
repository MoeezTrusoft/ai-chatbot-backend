import type { ApiConfig, BrowserSession, TestThread } from './types';

const THREADS_KEY = 'bookcraft.salesTestRoom.threads.v2';
const CONFIG_KEY = 'bookcraft.salesTestRoom.config.v2';
const LEGACY_SESSION_KEY = 'bookcraft.salesTestRoom.session.v2';
const CUSTOMER_ID_KEY = 'bookcraft.salesTestRoom.customerId.v1';
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function defaultHost(): string {
  if (typeof window === 'undefined') return 'localhost';
  return window.location.hostname || 'localhost';
}

export const defaultConfig: ApiConfig = {
  baseUrl: `http://${defaultHost()}:8000`,
  sessionServiceUrl: `http://${defaultHost()}:8787`
};

export function isStoredUuid(value: unknown): value is string {
  return typeof value === 'string' && UUID_RE.test(value);
}

export function uid(prefix: string): string {
  const random = createUuid();
  return `${prefix}-${random}`;
}

function createUuid(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }

  const bytes = new Uint8Array(16);
  if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
    crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }

  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function loadThreads(): TestThread[] {
  try {
    const raw = localStorage.getItem(THREADS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as TestThread[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function saveThreads(threads: TestThread[]): void {
  localStorage.setItem(THREADS_KEY, JSON.stringify(threads.slice(0, 50)));
}

export function loadConfig(): ApiConfig {
  try {
    const raw = localStorage.getItem(CONFIG_KEY);
    if (!raw) return defaultConfig;
    const parsed = JSON.parse(raw) as Partial<ApiConfig>;
    return {
      baseUrl: typeof parsed.baseUrl === 'string' && parsed.baseUrl.trim() ? parsed.baseUrl : defaultConfig.baseUrl,
      sessionServiceUrl: typeof parsed.sessionServiceUrl === 'string' && parsed.sessionServiceUrl.trim()
        ? parsed.sessionServiceUrl
        : defaultConfig.sessionServiceUrl
    };
  } catch {
    return defaultConfig;
  }
}

export function saveConfig(config: ApiConfig): void {
  localStorage.setItem(CONFIG_KEY, JSON.stringify(config));
}

export function getOrCreateBrowserSession(): BrowserSession {
  const customerId = getOrCreateCustomerId();
  return {
    customerId,
    source: 'local'
  };
}

function getOrCreateCustomerId(): string {
  try {
    const current = localStorage.getItem(CUSTOMER_ID_KEY);
    if (isStoredUuid(current)) return current;

    const legacyRaw = localStorage.getItem(LEGACY_SESSION_KEY);
    if (legacyRaw) {
      const legacy = JSON.parse(legacyRaw) as Partial<BrowserSession>;
      if (isStoredUuid(legacy.customerId)) {
        localStorage.setItem(CUSTOMER_ID_KEY, legacy.customerId);
        return legacy.customerId;
      }
    }
  } catch {
    // Replace invalid legacy values below.
  }

  const next = createUuid();
  localStorage.setItem(CUSTOMER_ID_KEY, next);
  return next;
}

export function saveBrowserSession(session: BrowserSession): void {
  if (isStoredUuid(session.customerId)) {
    localStorage.setItem(CUSTOMER_ID_KEY, session.customerId);
  }
}

export function resetBrowserSession(): BrowserSession {
  const customerId = createUuid();
  localStorage.setItem(CUSTOMER_ID_KEY, customerId);
  const session: BrowserSession = {
    customerId,
    source: 'local'
  };
  return session;
}
