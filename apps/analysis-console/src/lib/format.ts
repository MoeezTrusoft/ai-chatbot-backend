import type { PerformanceReport, ReportTurn, Tone } from '../types/reports';

export function fmtNumber(value: unknown, fallback = '—'): string {
  if (typeof value !== 'number' || Number.isNaN(value)) return fallback;
  return new Intl.NumberFormat('en-US').format(value);
}

export function fmtMs(value: unknown, fallback = '—'): string {
  if (typeof value !== 'number' || Number.isNaN(value)) return fallback;
  return `${value.toFixed(value >= 100 ? 0 : 1)}ms`;
}

export function fmtPercent(value: unknown, fallback = '—'): string {
  if (typeof value !== 'number' || Number.isNaN(value)) return fallback;
  return `${Math.round(value * 100)}%`;
}

export function compactDate(value?: string): string {
  if (!value) return 'No timestamp';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('en', {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  }).format(date);
}

export function titleCase(value: string | null | undefined): string {
  if (!value) return 'Unknown';
  return value
    .replace(/_/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

export function toneForBool(value?: boolean): Tone {
  return value ? 'green' : 'red';
}

export function latencyTone(ms?: number): Tone {
  if (typeof ms !== 'number') return 'neutral';
  if (ms < 250) return 'green';
  if (ms < 1000) return 'cyan';
  if (ms < 3000) return 'yellow';
  if (ms < 8000) return 'orange';
  return 'red';
}

export function statusTone(status?: string): Tone {
  const normalized = (status ?? '').toLowerCase();
  if (['succeeded', 'success', 'passed', 'valid', 'healthy'].includes(normalized)) return 'green';
  if (['timed_out', 'timeout', 'warning', 'degraded'].includes(normalized)) return 'yellow';
  if (['failed', 'error', 'invalid', 'blocked', 'critical'].includes(normalized)) return 'red';
  if (['shortcut', 'shadow', 'candidate'].includes(normalized)) return 'purple';
  return 'neutral';
}

export function safeArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

export function objectEntries(value: unknown): Array<[string, unknown]> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return [];
  return Object.entries(value as Record<string, unknown>);
}

export function getAssistantText(turn?: ReportTurn): string {
  const assistant = turn?.components?.assistant;
  if (!assistant) return '';
  if (typeof assistant.text_preview === 'string') return assistant.text_preview;
  const first = assistant.bubbles?.[0]?.text;
  return typeof first === 'string' ? first : '';
}

export function routeSourceCounts(report?: PerformanceReport): Record<string, number> {
  const explicit = report?.component_summary?.route_source_counts;
  if (explicit && Object.keys(explicit).length) return explicit;
  const counts: Record<string, number> = {};
  for (const turn of report?.turns ?? []) {
    const source = turn.components?.assistant?.source ?? turn.components?.decision_layer?.source ?? 'unknown';
    counts[source] = (counts[source] ?? 0) + 1;
  }
  return counts;
}

export function intentCounts(report?: PerformanceReport): Record<string, number> {
  const explicit = report?.component_summary?.intent_counts;
  if (explicit && Object.keys(explicit).length) return explicit;
  const counts: Record<string, number> = {};
  for (const turn of report?.turns ?? []) {
    const key = turn.components?.decision_layer?.query_primary ?? 'unknown';
    counts[key] = (counts[key] ?? 0) + 1;
  }
  return counts;
}

export function serviceCounts(report?: PerformanceReport): Record<string, number> {
  const explicit = report?.component_summary?.service_counts;
  if (explicit && Object.keys(explicit).length) return explicit;
  const counts: Record<string, number> = {};
  for (const turn of report?.turns ?? []) {
    const primary = turn.components?.decision_layer?.service_primary;
    if (primary) counts[primary] = (counts[primary] ?? 0) + 1;
  }
  return counts;
}

export function truncate(value: string, length = 120): string {
  return value.length <= length ? value : `${value.slice(0, length - 1)}…`;
}

export function downloadJson(filename: string, payload: unknown): void {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
