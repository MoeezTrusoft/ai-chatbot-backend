import { parseReportJson } from './reportParser';
import type {
  ActivationResult,
  AdminApiConfig,
  AdminHealth,
  ChatTurnResponse,
  LoadedReport,
  LiveTraceFilters,
  LiveTraceResponse,
  RuleCandidate,
  RuleCandidateStatus,
  RulesArmyPreflight
} from '../types/reports';

type HttpMethod = 'GET' | 'POST' | 'PATCH' | 'DELETE';

export class AdminApiClient {
  constructor(private readonly config: AdminApiConfig) {}

  get isEnabled(): boolean {
    return Boolean(this.config.enabled && this.config.baseUrl);
  }

  async health(): Promise<AdminHealth> {
    return this.request('/api/admin/analysis/health');
  }

  async sendChatTurn(input: {
    message: string;
    thread_id?: string;
    customer_id?: string;
    chatToken?: string;
  }): Promise<ChatTurnResponse> {
    const token = input.chatToken || this.config.chatToken || this.config.token;
    return this.request('/api/v1/chat/turn', {
      method: 'POST',
      body: {
        thread_id: input.thread_id || undefined,
        customer_id: input.customer_id || this.config.customerId || undefined,
        message: input.message
      },
      token
    });
  }

  async latestPerformanceReport(): Promise<LoadedReport> {
    const data = await this.request('/api/admin/analysis/reports/production');
    return parseReportJson(data, 'Live production component report');
  }

  async trimatchContextReport(): Promise<LoadedReport> {
    const data = await this.request('/api/admin/analysis/reports/trimatch-context');
    return parseReportJson(data, 'Live Tri-Match context report');
  }

  async runContextEval(): Promise<LoadedReport> {
    const data = await this.request('/api/admin/analysis/evals/context-candidate/run', { method: 'POST' });
    return parseReportJson(data, 'Fresh Tri-Match context eval');
  }

  async latestLiveTraces(filters: LiveTraceFilters = {}): Promise<LiveTraceResponse> {
    return this.request(`/api/admin/analysis/traces/latest${this.queryString(filters)}`);
  }

  async threadLiveTraces(
    threadId: string,
    filters: LiveTraceFilters = {}
  ): Promise<LiveTraceResponse> {
    return this.request(
      `/api/admin/analysis/traces/${encodeURIComponent(threadId)}${this.queryString(filters)}`
    );
  }

  async listRuleCandidates(): Promise<RuleCandidate[]> {
    const data = await this.request('/api/admin/analysis/rules/candidates');
    if (Array.isArray(data)) return data as RuleCandidate[];
    if (data && typeof data === 'object' && Array.isArray((data as { candidates?: unknown }).candidates)) {
      return (data as { candidates: RuleCandidate[] }).candidates;
    }
    return [];
  }

  async createRuleCandidate(candidate: Partial<RuleCandidate>): Promise<RuleCandidate> {
    return this.request('/api/admin/analysis/rules/candidates', {
      method: 'POST',
      body: candidate
    });
  }

  async updateRuleCandidate(id: string, status: RuleCandidateStatus, note?: string): Promise<RuleCandidate> {
    return this.request(`/api/admin/analysis/rules/candidates/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: { status, review_note: note }
    });
  }

  async rulesArmyPreflight(): Promise<RulesArmyPreflight> {
    return this.request('/api/admin/analysis/rules-army-v2/preflight', { method: 'POST' });
  }

  async activateRulesArmyV2(options: {
    confirm_phrase: string;
    force?: boolean;
    mode?: 'active' | 'shadow';
  }): Promise<ActivationResult> {
    return this.request('/api/admin/analysis/rules-army-v2/activate', {
      method: 'POST',
      body: options
    });
  }

  async rollbackRules(backupDir: string): Promise<ActivationResult> {
    return this.request('/api/admin/analysis/rules-army-v2/rollback', {
      method: 'POST',
      body: { backup_dir: backupDir }
    });
  }

  private queryString(params: Record<string, unknown>): string {
    const query = new URLSearchParams();

    for (const [key, value] of Object.entries(params)) {
      if (value === undefined || value === null || value === '') continue;
      query.set(key, String(value));
    }

    const encoded = query.toString();
    return encoded ? `?${encoded}` : '';
  }

  private async request<T>(
    path: string,
    init?: { method?: HttpMethod; body?: unknown; token?: string }
  ): Promise<T> {
    if (!this.isEnabled) {
      throw new Error('Admin API is disabled. Enable it in Settings and provide a backend URL/token.');
    }
    const response = await fetch(`${this.config.baseUrl.replace(/\/$/, '')}${path}`, {
      method: init?.method ?? 'GET',
      headers: {
        'Content-Type': 'application/json',
        ...((init?.token ?? this.config.token) ? { Authorization: `Bearer ${init?.token ?? this.config.token}` } : {})
      },
      body: init?.body === undefined ? undefined : JSON.stringify(init.body)
    });
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const payload = await response.json();
        detail = typeof payload.detail === 'string' ? payload.detail : JSON.stringify(payload);
      } catch {
        detail = await response.text();
      }
      throw new Error(detail);
    }
    return response.json() as Promise<T>;
  }
}
