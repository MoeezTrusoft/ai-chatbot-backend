import { useEffect, useMemo, useState } from 'react';
import { DistributionBars, LatencyBudget, Sparkline, WaterfallRows } from './components/charts';
import { ReportUploader } from './components/ReportUploader';
import { Badge, Button, Card, CardHeader, ChipList, EmptyState, JsonViewer, KeyValueGrid, MetricCard, SectionTitle } from './components/ui';
import { initialRuleCandidates, sampleReports } from './data/sampleData';
import { AdminApiClient } from './lib/apiClient';
import { defaultApiConfig, loadApiConfig, saveApiConfig } from './lib/apiConfig';
import { compactDate, downloadJson, fmtMs, fmtNumber, getAssistantText, intentCounts, latencyTone, objectEntries, routeSourceCounts, serviceCounts, titleCase, truncate } from './lib/format';
import { loadReports, loadRules, saveReports, saveRules } from './lib/storage';
import type { ActivationResult, AdminApiConfig, AdminHealth, ChatLabExchange, ChatTurnResponse, ContextCandidateReport, ContextReportRow, LiveTrace, LoadedReport, PageKey, PerformanceReport, ProviderVote, ReportTurn, RuleCandidate, RuleCandidateStatus, RulesArmyPreflight, Tone } from './types/reports';

const nav: Array<{ key: PageKey; label: string; hint: string }> = [
  { key: 'dashboard', label: 'Dashboard', hint: 'System health' },
  { key: 'trace', label: 'Trace Viewer', hint: 'Turn inspection' },
  { key: 'live', label: 'Live Traces', hint: 'Fresh chat turns' },
  { key: 'lab', label: 'Chat Test Lab', hint: 'Send + inspect' },
  { key: 'waterfall', label: 'Waterfall', hint: 'Latency timeline' },
  { key: 'intent', label: 'Intent', hint: 'Votes + decision' },
  { key: 'trimatch', label: 'Tri-Match', hint: 'Rules + evidence' },
  { key: 'rules', label: 'Rule Approval', hint: 'Mutate candidates' },
  { key: 'activation', label: 'Rules Army v2', hint: 'Preflight + activate' },
  { key: 'evals', label: 'Evals', hint: 'Regression runner' },
  { key: 'providers', label: 'Providers', hint: 'LLM health' },
  { key: 'quality', label: 'Safety', hint: 'Risks + quality' },
  { key: 'pricing', label: 'Pricing', hint: 'Quote traces' },
  { key: 'rag', label: 'RAG', hint: 'Retrieval traces' },
  { key: 'settings', label: 'Settings', hint: 'API + import' }
];

