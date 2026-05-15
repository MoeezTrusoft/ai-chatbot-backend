export type ReportKind = 'performance' | 'context' | 'threaded' | 'unknown';
export type PageKey =
  | 'dashboard'
  | 'trace'
  | 'live'
  | 'lab'
  | 'waterfall'
  | 'intent'
  | 'trimatch'
  | 'rules'
  | 'activation'
  | 'evals'
  | 'providers'
  | 'quality'
  | 'pricing'
  | 'rag'
  | 'settings';

export type Tone = 'green' | 'blue' | 'cyan' | 'yellow' | 'orange' | 'red' | 'purple' | 'neutral';
export type RawJson = Record<string, unknown>;

export type RuntimeAtoms = {
  services?: string[];
  negated_services?: string[];
  negated_terms?: string[];
  context_markers?: string[];
  forbid_markers?: string[];
  query_cues?: string[];
  service_mentions?: Array<Record<string, unknown>>;
  word_counts?: number[];
  page_counts?: number[];
  currency?: string[];
  urls?: string[];
  emails?: string[];
  phones?: string[];
  manuscript_status?: string;
  [key: string]: unknown;
};

export type ProviderVote = {
  provider?: string;
  status?: string;
  error?: string | null;
  elapsed_ms?: number;
  vote?: {
    query_primary?: string | null;
    service_primary?: string | null;
    service_secondary?: string[];
    funnel_stage?: string | null;
    confidence?: number | null;
    evidence?: string[];
    [key: string]: unknown;
  } | null;
  [key: string]: unknown;
};

export type ProviderTrace = {
  total_vote_count?: number;
  usable_vote_count?: number;
  timeout_count?: number;
  circuit_open_count?: number;
  failed_count?: number;
  votes?: ProviderVote[];
  provider_counts?: Record<string, number>;
  provider_status_counts?: Record<string, number>;
  status_counts?: Record<string, number>;
  error_counts?: Record<string, number>;
  [key: string]: unknown;
};

export type DecisionTrace = {
  intent_present?: boolean;
  query_primary?: string | null;
  service_primary?: string | null;
  service_secondary?: string[];
  funnel_stage?: string | null;
  confidence?: number | null;
  source?: string | null;
  audit_trail?: string[];
  [key: string]: unknown;
};

export type AssistantTrace = {
  source?: string;
  text_preview?: string;
  bubbles?: Array<{ text?: string; [key: string]: unknown }>;
  response_quality?: Record<string, unknown>;
  [key: string]: unknown;
};

export type ReportTurn = {
  turn: number;
  message: string;
  elapsed_ms: number;
  components: {
    decision_layer?: DecisionTrace;
    providers?: ProviderTrace;
    assistant?: AssistantTrace;
    runtime_atoms?: RuntimeAtoms;
    pricing?: Record<string, unknown>;
    rag?: Record<string, unknown>;
    [key: string]: unknown;
  };
  raw_events?: RawJson[];
  issues?: string[];
  [key: string]: unknown;
};

export type ComponentSummary = {
  critical_issue_count?: number;
  soft_warning_count?: number;
  decision_layer_missing_count?: number;
  provider_health?: ProviderTrace;
  response_quality?: Record<string, number>;
  fallback_summary?: Record<string, number>;
  intent_counts?: Record<string, number>;
  service_counts?: Record<string, number>;
  route_source_counts?: Record<string, number>;
  [key: string]: unknown;
};

export type PerformanceReport = {
  summary: {
    valid?: boolean;
    generated_at?: string;
    base_url?: string;
    message_count?: number;
    success_count?: number;
    failure_count?: number;
    critical_issue_count?: number;
    soft_warning_count?: number;
    avg_latency_ms?: number;
    p50_latency_ms?: number;
    p95_latency_ms?: number;
    max_latency_ms?: number;
    [key: string]: unknown;
  };
  component_summary: ComponentSummary;
  turns: ReportTurn[];
  [key: string]: unknown;
};

export type ContextEvidence = {
  rule_id?: string;
  dimension?: string;
  target?: string;
  layer?: string;
  matched_text?: string;
  confidence?: number;
  negated?: boolean;
  hedged?: boolean;
  counterfactual?: boolean;
  [key: string]: unknown;
};

export type ContextCheck = {
  field: string;
  expected?: unknown;
  actual?: unknown;
  missing?: unknown[];
  passed: boolean;
};

