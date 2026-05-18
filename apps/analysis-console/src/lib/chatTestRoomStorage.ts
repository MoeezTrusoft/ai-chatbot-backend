import type { ChatTurnResponse, LiveTrace } from '../types/reports';

export type SalesChatRole = 'customer' | 'assistant';

export type SalesChatMessage = {
  id: string;
  role: SalesChatRole;
  text: string;
  createdAt: string;
  turnId: string;
};

export type SalesChatTurn = {
  id: string;
  customerText: string;
  assistantText: string;
  createdAt: string;
  elapsedMs?: number;
  response?: ChatTurnResponse;
  trace?: LiveTrace;
};

export type SalesChatThread = {
  id: string;
  threadId?: string;
  title: string;
  customerId?: string;
  createdAt: string;
  updatedAt: string;
  turns: SalesChatTurn[];
};

const STORAGE_KEY = 'bookcraft.chatTestRoom.threads.v1';

export function makeLocalId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function loadSalesChatThreads(): SalesChatThread[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];

    const parsed = JSON.parse(raw) as SalesChatThread[];
    if (!Array.isArray(parsed)) return [];

    return parsed;
  } catch {
    return [];
  }
}

export function saveSalesChatThreads(threads: SalesChatThread[]): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(threads.slice(0, 40)));
}

export function clearSalesChatThreads(): void {
  localStorage.removeItem(STORAGE_KEY);
}