export function App() {
  const [page, setPage] = useState<PageKey>('dashboard');
  const [reports, setReports] = useState<LoadedReport[]>(() => loadReports().length ? loadReports() : sampleReports);
  const [rules, setRules] = useState<RuleCandidate[]>(() => loadRules(initialRuleCandidates));
  const [activeReportId, setActiveReportId] = useState(reports[0]?.id ?? 'sample-performance');
  const [selectedTurn, setSelectedTurn] = useState(1);
  const [selectedRow, setSelectedRow] = useState(1);
  const [selectedCandidate, setSelectedCandidate] = useState<string>(rules[0]?.id ?? '');
  const [search, setSearch] = useState('');
  const [apiConfig, setApiConfig] = useState<AdminApiConfig>(() => loadApiConfig());
  const [apiHealth, setApiHealth] = useState<AdminHealth | null>(null);
  const [apiBusy, setApiBusy] = useState(false);
  const [apiMessage, setApiMessage] = useState('');
  const [liveTraces, setLiveTraces] = useState<LiveTrace[]>([]);
  const [liveTraceFilters, setLiveTraceFilters] = useState({
    limit: 100,
    source: '',
    query_primary: '',
    service_primary: '',
    customer_id: '',
    min_latency_ms: '',
    has_forbid_markers: '',
    has_negated_terms: ''
  });

  const api = useMemo(() => new AdminApiClient(apiConfig), [apiConfig]);

  useEffect(() => saveReports(reports), [reports]);
  useEffect(() => saveRules(rules), [rules]);
  useEffect(() => saveApiConfig(apiConfig), [apiConfig]);

  const activeReport = reports.find((report) => report.id === activeReportId) ?? reports[0];
  const performanceReports = reports.filter((report): report is LoadedReport & { data: PerformanceReport } => report.kind === 'performance' || report.kind === 'threaded');
  const contextReports = reports.filter((report): report is LoadedReport & { data: ContextCandidateReport } => report.kind === 'context');
  const performance = (activeReport?.kind === 'performance' || activeReport?.kind === 'threaded') ? activeReport.data as PerformanceReport : performanceReports[0]?.data;
  const context = activeReport?.kind === 'context' ? activeReport.data as ContextCandidateReport : contextReports[0]?.data;

  function addReport(report: LoadedReport) {
    setReports((current) => [report, ...current.filter((item) => item.id !== report.id)]);
    setActiveReportId(report.id);
    if (report.kind === 'context') setSelectedRow((report.data as ContextCandidateReport).rows[0]?.index ?? 1);
    if (report.kind === 'performance' || report.kind === 'threaded') setSelectedTurn((report.data as PerformanceReport).turns[0]?.turn ?? 1);
  }

  async function withApi(label: string, action: () => Promise<void>) {
    setApiBusy(true);
    setApiMessage(`${label}...`);
    try {
      await action();
      setApiMessage(`${label}: done`);
    } catch (error) {
      setApiMessage(`${label}: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setApiBusy(false);
    }
  }

  async function connectApi() {
    await withApi('API health check', async () => {
      const health = await api.health();
      setApiHealth(health);
    });
  }

  async function importLiveReports() {
    await withApi('Import live reports', async () => {
      const [perf, tri] = await Promise.allSettled([api.latestPerformanceReport(), api.trimatchContextReport()]);
      if (perf.status === 'fulfilled') addReport(perf.value);
      if (tri.status === 'fulfilled') addReport(tri.value);
      if (perf.status === 'rejected' && tri.status === 'rejected') throw perf.reason;
    });
  }

  async function refreshLiveTraces() {
    await withApi('Refresh live traces', async () => {
      const response = await api.latestLiveTraces({
        limit: Number(liveTraceFilters.limit) || 100,
        source: liveTraceFilters.source || undefined,
        query_primary: liveTraceFilters.query_primary || undefined,
        service_primary: liveTraceFilters.service_primary || undefined,
        customer_id: liveTraceFilters.customer_id || undefined,
        min_latency_ms: liveTraceFilters.min_latency_ms
          ? Number(liveTraceFilters.min_latency_ms)
          : undefined,
        has_forbid_markers: liveTraceFilters.has_forbid_markers === ''
          ? undefined
          : liveTraceFilters.has_forbid_markers === 'true',
        has_negated_terms: liveTraceFilters.has_negated_terms === ''
          ? undefined
          : liveTraceFilters.has_negated_terms === 'true'
      });
      setLiveTraces(response.traces);
    });
  }

  async function syncRuleCandidates() {
    await withApi('Sync rule candidates', async () => setRules(await api.listRuleCandidates()));
  }

  async function updateCandidate(id: string, status: RuleCandidateStatus, note?: string) {
    const localUpdate = () => setRules((current) => current.map((rule) => rule.id === id ? { ...rule, status, review_note: note ?? rule.review_note, reviewer: apiConfig.enabled ? 'API reviewer' : 'Local reviewer' } : rule));
    if (!apiConfig.enabled) return localUpdate();
    await withApi(`Update ${id}`, async () => {
      const updated = await api.updateRuleCandidate(id, status, note);
      setRules((current) => current.map((rule) => rule.id === id ? { ...rule, ...updated } : rule));
    });
  }

  return (
    <div className="app-bg">
      <aside className="sidebar">
        <div className="brand"><div className="brand-mark">BC</div><div><h1>BookCraft AI</h1><p>Ops Console</p></div></div>
        <div className="nav-label">One-stop shop</div>
        {nav.map((item) => <button key={item.key} className={`nav-btn ${page === item.key ? 'active' : ''}`} onClick={() => setPage(item.key)}><span className="nav-dot" /><span><b>{item.label}</b><small>{item.hint}</small></span></button>)}
        <div className="sidebar-card">
          <h3>Runtime posture</h3>
          <p>{apiConfig.enabled ? 'Live admin API enabled. Mutations require backend authorization and are audited.' : 'Local/report mode. Enable admin API in Settings for live mutations.'}</p>
          <div className="chips"><Badge tone={apiConfig.enabled ? 'green' : 'purple'}>{apiConfig.enabled ? 'api-live' : 'read-only'}</Badge><Badge tone="cyan">v2-gated</Badge></div>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div className="topbar-left"><Badge tone={apiHealth?.ok ? 'green' : apiConfig.enabled ? 'yellow' : 'purple'}>{apiHealth?.ok ? 'api connected' : apiConfig.enabled ? 'api configured' : 'local mode'}</Badge><select className="select" value={activeReportId} onChange={(event) => setActiveReportId(event.target.value)}>{reports.map((report) => <option value={report.id} key={report.id}>{report.name}</option>)}</select></div>
          <input className="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search messages, rules, providers, contexts…" />
          <div className="topbar-right"><Badge tone="blue">{reports.length} reports</Badge><Badge tone="cyan">{rules.length} candidates</Badge></div>
        </header>
        {apiMessage ? <div className="api-banner"><span>{apiMessage}</span>{apiBusy ? <Badge tone="yellow">working</Badge> : <Badge tone="green">ready</Badge>}</div> : null}

        {page === 'dashboard' && <Dashboard performance={performance} context={context} onLoad={addReport} onLiveImport={importLiveReports} apiEnabled={apiConfig.enabled} />}
        {page === 'trace' && <TraceViewer report={performance} selectedTurn={selectedTurn} onSelect={setSelectedTurn} search={search} />}
        {page === 'live' && <LiveTracePage traces={liveTraces} refresh={refreshLiveTraces} apiEnabled={apiConfig.enabled} search={search} filters={liveTraceFilters} setFilters={setLiveTraceFilters} />}
        {page === 'lab' && <ChatTestLab api={api} apiConfig={apiConfig} setApiConfig={setApiConfig} withApi={withApi} />}
        {page === 'waterfall' && <Waterfall report={performance} selectedTurn={selectedTurn} onSelect={setSelectedTurn} />}
        {page === 'intent' && <IntentInspector report={performance} selectedTurn={selectedTurn} onSelect={setSelectedTurn} />}
        {page === 'trimatch' && <TriMatchView report={context} selectedRow={selectedRow} onSelect={setSelectedRow} search={search} />}
        {page === 'rules' && <RuleApproval rules={rules} selected={selectedCandidate} setSelected={setSelectedCandidate} updateCandidate={updateCandidate} syncRules={syncRuleCandidates} apiEnabled={apiConfig.enabled} />}
        {page === 'activation' && <RulesArmyActivation api={api} apiEnabled={apiConfig.enabled} withApi={withApi} addReport={addReport} />}
        {page === 'evals' && <EvalRegression reports={performanceReports} context={context} api={api} apiEnabled={apiConfig.enabled} withApi={withApi} addReport={addReport} />}
        {page === 'providers' && <ProviderHealth report={performance} />}
        {page === 'quality' && <SafetyQuality report={performance} context={context} />}
        {page === 'pricing' && <PricingTrace report={performance} />}
        {page === 'rag' && <RagTrace report={performance} />}
        {page === 'settings' && <Settings reports={reports} onLoad={addReport} setReports={setReports} rules={rules} apiConfig={apiConfig} setApiConfig={setApiConfig} connectApi={connectApi} importLiveReports={importLiveReports} apiHealth={apiHealth} />}
      </main>
    </div>
  );
}

function Dashboard({ performance, context, onLoad, onLiveImport, apiEnabled }: { performance?: PerformanceReport; context?: ContextCandidateReport; onLoad: (report: LoadedReport) => void; onLiveImport: () => void; apiEnabled: boolean }) {
  const latencies = performance?.turns?.map((turn) => turn.elapsed_ms) ?? [];
  return <div className="page"><SectionTitle kicker="Executive Overview" title="AI operations command center" subtitle="Production health, traces, rules, evals, activation controls, and live backend integration." right={<Badge tone={performance?.summary?.valid ? 'green' : 'red'}>{performance?.summary?.valid ? 'valid' : 'attention'}</Badge>} />
    <div className="grid cols-4"><MetricCard label="p95 latency" value={fmtMs(performance?.summary?.p95_latency_ms)} tone={latencyTone(performance?.summary?.p95_latency_ms as number)} /><MetricCard label="Critical issues" value={fmtNumber(performance?.summary?.critical_issue_count)} tone={(performance?.summary?.critical_issue_count ?? 0) === 0 ? 'green' : 'red'} /><MetricCard label="Context eval" value={`${context?.summary?.passed_count ?? 0}/${context?.summary?.example_count ?? 0}`} tone={(context?.summary?.failed_count ?? 1) === 0 ? 'green' : 'yellow'} /><MetricCard label="Rules Army v2" value={context?.summary?.valid_for_active_promotion ? 'Allowed' : 'Gated'} tone="purple" /></div>
    <div className="grid cols-2 mt"><Card><CardHeader title="Latency profile" subtitle="Turn latency sparkline and budget." /><div className="card-body"><Sparkline values={latencies.length ? latencies : [0]} /><LatencyBudget value={performance?.summary?.p95_latency_ms as number} /></div></Card><Card><CardHeader title="Import reports / live sync" subtitle="Use local JSON or backend admin APIs." action={apiEnabled ? <Button onClick={onLiveImport}>Import live</Button> : <Badge tone="purple">local mode</Badge>} /><div className="card-body"><ReportUploader onLoad={onLoad} /></div></Card><Card><CardHeader title="Route source distribution" /><div className="card-body"><DistributionBars data={routeSourceCounts(performance)} /></div></Card><Card><CardHeader title="Intent distribution" /><div className="card-body"><DistributionBars data={intentCounts(performance)} /></div></Card></div>
  </div>;
}

function TraceViewer({ report, selectedTurn, onSelect, search }: { report?: PerformanceReport; selectedTurn: number; onSelect: (turn: number) => void; search: string }) {
  const turns = filterTurns(report, search);
  const selected = report?.turns?.find((turn) => turn.turn === selectedTurn) ?? turns[0];
  return <div className="page"><SectionTitle kicker="Conversation Trace" title="Turn-by-turn component inspection" subtitle="Inspect message, response, atoms, provider votes, raw events, and safety flags." /><div className="split"><Card><CardHeader title={`${turns.length} turns`} /><TurnTable turns={turns} selected={selected?.turn} onSelect={onSelect} /></Card><TurnInspector turn={selected} /></div></div>;
}

function TurnTable({ turns, selected, onSelect }: { turns: ReportTurn[]; selected?: number; onSelect: (turn: number) => void }) {
  return <div className="table-wrap"><table><thead><tr><th>Turn</th><th>Latency</th><th>Intent</th><th>Service</th><th>Message</th></tr></thead><tbody>{turns.map((turn) => <tr key={turn.turn} onClick={() => onSelect(turn.turn)} className={turn.turn === selected ? 'selected' : ''}><td>#{turn.turn}</td><td><Badge tone={latencyTone(turn.elapsed_ms)}>{fmtMs(turn.elapsed_ms)}</Badge></td><td>{titleCase(turn.components?.decision_layer?.query_primary)}</td><td>{titleCase(turn.components?.decision_layer?.service_primary)}</td><td>{truncate(turn.message, 78)}</td></tr>)}</tbody></table></div>;
}

function TurnInspector({ turn }: { turn?: ReportTurn }) {
  if (!turn) return <Card className="pad"><EmptyState title="No turn selected" /></Card>;
  const decision = turn.components?.decision_layer;
  const atoms = turn.components?.runtime_atoms;
  return <div className="stack"><Card><CardHeader title={`Turn #${turn.turn}`} subtitle={turn.message} action={<Badge tone={latencyTone(turn.elapsed_ms)}>{fmtMs(turn.elapsed_ms)}</Badge>} /><div className="card-body"><p className="assistant-text">{getAssistantText(turn) || 'No assistant preview available.'}</p><KeyValueGrid items={[[ 'Source', decision?.source ?? turn.components?.assistant?.source ?? 'unknown' ], [ 'Intent', titleCase(decision?.query_primary) ], [ 'Service', titleCase(decision?.service_primary) ], [ 'Secondary', <ChipList values={decision?.service_secondary ?? []} tone="cyan" /> ], [ 'Funnel', titleCase(decision?.funnel_stage) ], [ 'Confidence', typeof decision?.confidence === 'number' ? decision.confidence.toFixed(3) : '—' ]]} /></div></Card><RuntimeAtomsPanel atoms={atoms} /><ProviderVotes votes={turn.components?.providers?.votes ?? []} /><Card><CardHeader title="Raw events" /><div className="card-body"><JsonViewer value={turn.raw_events ?? []} /></div></Card></div>;
}

function RuntimeAtomsPanel({ atoms }: { atoms?: Record<string, unknown> }) {
  return <Card><CardHeader title="Runtime context atoms" subtitle="Deterministic facts available to routing, safety, and the analysis console." /><div className="card-body atom-grid">{['services','negated_services','negated_terms','context_markers','forbid_markers','query_cues','word_counts','page_counts','currency','urls','emails','phones'].map((key) => <div className="atom-box" key={key}><span>{titleCase(key)}</span><ChipList values={(atoms?.[key] as Array<string | number>) ?? []} tone={key.includes('forbid') || key.includes('negated') ? 'red' : key.includes('context') ? 'purple' : 'cyan'} /></div>)}</div></Card>;
}

function ProviderVotes({ votes }: { votes: ProviderVote[] }) {
  return <Card><CardHeader title="Provider votes" subtitle="LLM providers, deterministic shortcuts, and fallback votes." /><div className="table-wrap"><table><thead><tr><th>Provider</th><th>Status</th><th>Intent</th><th>Service</th><th>Confidence</th><th>Error</th></tr></thead><tbody>{votes.length ? votes.map((vote, index) => <tr key={`${vote.provider}-${index}`}><td>{vote.provider ?? 'unknown'}</td><td><Badge tone={statusTone(vote.status)}>{vote.status ?? 'unknown'}</Badge></td><td>{titleCase(vote.vote?.query_primary)}</td><td>{titleCase(vote.vote?.service_primary)}</td><td>{typeof vote.vote?.confidence === 'number' ? vote.vote.confidence.toFixed(3) : '—'}</td><td>{vote.error ? <Badge tone="red">{truncate(vote.error, 40)}</Badge> : '—'}</td></tr>) : <tr><td colSpan={6}><EmptyState title="No provider votes in selected turn" /></td></tr>}</tbody></table></div></Card>;
}



function ChatTestLab({
  api,
  apiConfig,
  setApiConfig,
  withApi
}: {
  api: AdminApiClient;
  apiConfig: AdminApiConfig;
  setApiConfig: (config: AdminApiConfig) => void;
  withApi: (label: string, action: () => Promise<void>) => Promise<void>;
}) {
  const [threadId, setThreadId] = useState('');
  const [message, setMessage] = useState('I need editing, formatting, and marketing for my manuscript.');
  const [exchanges, setExchanges] = useState<ChatLabExchange[]>([]);
  const [threadTraces, setThreadTraces] = useState<LiveTrace[]>([]);
  const [selectedTraceIndex, setSelectedTraceIndex] = useState(0);
  const [chatBusy, setChatBusy] = useState(false);

  const latestExchange = exchanges[0];
  const selectedTrace = threadTraces[selectedTraceIndex] ?? threadTraces[0];

  function newThread() {
    const next = crypto.randomUUID();
    setThreadId(next);
    setExchanges([]);
    setThreadTraces([]);
    setSelectedTraceIndex(0);
  }

  async function refreshThreadTraces(targetThreadId = threadId) {
    if (!targetThreadId) return;
    await withApi('Refresh thread traces', async () => {
      const response = await api.threadLiveTraces(targetThreadId, { limit: 50 });
      setThreadTraces(response.traces);
      setSelectedTraceIndex(0);
    });
  }

  async function sendMessage() {
    const trimmed = message.trim();
    if (!trimmed) return;

    const started = performance.now();
    setChatBusy(true);

    try {
      const response = await api.sendChatTurn({
        message: trimmed,
        thread_id: threadId || undefined,
        customer_id: apiConfig.customerId || undefined,
        chatToken: apiConfig.chatToken || undefined
      });

      const elapsedMs = Math.round((performance.now() - started) * 100) / 100;
      const resolvedThreadId = response.thread_id;

      setThreadId(resolvedThreadId);
      setExchanges((current) => [
        {
          id: crypto.randomUUID(),
          message: trimmed,
          response,
          sentAt: new Date().toISOString(),
          elapsedMs
        },
        ...current
      ]);

      setMessage('');

      const traces = await api.threadLiveTraces(resolvedThreadId, { limit: 50 });
      setThreadTraces(traces.traces);
      setSelectedTraceIndex(0);
    } catch (error) {
      setExchanges((current) => [
        {
          id: crypto.randomUUID(),
          message: trimmed,
          error: error instanceof Error ? error.message : String(error),
          sentAt: new Date().toISOString(),
          elapsedMs: Math.round((performance.now() - started) * 100) / 100
        },
        ...current
      ]);
    } finally {
      setChatBusy(false);
    }
  }

  return (
    <div className="page">
      <SectionTitle
        kicker="Chat Test Lab"
        title="Initiate a thread, send messages, and inspect routing intelligence"
        subtitle="A full test bench for chatbot response, classified intent, provider votes, Tri-Match evidence, runtime atoms, latency, and raw trace diagnostics."
        right={<Badge tone={apiConfig.enabled ? 'green' : 'purple'}>{apiConfig.enabled ? 'admin API live' : 'admin API disabled'}</Badge>}
      />

      <div className="grid cols-4">
        <MetricCard label="Thread traces" value={threadTraces.length} tone="blue" />
        <MetricCard label="Last chat latency" value={fmtMs(latestExchange?.elapsedMs)} tone={latencyTone(latestExchange?.elapsedMs)} />
        <MetricCard label="Trace latency" value={fmtMs(selectedTrace?.elapsed_ms)} tone={latencyTone(selectedTrace?.elapsed_ms)} />
        <MetricCard label="Intent confidence" value={formatConfidence(latestExchange?.response?.intent?.confidence ?? traceConfidence(selectedTrace))} tone="green" />
      </div>

      <div className="grid cols-2 mt">
        <Card>
          <CardHeader title="Connection and thread setup" subtitle="Chat uses the normal JWT. Trace fetch uses the admin analysis token." />
          <div className="card-body stack-small">
            <label className="field-label">Chat JWT for /api/v1/chat/turn</label>
            <textarea
              className="textarea mono"
              rows={3}
              placeholder="Paste CHAT_JWT here"
              value={apiConfig.chatToken ?? ''}
              onChange={(event) => setApiConfig({ ...apiConfig, chatToken: event.target.value })}
            />

            <label className="field-label">Customer ID</label>
            <input
              className="input mono"
              placeholder="SMOKE_CUSTOMER_ID or customer UUID"
              value={apiConfig.customerId ?? ''}
              onChange={(event) => setApiConfig({ ...apiConfig, customerId: event.target.value })}
            />

            <label className="field-label">Thread ID</label>
            <div className="inline-controls">
              <input
                className="input mono"
                placeholder="Empty = backend creates one"
                value={threadId}
                onChange={(event) => setThreadId(event.target.value)}
              />
              <Button onClick={newThread}>New thread</Button>
              <Button variant="ghost" disabled={!threadId} onClick={() => refreshThreadTraces()}>Refresh traces</Button>
            </div>
          </div>
        </Card>

        <Card>
          <CardHeader title="Send message" subtitle="Send a user message, receive bubbles, then auto-load the matching live trace." />
          <div className="card-body stack-small">
            <textarea
              className="textarea"
              rows={8}
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              placeholder="Type a test message..."
              onKeyDown={(event) => {
                if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
                  void sendMessage();
                }
              }}
            />
            <div className="action-row">
              <Button disabled={chatBusy || !apiConfig.chatToken} onClick={sendMessage}>
                {chatBusy ? 'Sending…' : 'Send message'}
              </Button>
              <Badge tone="cyan">⌘/Ctrl + Enter</Badge>
              {!apiConfig.chatToken ? <Badge tone="yellow">chat JWT required</Badge> : null}
            </div>
          </div>
        </Card>
      </div>

      <div className="split mt">
        <Card>
          <CardHeader title="Conversation responses" subtitle="Newest exchanges first. Shows raw chatbot output and classified intent returned by the chat endpoint." />
          <div className="card-body chat-stream">
            {exchanges.length ? exchanges.map((exchange) => (
              <div className="chat-exchange" key={exchange.id}>
                <div className="chat-user">
                  <div className="chat-meta">
                    <Badge tone="blue">user</Badge>
                    <span>{compactDate(exchange.sentAt)}</span>
                    <Badge tone={latencyTone(exchange.elapsedMs)}>{fmtMs(exchange.elapsedMs)}</Badge>
                  </div>
                  <p>{exchange.message}</p>
                </div>

                {exchange.error ? (
                  <div className="chat-error">
                    <Badge tone="red">error</Badge>
                    <span>{exchange.error}</span>
                  </div>
                ) : null}

                {exchange.response ? (
                  <div className="chat-assistant">
                    <div className="chat-meta">
                      <Badge tone="green">assistant</Badge>
                      <Badge tone="purple">{exchange.response.intent?.query_primary ?? 'unknown intent'}</Badge>
                      <Badge tone="cyan">{exchange.response.intent?.service_primary ?? 'unknown service'}</Badge>
                      <Badge tone="blue">{formatConfidence(exchange.response.intent?.confidence)}</Badge>
                    </div>

                    <div className="bubble-list">
                      {exchange.response.bubbles.map((bubble, index) => (
                        <div className="response-bubble" key={`${exchange.id}-${index}`}>
                          <span>Bubble {bubble.bubble_index ?? index}</span>
                          <p>{bubble.text}</p>
                        </div>
                      ))}
                    </div>

                    <JsonViewer value={{ intent: exchange.response.intent, language_status: exchange.response.language_status }} maxHeight={260} />
                  </div>
                ) : null}
              </div>
            )) : (
              <EmptyState title="No messages sent yet" body="Paste a Chat JWT, set a customer ID, and send a message to start testing." />
            )}
          </div>
        </Card>

        <div className="stack">
          <Card>
            <CardHeader
              title="Live trace diagnosis"
              subtitle={selectedTrace?.thread_id ? `Trace for ${selectedTrace.thread_id}` : 'Send a message to generate a trace.'}
              action={<Badge tone={latencyTone(selectedTrace?.elapsed_ms)}>{fmtMs(selectedTrace?.elapsed_ms)}</Badge>}
            />
            <div className="card-body">
              <KeyValueGrid
                items={[
                  ['Recorded', compactDate(selectedTrace?.recorded_at)],
                  ['Assistant source', selectedTrace?.assistant?.source ?? '—'],
                  ['Query', traceQueryPrimary(selectedTrace)],
                  ['Service', traceServicePrimary(selectedTrace)],
                  ['Funnel', traceFunnelStage(selectedTrace)],
                  ['Confidence', formatConfidence(traceConfidence(selectedTrace))]
                ]}
              />
            </div>
          </Card>

          <ProviderVotes votes={traceProviderVotes(selectedTrace)} />

          <TriMatchRuleHits trace={selectedTrace} />

          <RuntimeAtomsPanel atoms={selectedTrace?.runtime_atoms} />

          <Card>
            <CardHeader title="Trace timeline for this thread" subtitle="Click a trace row to inspect older/newer turns in the same thread." />
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Latency</th>
                    <th>Source</th>
                    <th>Intent</th>
                    <th>Message</th>
                  </tr>
                </thead>
                <tbody>
                  {threadTraces.length ? threadTraces.map((trace, index) => (
                    <tr
                      key={`${trace.recorded_at}-${index}`}
                      className={index === selectedTraceIndex ? 'selected' : ''}
                      onClick={() => setSelectedTraceIndex(index)}
                    >
                      <td>{compactDate(trace.recorded_at)}</td>
                      <td><Badge tone={latencyTone(trace.elapsed_ms)}>{fmtMs(trace.elapsed_ms)}</Badge></td>
                      <td>{trace.assistant?.source ?? 'unknown'}</td>
                      <td>{traceQueryPrimary(trace)}</td>
                      <td>{truncate(trace.message_preview ?? '', 70)}</td>
                    </tr>
                  )) : (
                    <tr>
                      <td colSpan={5}><EmptyState title="No thread traces yet" /></td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </Card>

          <Card>
            <CardHeader title="Raw selected trace JSON" />
            <div className="card-body">
              <JsonViewer value={selectedTrace ?? {}} maxHeight={420} />
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

function TriMatchRuleHits({ trace }: { trace?: LiveTrace }) {
  const evidence = traceEvidence(trace);
  return (
    <Card>
      <CardHeader title="Tri-Match rule hits" subtitle="Matched rules, layers, confidence, and shortcut eligibility from the selected live trace." />
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Rule</th>
              <th>Dimension</th>
              <th>Target</th>
              <th>Layer</th>
              <th>Conf</th>
              <th>Matched text</th>
              <th>Shortcut</th>
            </tr>
          </thead>
          <tbody>
            {evidence.length ? evidence.map((item, index) => (
              <tr key={`${String(item.rule_id)}-${index}`}>
                <td>{String(item.rule_id ?? '—')}</td>
                <td>{String(item.dimension ?? '—')}</td>
                <td>{String(item.target ?? '—')}</td>
                <td><Badge tone="blue">{String(item.layer ?? '—')}</Badge></td>
                <td>{typeof item.confidence === 'number' ? item.confidence.toFixed(3) : '—'}</td>
                <td>{String(item.matched_text ?? '—')}</td>
                <td><Badge tone={item.shortcut_eligible ? 'green' : 'purple'}>{item.shortcut_eligible ? 'yes' : 'no'}</Badge></td>
              </tr>
            )) : (
              <tr>
                <td colSpan={7}><EmptyState title="No Tri-Match evidence available" /></td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function traceEvidence(trace?: LiveTrace): Array<Record<string, unknown>> {
  const evidence = trace?.trimatch?.evidence;
  return Array.isArray(evidence) ? evidence as Array<Record<string, unknown>> : [];
}

function traceProviderVotes(trace?: LiveTrace): ProviderVote[] {
  const decisionVotes = trace?.decision?.provider_votes;
  if (Array.isArray(decisionVotes)) return decisionVotes as ProviderVote[];

  const finalVote = trace?.decision?.final_vote;
  if (finalVote && typeof finalVote === 'object') {
    return [{
      provider: 'decision_layer',
      status: 'selected',
      vote: finalVote as ProviderVote['vote']
    }];
  }

  return [];
}

function traceQueryPrimary(trace?: LiveTrace): string {
  return traceStringValue(trace, 'query_primary');
}

function traceServicePrimary(trace?: LiveTrace): string {
  return traceStringValue(trace, 'service_primary');
}

function traceFunnelStage(trace?: LiveTrace): string {
  return traceStringValue(trace, 'funnel_stage');
}

function traceStringValue(trace: LiveTrace | undefined, key: string): string {
  const intentValue = trace?.intent?.[key];
  if (typeof intentValue === 'string' && intentValue) return intentValue;

  const decisionValue = trace?.decision?.[key];
  if (typeof decisionValue === 'string' && decisionValue) return decisionValue;

  const finalVote = traceFinalVote(trace);
  const finalValue = finalVote?.[key];
  return typeof finalValue === 'string' && finalValue ? finalValue : '—';
}

function traceFinalVote(trace?: LiveTrace): Record<string, unknown> | undefined {
  const value = trace?.decision?.final_vote;
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

function traceConfidence(trace?: LiveTrace): number | undefined {
  const intentConfidence = trace?.intent?.confidence;
  if (typeof intentConfidence === 'number') return intentConfidence;

  const decisionConfidence = trace?.decision?.confidence;
  if (typeof decisionConfidence === 'number') return decisionConfidence;

  const finalConfidence = traceFinalVote(trace)?.confidence;
  return typeof finalConfidence === 'number' ? finalConfidence : undefined;
}

function formatConfidence(value?: number): string {
  return typeof value === 'number' ? value.toFixed(3) : '—';
}


function LiveTracePage({
  traces,
  refresh,
  apiEnabled,
  search,
  filters,
  setFilters
}: {
  traces: LiveTrace[];
  refresh: () => void;
  apiEnabled: boolean;
  search: string;
  filters: {
    limit: number;
    source: string;
    query_primary: string;
    service_primary: string;
    customer_id: string;
    min_latency_ms: string;
    has_forbid_markers: string;
    has_negated_terms: string;
  };
  setFilters: (filters: {
    limit: number;
    source: string;
    query_primary: string;
    service_primary: string;
    customer_id: string;
    min_latency_ms: string;
    has_forbid_markers: string;
    has_negated_terms: string;
  }) => void;
}) {
  const [selectedIndex, setSelectedIndex] = useState(0);
  const filtered = traces.filter((trace) => {
    if (!search.trim()) return true;
    const haystack = [
      trace.thread_id,
      trace.message_preview,
      trace.assistant?.source,
      trace.assistant?.preview,
      JSON.stringify(trace.runtime_atoms ?? {})
    ].join(' ').toLowerCase();
    return haystack.includes(search.toLowerCase());
  });
  const selected = filtered[selectedIndex] ?? filtered[0];

  return (
    <div className="page">
      <SectionTitle
        kicker="Live Traces"
        title="Fresh chat-turn trace stream"
        subtitle="Redacted per-turn snapshots from ChatService: intent, decision, Tri-Match, runtime atoms, source, latency, and component summary."
        right={<Button disabled={!apiEnabled} onClick={refresh}>Refresh traces</Button>}
      />
      <div className="grid cols-4">
        <MetricCard label="Loaded traces" value={filtered.length} tone="blue" />
        <MetricCard
          label="Latest latency"
          value={fmtMs(filtered[0]?.elapsed_ms)}
          tone={latencyTone(filtered[0]?.elapsed_ms)}
        />
        <MetricCard label="API mode" value={apiEnabled ? 'live' : 'disabled'} tone={apiEnabled ? 'green' : 'purple'} />
        <MetricCard label="Storage" value="JSONL" tone="cyan" />
      </div>

      <Card className="mt">
        <CardHeader
          title="Backend filters"
          subtitle="Filters are sent to /api/admin/analysis/traces/latest before the table is rendered."
          action={<Button disabled={!apiEnabled} onClick={refresh}>Apply filters</Button>}
        />
        <div className="card-body filter-grid">
          <input
            className="input"
            placeholder="source, e.g. clarification"
            value={filters.source}
            onChange={(event) => setFilters({ ...filters, source: event.target.value })}
          />
          <input
            className="input"
            placeholder="query_primary"
            value={filters.query_primary}
            onChange={(event) => setFilters({ ...filters, query_primary: event.target.value })}
          />
          <input
            className="input"
            placeholder="service_primary"
            value={filters.service_primary}
            onChange={(event) => setFilters({ ...filters, service_primary: event.target.value })}
          />
          <input
            className="input"
            placeholder="customer_id"
            value={filters.customer_id}
            onChange={(event) => setFilters({ ...filters, customer_id: event.target.value })}
          />
          <input
            className="input"
            placeholder="min_latency_ms"
            value={filters.min_latency_ms}
            onChange={(event) => setFilters({ ...filters, min_latency_ms: event.target.value })}
          />
          <select
            className="select wide"
            value={filters.has_forbid_markers}
            onChange={(event) => setFilters({ ...filters, has_forbid_markers: event.target.value })}
          >
            <option value="">forbid markers: any</option>
            <option value="true">forbid markers: yes</option>
            <option value="false">forbid markers: no</option>
          </select>
          <select
            className="select wide"
            value={filters.has_negated_terms}
            onChange={(event) => setFilters({ ...filters, has_negated_terms: event.target.value })}
          >
            <option value="">negated terms: any</option>
            <option value="true">negated terms: yes</option>
            <option value="false">negated terms: no</option>
          </select>
          <input
            className="input"
            placeholder="limit"
            type="number"
            min={1}
            max={500}
            value={filters.limit}
            onChange={(event) => setFilters({ ...filters, limit: Number(event.target.value) || 100 })}
          />
        </div>
      </Card>

      <div className="split mt">
        <Card>
          <CardHeader title="Trace rows" subtitle="Newest traces first from /api/admin/analysis/traces/latest." />
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Latency</th>
                  <th>Source</th>
                  <th>Intent</th>
                  <th>Message</th>
                </tr>
              </thead>
              <tbody>
                {filtered.length ? filtered.map((trace, index) => (
                  <tr
                    key={`${trace.thread_id}-${trace.recorded_at}-${index}`}
                    className={index === selectedIndex ? 'selected' : ''}
                    onClick={() => setSelectedIndex(index)}
                  >
                    <td>{compactDate(trace.recorded_at)}</td>
                    <td><Badge tone={latencyTone(trace.elapsed_ms)}>{fmtMs(trace.elapsed_ms)}</Badge></td>
                    <td>{trace.assistant?.source ?? 'unknown'}</td>
                    <td>{String(trace.intent?.query_primary ?? trace.decision?.query_primary ?? '—')}</td>
                    <td>{truncate(trace.message_preview ?? '', 80)}</td>
                  </tr>
                )) : (
                  <tr>
                    <td colSpan={5}>
                      <EmptyState title="No live traces loaded" body="Send a chat turn, then click Refresh traces." />
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </Card>

        <div className="stack">
          <Card>
            <CardHeader
              title="Selected trace"
              subtitle={selected?.thread_id ? `Thread ${selected.thread_id}` : 'No trace selected'}
              action={<Badge tone={latencyTone(selected?.elapsed_ms)}>{fmtMs(selected?.elapsed_ms)}</Badge>}
            />
            <div className="card-body">
              <KeyValueGrid
                items={[
                  ['Recorded', compactDate(selected?.recorded_at)],
                  ['Thread', selected?.thread_id ?? '—'],
                  ['Customer', selected?.customer_id ?? '—'],
                  ['Language', selected?.language_status ?? '—'],
                  ['Assistant source', selected?.assistant?.source ?? '—'],
                  ['Bubble count', selected?.assistant?.bubble_count ?? '—']
                ]}
              />
              <p className="assistant-text">{selected?.assistant?.preview ?? selected?.message_preview ?? 'No preview available.'}</p>
            </div>
          </Card>

          <RuntimeAtomsPanel atoms={selected?.runtime_atoms} />

          <Card>
            <CardHeader title="Decision + components" />
            <div className="card-body">
              <JsonViewer
                value={{
                  intent: selected?.intent ?? null,
                  decision: selected?.decision ?? null,
                  trimatch: selected?.trimatch ?? null,
                  components: selected?.components ?? {}
                }}
                maxHeight={360}
              />
            </div>
          </Card>

          <Card>
            <CardHeader title="Raw trace JSON" />
            <div className="card-body">
              <JsonViewer value={selected ?? {}} maxHeight={420} />
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}


function Waterfall({ report, selectedTurn, onSelect }: { report?: PerformanceReport; selectedTurn: number; onSelect: (turn: number) => void }) {
  const turn = report?.turns?.find((item) => item.turn === selectedTurn) ?? report?.turns?.[0];
  const rows = buildWaterfallRows(turn);
  return <div className="page"><SectionTitle kicker="Latency" title="Component waterfall and budget" subtitle="Shows raw event timings when available and estimated budgets otherwise." /><div className="split two-one"><Card><CardHeader title="Turn selector" /><TurnTable turns={report?.turns ?? []} selected={turn?.turn} onSelect={onSelect} /></Card><Card><CardHeader title={`Turn #${turn?.turn ?? '—'} waterfall`} /><div className="card-body"><WaterfallRows rows={rows} /></div></Card></div></div>;
}

function IntentInspector({ report, selectedTurn, onSelect }: { report?: PerformanceReport; selectedTurn: number; onSelect: (turn: number) => void }) {
  const turn = report?.turns?.find((item) => item.turn === selectedTurn) ?? report?.turns?.[0];
  return <div className="page"><SectionTitle kicker="Intent" title="Decision-layer inspector" subtitle="Final classification, audit trail, shortcuts, and provider vote comparison." /><div className="split two-one"><Card><CardHeader title="Turns" /><TurnTable turns={report?.turns ?? []} selected={turn?.turn} onSelect={onSelect} /></Card><div className="stack"><Card><CardHeader title="Final decision" /><div className="card-body"><KeyValueGrid items={[[ 'Intent', titleCase(turn?.components?.decision_layer?.query_primary) ], [ 'Service', titleCase(turn?.components?.decision_layer?.service_primary) ], [ 'Secondary', <ChipList values={turn?.components?.decision_layer?.service_secondary ?? []} /> ], [ 'Funnel', titleCase(turn?.components?.decision_layer?.funnel_stage) ], [ 'Source', turn?.components?.decision_layer?.source ?? 'unknown' ], [ 'Audit', <ChipList values={turn?.components?.decision_layer?.audit_trail ?? []} tone="purple" /> ]]} /></div></Card><ProviderVotes votes={turn?.components?.providers?.votes ?? []} /></div></div></div>;
}

function TriMatchView({ report, selectedRow, onSelect, search }: { report?: ContextCandidateReport; selectedRow: number; onSelect: (row: number) => void; search: string }) {
  const rows = (report?.rows ?? []).filter((row) => !search.trim() || `${row.text} ${row.subset}`.toLowerCase().includes(search.toLowerCase()));
  const selected = report?.rows?.find((row) => row.index === selectedRow) ?? rows[0];
  return <div className="page"><SectionTitle kicker="Tri-Match" title="Context report and evidence explorer" subtitle="Advanced v2 diagnostic evidence with active-promotion lock shown explicitly." right={<Badge tone={report?.summary?.valid_for_active_promotion ? 'green' : 'purple'}>{report?.summary?.valid_for_active_promotion ? 'promotion allowed' : 'promotion blocked'}</Badge>} /><div className="grid cols-4"><MetricCard label="Examples" value={report?.summary?.example_count ?? 0} tone="blue" /><MetricCard label="Passed" value={report?.summary?.passed_count ?? 0} tone="green" /><MetricCard label="Failed" value={report?.summary?.failed_count ?? 0} tone={(report?.summary?.failed_count ?? 0) === 0 ? 'green' : 'red'} /><MetricCard label="Active promotion" value={report?.summary?.valid_for_active_promotion ? 'Allowed' : 'Blocked'} tone="purple" /></div><div className="split mt"><Card><CardHeader title="Eval rows" /><div className="table-wrap"><table><thead><tr><th>#</th><th>Subset</th><th>Status</th><th>Text</th></tr></thead><tbody>{rows.map((row) => <tr key={row.index} onClick={() => onSelect(row.index)} className={row.index === selected?.index ? 'selected' : ''}><td>{row.index}</td><td>{row.subset}</td><td><Badge tone={row.passed ? 'green' : 'red'}>{row.passed ? 'pass' : 'fail'}</Badge></td><td>{truncate(row.text, 84)}</td></tr>)}</tbody></table></div></Card><ContextRowInspector row={selected} /></div></div>;
}

function ContextRowInspector({ row }: { row?: ContextReportRow }) {
  if (!row) return <Card className="pad"><EmptyState title="No context row selected" /></Card>;
  return <div className="stack"><Card><CardHeader title={`${row.subset} — ${row.passed ? 'PASS' : 'FAIL'}`} subtitle={row.text} /><div className="card-body"><KeyValueGrid items={[[ 'Query', String(row.actual.query_primary ?? '—') ], [ 'Service', String(row.actual.service_primary ?? '—') ], [ 'Secondary', <ChipList values={(row.actual.service_secondary as string[]) ?? []} /> ], [ 'Context', <ChipList values={(row.actual.context as string[]) ?? []} tone="purple" /> ], [ 'Forbid', <ChipList values={(row.actual.forbid as string[]) ?? []} tone="red" /> ], [ 'Negated', <ChipList values={(row.actual.negated_services as string[]) ?? []} tone="red" /> ]]} /></div></Card><Card><CardHeader title="Evidence" /><div className="table-wrap"><table><thead><tr><th>Rule</th><th>Dimension</th><th>Target</th><th>Layer</th><th>Text</th><th>Conf</th></tr></thead><tbody>{row.evidence.map((item, index) => <tr key={`${item.rule_id}-${index}`}><td>{item.rule_id}</td><td>{item.dimension}</td><td>{item.target}</td><td><Badge tone="blue">{item.layer}</Badge></td><td>{item.matched_text}</td><td>{typeof item.confidence === 'number' ? item.confidence.toFixed(3) : '—'}</td></tr>)}</tbody></table></div></Card><Card><CardHeader title="Expected vs actual" /><div className="card-body"><JsonViewer value={{ expected: row.expected, actual: row.actual, checks: row.checks }} /></div></Card></div>;
}

function RuleApproval({ rules, selected, setSelected, updateCandidate, syncRules, apiEnabled }: { rules: RuleCandidate[]; selected: string; setSelected: (id: string) => void; updateCandidate: (id: string, status: RuleCandidateStatus, note?: string) => void; syncRules: () => void; apiEnabled: boolean }) {
  const rule = rules.find((item) => item.id === selected) ?? rules[0];
  return <div className="page"><SectionTitle kicker="Governance" title="LLM rule approval workflow" subtitle="Human-in-the-loop rule mutation with API-backed writes when enabled." right={<Button onClick={syncRules} disabled={!apiEnabled}>Sync API candidates</Button>} /><div className="split two-one"><Card><CardHeader title="Candidate queue" /><div className="table-wrap"><table><thead><tr><th>ID</th><th>Target</th><th>Status</th><th>Risk</th></tr></thead><tbody>{rules.map((item) => <tr key={item.id} onClick={() => setSelected(item.id)} className={item.id === rule?.id ? 'selected' : ''}><td>{item.id}</td><td>{titleCase(item.target)}</td><td><Badge tone={statusToneForRule(item.status)}>{titleCase(item.status)}</Badge></td><td>{item.collision_warnings.map((warning) => <Badge key={warning.message} tone={warning.severity}>{warning.severity}</Badge>)}</td></tr>)}</tbody></table></div></Card><RuleDetail rule={rule} updateCandidate={updateCandidate} apiEnabled={apiEnabled} /></div></div>;
}

function RuleDetail({ rule, updateCandidate, apiEnabled }: { rule?: RuleCandidate; updateCandidate: (id: string, status: RuleCandidateStatus, note?: string) => void; apiEnabled: boolean }) {
  if (!rule) return <Card className="pad"><EmptyState title="No rule selected" /></Card>;
  return <div className="stack"><Card><CardHeader title={rule.title} subtitle={rule.source_message} action={<Badge tone={statusToneForRule(rule.status)}>{titleCase(rule.status)}</Badge>} /><div className="card-body"><KeyValueGrid items={[[ 'Dimension', rule.dimension ], [ 'Target', titleCase(rule.target) ], [ 'Layer', rule.layer ], [ 'Confidence', rule.confidence.toFixed(3) ], [ 'Shortcut', rule.shortcut_allowed ? 'yes' : 'no' ], [ 'Eval', `${rule.eval_result.passed} passed / ${rule.eval_result.failed} failed` ]]} /><p>{rule.reason}</p><ChipList values={rule.phrases ?? (rule.regex ? [rule.regex] : rule.pattern ?? [])} tone="cyan" /></div></Card><Card><CardHeader title="Collision warnings" /><div className="card-body stack-small">{rule.collision_warnings.map((warning) => <div className="warning" key={warning.message}><Badge tone={warning.severity}>{warning.severity}</Badge><span>{warning.message}</span></div>)}</div></Card><Card><CardHeader title="Review actions" subtitle={apiEnabled ? 'Writes are sent to admin backend and audit log.' : 'Local prototype state only.'} /><div className="card-body action-row"><Button variant="success" onClick={() => updateCandidate(rule.id, 'approved_for_staging', 'Approved for staging review.')}>Approve</Button><Button variant="ghost" onClick={() => updateCandidate(rule.id, 'changes_requested', 'Needs narrower context or more eval coverage.')}>Request changes</Button><Button variant="danger" onClick={() => updateCandidate(rule.id, 'rejected', 'Rejected due to collision risk.')}>Reject</Button><Button variant="ghost" onClick={() => updateCandidate(rule.id, 'blocked', 'Blocked until verifier gates pass.')}>Block</Button></div></Card></div>;
}

function RulesArmyActivation({ api, apiEnabled, withApi, addReport }: { api: AdminApiClient; apiEnabled: boolean; withApi: (label: string, action: () => Promise<void>) => Promise<void>; addReport: (report: LoadedReport) => void }) {
  const [preflight, setPreflight] = useState<RulesArmyPreflight | null>(null);
  const [result, setResult] = useState<ActivationResult | null>(null);
  const [confirm, setConfirm] = useState('');
  const [force, setForce] = useState(false);
  const [mode, setMode] = useState<'active' | 'shadow'>('active');
  const [backupDir, setBackupDir] = useState('');
  return <div className="page"><SectionTitle kicker="Activation" title="Rules Army v2 activation center" subtitle="Production mutation controls with preflight, backup, audit log, verifier gate, forced override, and rollback." right={<Badge tone="red">high risk</Badge>} />
    <div className="grid cols-3"><MetricCard label="Candidate exists" value={preflight?.candidate_exists ? 'yes' : 'unknown'} tone={preflight?.candidate_exists ? 'green' : 'yellow'} /><MetricCard label="Verifier" value={preflight?.verifier_valid ? 'valid' : 'not valid'} tone={preflight?.verifier_valid ? 'green' : 'red'} /><MetricCard label="Context report" value={preflight?.context_report_valid ? 'valid' : 'unknown'} tone={preflight?.context_report_valid ? 'green' : 'purple'} /></div>
    <div className="grid cols-2 mt"><Card><CardHeader title="1. Preflight" subtitle="Runs candidate inventory and verifier/context checks." action={<Button disabled={!apiEnabled} onClick={() => withApi('Rules Army preflight', async () => setPreflight(await api.rulesArmyPreflight()))}>Run preflight</Button>} /><div className="card-body"><JsonViewer value={preflight ?? { status: 'not_run' }} /></div></Card>
    <Card><CardHeader title="2. Activate or shadow-stage" subtitle="Active mode replaces data/trimatch/rules after backup. Shadow mode records the candidate without replacing active rules." /><div className="card-body stack-small"><label className="field-label">Confirm phrase</label><input className="input" value={confirm} onChange={(event) => setConfirm(event.target.value)} placeholder="I_UNDERSTAND_THIS_PROMOTES_RULES_ARMY_V2" /><label className="field-label">Mode</label><select className="select wide" value={mode} onChange={(event) => setMode(event.target.value as 'active' | 'shadow')}><option value="active">active replacement</option><option value="shadow">shadow record only</option></select><label className="check"><input type="checkbox" checked={force} onChange={(event) => setForce(event.target.checked)} /> Force activation even if verifier is red</label><Button variant="danger" disabled={!apiEnabled || !confirm} onClick={() => withApi('Rules Army activation', async () => setResult(await api.activateRulesArmyV2({ confirm_phrase: confirm, force, mode })))}>Activate with backup</Button><JsonViewer value={result ?? { status: 'not_activated' }} maxHeight={260} /></div></Card>
    <Card><CardHeader title="3. Run fresh context eval" subtitle="Regenerate the diagnostic context report after mutation." action={<Button disabled={!apiEnabled} onClick={() => withApi('Run context eval', async () => addReport(await api.runContextEval()))}>Run eval</Button>} /><div className="card-body"><p>This endpoint reruns <code>run_trimatch_context_candidate_report.py</code> and imports the JSON result into the console.</p></div></Card>
    <Card><CardHeader title="4. Rollback" subtitle="Restore a backup created during active replacement." /><div className="card-body stack-small"><input className="input" value={backupDir} onChange={(event) => setBackupDir(event.target.value)} placeholder="data/trimatch/backups/2026..._rules" /><Button variant="danger" disabled={!apiEnabled || !backupDir} onClick={() => withApi('Rollback rules', async () => setResult(await api.rollbackRules(backupDir)))}>Rollback from backup</Button></div></Card></div>
  </div>;
}

function EvalRegression({ reports, context, api, apiEnabled, withApi, addReport }: { reports: Array<LoadedReport & { data: PerformanceReport }>; context?: ContextCandidateReport; api: AdminApiClient; apiEnabled: boolean; withApi: (label: string, action: () => Promise<void>) => Promise<void>; addReport: (report: LoadedReport) => void }) {
  const before = reports[1]?.data;
  const after = reports[0]?.data;
  const diffs = buildDiffs(before, after);
  return <div className="page"><SectionTitle kicker="Regression" title="Eval and regression console" subtitle="Compare reports and trigger backend eval runs." right={<Button disabled={!apiEnabled} onClick={() => withApi('Run context eval', async () => addReport(await api.runContextEval()))}>Run backend eval</Button>} /><div className="grid cols-3"><MetricCard label="Reports" value={reports.length} tone="blue" /><MetricCard label="Context diagnostic" value={`${context?.summary.passed_count ?? 0}/${context?.summary.example_count ?? 0}`} tone={(context?.summary.failed_count ?? 1) === 0 ? 'green' : 'yellow'} /><MetricCard label="Promotion" value={context?.summary.valid_for_active_promotion ? 'Allowed' : 'Blocked'} tone="purple" /></div><Card className="mt"><CardHeader title="Report diff" /><div className="table-wrap"><table><thead><tr><th>Metric</th><th>Before</th><th>After</th><th>Severity</th></tr></thead><tbody>{diffs.map((diff) => <tr key={diff.id}><td>{diff.label}</td><td>{String(diff.before ?? '—')}</td><td>{String(diff.after ?? '—')}</td><td><Badge tone={diff.severity}>{diff.severity}</Badge></td></tr>)}</tbody></table></div></Card></div>;
}

function ProviderHealth({ report }: { report?: PerformanceReport }) { const health = report?.component_summary?.provider_health; return <div className="page"><SectionTitle kicker="Providers" title="Provider and shortcut health" subtitle="Claude/OpenAI/DeepSeek status, deterministic shortcuts, timeouts, and circuit breakers." /><div className="grid cols-4"><MetricCard label="Timeouts" value={fmtNumber(health?.timeout_count)} tone={(health?.timeout_count ?? 0) === 0 ? 'green' : 'yellow'} /><MetricCard label="Circuit open" value={fmtNumber(health?.circuit_open_count)} tone={(health?.circuit_open_count ?? 0) === 0 ? 'green' : 'red'} /><MetricCard label="Failed" value={fmtNumber(health?.failed_count)} tone={(health?.failed_count ?? 0) === 0 ? 'green' : 'red'} /><MetricCard label="Usable votes" value={fmtNumber(health?.usable_vote_count)} tone="blue" /></div><div className="grid cols-2 mt"><Card><CardHeader title="Provider counts" /><div className="card-body"><DistributionBars data={health?.provider_counts} /></div></Card><Card><CardHeader title="Provider status counts" /><div className="card-body"><DistributionBars data={health?.provider_status_counts} /></div></Card></div></div>; }
function SafetyQuality({ report, context }: { report?: PerformanceReport; context?: ContextCandidateReport }) { const quality = report?.component_summary?.response_quality ?? {}; const safetyRows = context?.rows?.map((row) => ({ subset: row.subset, context: row.actual.context, forbid: row.actual.forbid, text: row.text })) ?? []; return <div className="page"><SectionTitle kicker="Safety" title="Response quality and safety flags" subtitle="Warnings, invented-link risks, pricing/document gates, and context markers." /><div className="grid cols-4">{objectEntries(quality).map(([key, value]) => <MetricCard key={key} label={titleCase(key)} value={String(value)} tone={Number(value) === 0 ? 'green' : 'yellow'} />)}</div><Card className="mt"><CardHeader title="Context safety rows" /><div className="table-wrap"><table><thead><tr><th>Subset</th><th>Context</th><th>Forbid</th><th>Message</th></tr></thead><tbody>{safetyRows.map((row, index) => <tr key={index}><td>{row.subset}</td><td><ChipList values={row.context as string[]} tone="purple" /></td><td><ChipList values={row.forbid as string[]} tone="red" /></td><td>{truncate(row.text, 90)}</td></tr>)}</tbody></table></div></Card></div>; }
function PricingTrace({ report }: { report?: PerformanceReport }) { return <TraceEventPage kicker="Pricing" title="Pricing and timeline trace inspector" subtitle="Quote explanation, timeline multipliers, audit trace, and missing inputs." events={collectEvents(report, ['pricing', 'quote', 'timeline', 'schedule'])} empty="No pricing events found in this report yet." />; }
function RagTrace({ report }: { report?: PerformanceReport }) { return <TraceEventPage kicker="RAG" title="RAG trace inspector" subtitle="Retrieval, grounding, source chunks, verifier outcomes, and fast-path responses." events={collectEvents(report, ['rag', 'retrieval', 'chunk', 'verifier', 'grounding'])} empty="No RAG events found in this report yet." />; }
function TraceEventPage({ kicker, title, subtitle, events, empty }: { kicker: string; title: string; subtitle: string; events: Array<Record<string, unknown>>; empty: string }) { return <div className="page"><SectionTitle kicker={kicker} title={title} subtitle={subtitle} /><Card><CardHeader title={`${events.length} matching events`} /><div className="card-body">{events.length ? <JsonViewer value={events} /> : <EmptyState title={empty} body="The page is ready for richer backend traces when available." />}</div></Card></div>; }

function Settings({ reports, onLoad, setReports, rules, apiConfig, setApiConfig, connectApi, importLiveReports, apiHealth }: { reports: LoadedReport[]; onLoad: (report: LoadedReport) => void; setReports: (reports: LoadedReport[]) => void; rules: RuleCandidate[]; apiConfig: AdminApiConfig; setApiConfig: (config: AdminApiConfig) => void; connectApi: () => void; importLiveReports: () => void; apiHealth: AdminHealth | null }) {
  return <div className="page"><SectionTitle kicker="Settings" title="Admin API, import/export, and backend contracts" subtitle="Configure production backend connectivity and keep local report workflows available." /><div className="grid cols-2"><Card><CardHeader title="Admin API connection" subtitle="Requires backend route module and BOOKCRAFT_ADMIN_ANALYSIS_TOKEN." action={<Badge tone={apiHealth?.ok ? 'green' : 'neutral'}>{apiHealth?.ok ? 'connected' : 'not connected'}</Badge>} /><div className="card-body stack-small"><label className="check"><input type="checkbox" checked={apiConfig.enabled} onChange={(event) => setApiConfig({ ...apiConfig, enabled: event.target.checked })} /> Enable live admin API</label><label className="field-label">Base URL</label><input className="input" value={apiConfig.baseUrl} onChange={(event) => setApiConfig({ ...apiConfig, baseUrl: event.target.value })} /><label className="field-label">Bearer token</label><input className="input" type="password" value={apiConfig.token} onChange={(event) => setApiConfig({ ...apiConfig, token: event.target.value })} /><div className="action-row"><Button onClick={connectApi}>Test connection</Button><Button variant="ghost" onClick={importLiveReports}>Import live reports</Button><Button variant="ghost" onClick={() => setApiConfig(defaultApiConfig)}>Reset</Button></div><JsonViewer value={apiHealth ?? { ok: false }} maxHeight={220} /></div></Card><Card><CardHeader title="Import local reports" /><div className="card-body"><ReportUploader onLoad={onLoad} /><div className="action-row mt"><Button variant="ghost" onClick={() => setReports(sampleReports)}>Reset samples</Button><Button variant="ghost" onClick={() => downloadJson('analysis-console-session.json', { reports, rules, api: { ...apiConfig, token: apiConfig.token ? '***' : '' } })}>Export session</Button></div></div></Card></div><Card className="mt"><CardHeader title="Required backend endpoints" /><div className="card-body"><pre className="contract">GET  /api/admin/analysis/health{`\n`}GET  /api/admin/analysis/reports/production{`\n`}GET  /api/admin/analysis/reports/trimatch-context{`\n`}POST /api/admin/analysis/evals/context-candidate/run{`\n`}GET  /api/admin/analysis/rules/candidates{`\n`}POST /api/admin/analysis/rules/candidates{`\n`}PATCH /api/admin/analysis/rules/candidates/:id{`\n`}POST /api/admin/analysis/rules-army-v2/preflight{`\n`}POST /api/admin/analysis/rules-army-v2/activate{`\n`}POST /api/admin/analysis/rules-army-v2/rollback</pre></div></Card></div>;
}

function filterTurns(report: PerformanceReport | undefined, search: string): ReportTurn[] { const turns = report?.turns ?? []; if (!search.trim()) return turns; const needle = search.toLowerCase(); return turns.filter((turn) => `${turn.message} ${getAssistantText(turn)} ${JSON.stringify(turn.components ?? {})}`.toLowerCase().includes(needle)); }
function buildWaterfallRows(turn?: ReportTurn) { const events = turn?.raw_events ?? []; const rows = events.map((event) => ({ label: String(event.component ?? event.event ?? 'event'), ms: typeof event.elapsed_ms === 'number' ? event.elapsed_ms : 0, tone: latencyTone(typeof event.elapsed_ms === 'number' ? event.elapsed_ms : 0) })); return rows.length ? rows : [{ label: 'Total turn', ms: turn?.elapsed_ms ?? 0, tone: latencyTone(turn?.elapsed_ms) }]; }
function statusTone(status?: string): Tone { const normalized = (status ?? '').toLowerCase(); if (['succeeded','success','passed','valid','healthy'].includes(normalized)) return 'green'; if (['timed_out','timeout','warning','degraded'].includes(normalized)) return 'yellow'; if (['failed','error','invalid','blocked','critical'].includes(normalized)) return 'red'; if (['shortcut','shadow','candidate'].includes(normalized)) return 'purple'; return 'neutral'; }
function statusToneForRule(status: RuleCandidateStatus): Tone { if (['approved_for_staging','promoted_to_staged'].includes(status)) return 'green'; if (['changes_requested','draft','needs_review'].includes(status)) return 'yellow'; if (['rejected','blocked'].includes(status)) return 'red'; return 'neutral'; }
function collectEvents(report: PerformanceReport | undefined, needles: string[]) { const rows: Array<Record<string, unknown>> = []; for (const turn of report?.turns ?? []) { for (const event of turn.raw_events ?? []) { const blob = JSON.stringify(event).toLowerCase(); if (needles.some((needle) => blob.includes(needle))) rows.push({ turn: turn.turn, message: turn.message, ...event }); } } return rows; }
function buildDiffs(before?: PerformanceReport, after?: PerformanceReport) { return [{ id: 'p95', label: 'p95 latency', before: before?.summary.p95_latency_ms ?? null, after: after?.summary.p95_latency_ms ?? null, severity: latencyTone(after?.summary.p95_latency_ms) }, { id: 'critical', label: 'critical issues', before: before?.summary.critical_issue_count ?? null, after: after?.summary.critical_issue_count ?? null, severity: (after?.summary.critical_issue_count ?? 0) === 0 ? 'green' as Tone : 'red' as Tone }, { id: 'success', label: 'success count', before: before?.summary.success_count ?? null, after: after?.summary.success_count ?? null, severity: 'blue' as Tone }]; }