export type ContextReportRow = {
  index: number;
  subset: string;
  text: string;
  expected: RawJson;
  actual: RawJson;
  passed: boolean;
  checks: ContextCheck[];
  evidence: ContextEvidence[];
};

export type ContextCandidateReport = {
  summary: {
    rule_dir: string;
    eval_path: string;
    example_count: number;
    passed_count: number;
    failed_count: number;
    valid_for_active_promotion: boolean;
    note?: string;
    [key: string]: unknown;
  };
  rows: ContextReportRow[];
};





export type ChatBubble = {
  text: string;
  bubble_index?: number;
  rich_segments?: Array<Record<string, unknown>>;
  [key: string]: unknown;
};

export type ChatTurnResponse = {
  thread_id: string;
  bubbles: ChatBubble[];
  intent?: {
    query_primary?: string;
    query_secondary?: string[];
    service_primary?: string;
    service_secondary?: string[];
    funnel_stage?: string;
    needs_clarification?: boolean;
    confidence?: number;
    rationale?: string;
    evidence?: string[];
    [key: string]: unknown;
  } | null;
  language_status?: string;
  debug_event_ids?: string[];
  [key: string]: unknown;
};

export type ChatLabExchange = {
  id: string;
  message: string;
  response?: ChatTurnResponse;
  error?: string;
  sentAt: string;
  elapsedMs?: number;
};

export type LiveTrace = {
  thread_id?: string;
  customer_id?: string | null;
  correlation_id?: string | null;
  message_preview?: string;
  elapsed_ms?: number;
  language_status?: string;
  assistant?: {
    source?: string;
    bubble_count?: number;
    preview?: string;
    [key: string]: unknown;
  };
  intent?: Record<string, unknown> | null;
  decision?: Record<string, unknown> | null;
  trimatch?: Record<string, unknown> | null;
  trimatch_shadow?: Record<string, unknown> | null;
  runtime_atoms?: RuntimeAtoms;
  components?: Record<string, unknown>;
  recorded_at?: string;
  [key: string]: unknown;
};



export type LiveTraceFilters = {
  limit?: number;
  source?: string;
  query_primary?: string;
  service_primary?: string;
  customer_id?: string;
  min_latency_ms?: number;
  has_forbid_markers?: boolean;
  has_negated_terms?: boolean;
};

export type LiveTraceResponse = {
  trace_path: string;
  count: number;
  traces: LiveTrace[];
  thread_id?: string;
};

export type LoadedReport = {
  id: string;
  name: string;
  kind: ReportKind;
  loadedAt: string;
  data: PerformanceReport | ContextCandidateReport | RawJson;
};

export type RuleCandidateStatus =
  | 'draft'
  | 'needs_review'
  | 'approved_for_staging'
  | 'changes_requested'
  | 'rejected'
  | 'promoted_to_staged'
  | 'blocked';

export type RuleCandidate = {
  id: string;
  title: string;
  status: RuleCandidateStatus;
  dimension: 'query_intent' | 'service_intent' | 'funnel_stage';
  target: string;
  layer: 'exact' | 'regex' | 'pattern' | 'semantic';
  confidence: number;
  shortcut_allowed: boolean;
  phrases?: string[];
  regex?: string;
  pattern?: string[];
  semantic_examples?: string[];
  reason: string;
  source_message: string;
  reviewer?: string;
  review_note?: string;
  collision_warnings: Array<{ severity: Tone; message: string }>;
  eval_result: {
    passed: number;
    failed: number;
    precision?: number;
    recall?: number;
  };
};

export type RegressionDiff = {
  id: string;
  label: string;
  before: string | number | null;
  after: string | number | null;
  severity: Tone;
};

export type AdminApiConfig = {
  enabled: boolean;
  baseUrl: string;
  token: string;
  chatToken?: string;
  customerId?: string;
};

export type AdminHealth = {
  ok: boolean;
  app?: string;
  mode?: string;
  timestamp?: string;
  paths?: Record<string, string>;
  [key: string]: unknown;
};

export type RulesArmyPreflight = {
  candidate: string;
  candidate_exists: boolean;
  active_rule_count?: number;
  candidate_rule_count?: number;
  verifier_valid?: boolean;
  context_report_valid?: boolean;
  warnings?: string[];
  errors?: string[];
  summary?: Record<string, unknown>;
  [key: string]: unknown;
};

export type ActivationResult = {
  activated: boolean;
  mode: 'active' | 'shadow' | string;
  backup_dir?: string;
  copied_files?: string[];
  verifier_valid?: boolean;
  message?: string;
  [key: string]: unknown;
};
