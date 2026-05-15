import type { LoadedReport, RuleCandidate } from '../types/reports';

const REPORTS_KEY = 'bookcraft.analysis.reports.v2';
const RULES_KEY = 'bookcraft.analysis.rules.v2';

export function loadReports(): LoadedReport[] {
  return load<LoadedReport[]>(REPORTS_KEY, []);
}

export function saveReports(reports: LoadedReport[]): void {
  save(REPORTS_KEY, reports.slice(0, 12));
}

export function loadRules(fallback: RuleCandidate[]): RuleCandidate[] {
  return load<RuleCandidate[]>(RULES_KEY, fallback);
}

export function saveRules(rules: RuleCandidate[]): void {
  save(RULES_KEY, rules);
}

function load<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function save(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Ignore storage quota/private mode errors. The console remains usable in-memory.
  }
}
