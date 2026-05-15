import type { ContextCandidateReport, LoadedReport, PerformanceReport, RawJson, ReportKind } from '../types/reports';

export function parseReportJson(data: unknown, name = 'Uploaded report'): LoadedReport {
  const kind = detectReportKind(data);
  if (kind === 'unknown') {
    throw new Error('Unsupported report JSON. Upload a production component report, threaded load report, or Tri-Match context candidate report.');
  }
  return {
    id: crypto.randomUUID(),
    kind,
    name,
    loadedAt: new Date().toISOString(),
    data: data as LoadedReport['data']
  };
}

export function detectReportKind(data: unknown): ReportKind {
  if (isPerformanceReport(data)) return Array.isArray((data as RawJson).threads) ? 'threaded' : 'performance';
  if (isContextReport(data)) return 'context';
  return 'unknown';
}

export function isPerformanceReport(data: unknown): data is PerformanceReport {
  if (!isRecord(data)) return false;
  return isRecord(data.summary) && Array.isArray(data.turns) && isRecord(data.component_summary);
}

export function isContextReport(data: unknown): data is ContextCandidateReport {
  if (!isRecord(data)) return false;
  return isRecord(data.summary) && Array.isArray(data.rows) && 'valid_for_active_promotion' in data.summary;
}

export function isRecord(value: unknown): value is RawJson {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}
