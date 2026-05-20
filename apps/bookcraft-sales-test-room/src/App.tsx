import { useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { checkChatHealth, ensureChatSession, fetchThreadTraces, sendChatTurn } from './api';
import {
  getOrCreateBrowserSession,
  loadConfig,
  loadThreads,
  resetBrowserSession,
  saveBrowserSession,
  saveConfig,
  saveThreads,
  uid
} from './storage';
import type { ApiConfig, BrowserSession, LiveTrace, TestThread, ThreadTurn } from './types';

const PROMPTS = [
  {
    label: 'Ghostwriting help',
    message:
      "I have my manuscript progress to 3 chapters, I have my story but I don't have time to write it. Can you help me with it?"
  },
  {
    label: 'Pricing check',
    message: 'Can you give me a quote for ghostwriting a 50000 word fantasy novel?'
  },
  {
    label: 'Not a price quote',
    message:
      "I can't quote a fixed one but approximately it will be 500 pages or 150K words, but now I just have about 120 pages done."
  },
  {
    label: 'Consultation',
    message:
      'Please schedule a consultation on May 20, 2026 at 11:00 AM Houston time. My name is Maya Author and my email is maya@example.com.'
  },
  { label: 'NDA', message: 'I need an NDA before I share my manuscript.' },
  { label: 'Agreement', message: 'I am ready to start. Please prepare the service agreement.' },
  { label: 'Portfolio', message: 'Show me cover design portfolio samples for cozy mystery.' },
  { label: 'Negation', message: "I don't need ghostwriting, I need editing for a finished manuscript." },
  {
    label: 'Multi-service',
    message: 'I need editing, formatting, cover design, publishing setup, and launch marketing for my manuscript.'
  }
];

const INTENT_LABELS: Record<string, string> = {
  pricing_question: 'Asking about price',
  service_question: 'Asking about a service',
  consultation_request: 'Wants a consultation',
  nda_request: 'Needs confidentiality/NDA',
  agreement_request: 'Ready for agreement',
  portfolio_request: 'Wants samples',
  manuscript_status_update: 'Sharing manuscript progress',
  timeline_question: 'Asking about timeline',
  greeting: 'Greeting',
  unclear: 'Needs clarification'
};

const SERVICE_LABELS: Record<string, string> = {
  ghostwriting: 'Ghostwriting',
  editing_proofreading: 'Editing & proofreading',
  cover_design_illustration: 'Cover design / illustration',
  interior_formatting: 'Interior formatting',
  audiobook_production: 'Audiobook production',
  publishing_distribution: 'Publishing & distribution',
  marketing_promotion: 'Marketing & promotion',
  author_website: 'Author website',
  video_trailer: 'Video trailer'
};

const STAGE_LABELS: Record<string, string> = {
  new: 'New lead',
  exploring: 'Exploring',
  service_discovery: 'Understanding needs',
  quote_requested: 'Quote requested',
  nda_requested: 'NDA requested',
  agreement_requested: 'Agreement requested',
  ready_to_start: 'Ready to start'
};

export function App() {
  const [config, setConfig] = useState<ApiConfig>(() => loadConfig());
  const [session, setSession] = useState<BrowserSession>(() => getOrCreateBrowserSession());
  const [threads, setThreads] = useState<TestThread[]>(() => loadThreads());
  const [activeThreadId, setActiveThreadId] = useState(() => loadThreads()[0]?.id ?? '');
  const [selectedTurnId, setSelectedTurnId] = useState('');
  const [message, setMessage] = useState(PROMPTS[0].message);
  const [search, setSearch] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [health, setHealth] = useState<'unknown' | 'ok' | 'bad'>('unknown');
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [sessionMessage, setSessionMessage] = useState('Creating customer session automatically...');

  useEffect(() => saveThreads(threads), [threads]);
  useEffect(() => saveConfig(config), [config]);
  useEffect(() => saveBrowserSession(session), [session]);
  useEffect(() => {
    let cancelled = false;
    async function loadInitialSession() {
      try {
        const next = await ensureChatSession(config, session);
        if (cancelled) return;
        setSession(next);
        setSessionMessage('Session ready.');
      } catch (caught) {
        if (cancelled) return;
        setSessionMessage(caught instanceof Error ? caught.message : String(caught));
      }
    }
    void loadInitialSession();
    return () => {
      cancelled = true;
    };
  }, []);

  const activeThread = threads.find((thread) => thread.id === activeThreadId);
  const threadInputDisabled = Boolean(activeThread?.inputDisabled);
  const selectedTurn = activeThread?.turns.find((turn) => turn.id === selectedTurnId) ?? activeThread?.turns.at(-1);

  const filteredThreads = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return threads;
    return threads.filter((thread) => `${thread.title} ${thread.threadId ?? ''} ${preview(thread)}`.toLowerCase().includes(query));
  }, [threads, search]);

  function updateConfig(patch: Partial<ApiConfig>) {
    setConfig((current) => ({ ...current, ...patch }));
  }

  function newThread() {
    const now = new Date().toISOString();
    const thread: TestThread = {
      id: uid('thread'),
      title: 'New customer test',
      customerId: session.customerId,
      createdAt: now,
      updatedAt: now,
      turns: []
    };
    setThreads((current) => [thread, ...current]);
    setActiveThreadId(thread.id);
    setSelectedTurnId('');
    setMessage('');
    setError('');
  }

  async function runHealthCheck() {
    setHealth('unknown');
    const ok = await checkChatHealth(config);
    setHealth(ok ? 'ok' : 'bad');
  }

  async function prepareSession(): Promise<BrowserSession> {
    const next = await ensureChatSession(config, session);
    setSession(next);

    if (next.source === 'session-service') {
      setSessionMessage('Session ready.');
    } else {
      setSessionMessage('Customer ID was created automatically in this browser.');
    }

    return next;
  }

  async function send() {
    const trimmed = message.trim();
    if (!trimmed || busy || threadInputDisabled) return;

    let thread = activeThread;
    if (!thread) {
      const now = new Date().toISOString();
      thread = {
        id: uid('thread'),
        title: titleFromMessage(trimmed),
        customerId: session.customerId,
        createdAt: now,
        updatedAt: now,
        turns: []
      };
      setThreads((current) => [thread as TestThread, ...current]);
      setActiveThreadId(thread.id);
    }

    setBusy(true);
    setError('');
    const started = performance.now();

    try {
      const activeSession = await prepareSession();
      const response = await sendChatTurn(config, activeSession, trimmed, thread.threadId);
      const elapsedMs = Math.round(performance.now() - started);
      let trace: LiveTrace | undefined;
      let traceStatus: ThreadTurn['traceStatus'] = 'not-configured';

      try {
        const traceResponse = await fetchThreadTraces(config, response.thread_id, 20);
        trace = traceResponse.traces.at(-1) ?? traceResponse.traces[0];
        traceStatus = trace ? 'available' : 'unavailable';
      } catch {
        trace = undefined;
        traceStatus = 'not-configured';
      }

      const turn: ThreadTurn = {
        id: uid('turn'),
        customerText: trimmed,
        assistantText: response.bubbles.map((bubble) => bubble.text).filter(Boolean).join('\n\n') || 'No response text returned.',
        createdAt: new Date().toISOString(),
        elapsedMs,
        response,
        trace,
        traceStatus
      };
      if (response.system_message) {
        turn.assistantText = response.system_message;
      }

      setThreads((current) =>
        current.map((item) =>
          item.id === thread?.id
            ? {
                ...item,
                title: item.turns.length ? item.title : titleFromMessage(trimmed),
                threadId: response.thread_id,
                customerId: activeSession.customerId,
                updatedAt: turn.createdAt,
                turns: [...item.turns, turn],
                inputDisabled:
                  item.inputDisabled ||
                  Boolean(response.blocked || response.input_disabled),
                blockedSystemMessage: response.system_message || item.blockedSystemMessage
              }
            : item
        )
      );

      setSelectedTurnId(turn.id);
      setMessage('');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  function resetVisitor() {
    const next = resetBrowserSession();
    setSession(next);
    setSessionMessage('A new customer UUID was created. Session will refresh on the next message.');
  }

  function clearActiveThread() {
    if (!activeThread) return;
    const nextThreads = threads.filter((thread) => thread.id !== activeThread.id);
    setThreads(nextThreads);
    setActiveThreadId(nextThreads[0]?.id ?? '');
    setSelectedTurnId('');
  }

  return (
    <div className="app-shell">
      <header className="hero">
        <div>
          <p className="eyebrow">BookCraft Sales QA</p>
          <h1>Chatbot Test Room</h1>
          <p className="hero-copy">
            A clean WhatsApp-style tester for sales teams. The app creates the customer ID automatically and requests a chat session without asking users to paste tokens.
          </p>
        </div>
        <div className="hero-actions">
          <button className="secondary" onClick={runHealthCheck}>Check API</button>
          <button className="secondary" onClick={() => setAdvancedOpen((value) => !value)}>
            {advancedOpen ? 'Hide Developer Settings' : 'Developer Settings'}
          </button>
          <button className="secondary" onClick={() => void prepareSession()}>Create Session</button>
          <span className={`status-pill ${health === 'ok' ? 'ok' : health === 'bad' ? 'bad' : ''}`}>{health === 'ok' ? 'API OK' : health === 'bad' ? 'API issue' : 'API not checked'}</span>
        </div>
      </header>

      {advancedOpen ? (
        <section className="config-card advanced">
          <label>
            Backend URL
            <input value={config.baseUrl} onChange={(event) => updateConfig({ baseUrl: event.target.value })} />
          </label>
          <label>
            Session service URL
            <input value={config.sessionServiceUrl} onChange={(event) => updateConfig({ sessionServiceUrl: event.target.value })} />
          </label>
          <label>
            Current customer UUID
            <input value={session.customerId} readOnly />
          </label>
          <button className="secondary" onClick={resetVisitor}>Reset session</button>
        </section>
      ) : null}

      <main className="workspace">
        <aside className="panel threads-panel">
          <div className="panel-head">
            <div>
              <p className="eyebrow small">Threads</p>
              <h2>Customer chats</h2>
            </div>
            <button className="primary" onClick={newThread}>+ New</button>
          </div>
          <section className="thread-session-card">
            <b>Auto customer session</b>
            <p>{sessionMessage}</p>
            <div className="session-pills">
              <span className="mini-pill">Customer: {shortId(session.customerId)}</span>
              <span className={`mini-pill ${session.chatToken ? 'ok' : 'warn'}`}>{session.chatToken ? 'Session ready' : 'Starting session'}</span>
              <span className="mini-pill">Source: {session.source}</span>
            </div>
          </section>
          <input className="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search chats..." />
          <div className="thread-list">
            {filteredThreads.length ? filteredThreads.map((thread) => (
              <button
                key={thread.id}
                className={`thread-item ${thread.id === activeThreadId ? 'active' : ''}`}
                onClick={() => {
                  setActiveThreadId(thread.id);
                  setSelectedTurnId(thread.turns.at(-1)?.id ?? '');
                }}
              >
                <span className="thread-title">{thread.title}</span>
                <span className="thread-preview">{preview(thread)}</span>
                <span className="thread-meta">{thread.turns.length} turns • {timeAgo(thread.updatedAt)}</span>
              </button>
            )) : <Empty title="No test chats" text="Click New to start a customer conversation." />}
          </div>
        </aside>

        <section className="panel chat-panel">
          <div className="chat-head">
            <div className="avatar">BC</div>
            <div>
              <h2>{activeThread?.title ?? 'New customer chat'}</h2>
              <p>{activeThread?.threadId ? `Thread ${activeThread.threadId}` : 'No backend thread yet'} • {shortId(session.customerId)}</p>
            </div>
            <button className="secondary danger" onClick={clearActiveThread} disabled={!activeThread}>Clear</button>
          </div>

          <div className="chat-body">
            {activeThread?.turns.length ? activeThread.turns.map((turn) => (
              <div key={turn.id}>
                <Bubble role="customer" text={turn.customerText} onClick={() => setSelectedTurnId(turn.id)} selected={selectedTurn?.id === turn.id} />
                <Bubble role="assistant" text={turn.assistantText} onClick={() => setSelectedTurnId(turn.id)} selected={selectedTurn?.id === turn.id} turn={turn} />
              </div>
            )) : (
              <div className="empty-chat">
                <Empty title="Ready to test" text="Choose a scenario below or type a customer message. The right panel explains the result in sales-friendly language." />
              </div>
            )}
          </div>

          <div className="prompt-row">
            {PROMPTS.map((prompt) => <button key={prompt.label} onClick={() => setMessage(prompt.message)}>{prompt.label}</button>)}
          </div>
          <div className="composer">
            <textarea
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault();
                  void send();
                }
              }}
              placeholder="Type a customer message..."
              disabled={threadInputDisabled}
            />
            <button className="send" disabled={busy || !message.trim() || threadInputDisabled} onClick={() => void send()}>{busy ? 'Sending...' : 'Send'}</button>
          </div>
          {threadInputDisabled ? <div className="error-box">{activeThread?.blockedSystemMessage || 'This thread is blocked.'}</div> : null}
          {error ? <div className="error-box">{error}</div> : null}
        </section>

        <aside className="panel inspector-panel">
          <div className="panel-head vertical">
            <p className="eyebrow small">Sales review</p>
            <h2>Bot Assessment</h2>
            <p>Plain-English view for non-technical sales review.</p>
          </div>
          <Inspector turn={selectedTurn} />
        </aside>
      </main>
    </div>
  );
}

function Bubble({ role, text, selected, onClick, turn }: { role: 'customer' | 'assistant'; text: string; selected: boolean; onClick: () => void; turn?: ThreadTurn }) {
  const intent = turn ? getIntent(turn) : {};
  return (
    <div className={`bubble-row ${role}`}>
      <button className={`bubble ${selected ? 'selected' : ''}`} onClick={onClick}>
        <span>{text}</span>
        {role === 'assistant' && turn ? (
          <div className="bubble-tags">
            <em>{plainIntent(intent.query)}</em>
            <em>{plainConfidence(intent.confidence)}</em>
            {turn.elapsedMs ? <em>{turn.elapsedMs}ms</em> : null}
            {turn.traceStatus === 'available' ? <em>Trace ready</em> : null}
          </div>
        ) : null}
      </button>
    </div>
  );
}

function Inspector({ turn }: { turn?: ThreadTurn }) {
  if (!turn) return <div className="inspector-body"><Empty title="No message selected" text="Send a message or click a bot reply to review it." /></div>;

  const intent = getIntent(turn);
  const service = getService(turn);
  const action = getAction(turn);
  const atoms = getRuntimeAtoms(turn);
  const source = getSource(turn);

  return (
    <div className="inspector-body">
      <ScoreCard title="What the bot understood">
        <Row label="Customer need" value={plainIntent(intent.query)} />
        <Row label="Service" value={plainService(service)} />
        <Row label="Sales stage" value={plainStage(intent.stage)} />
        <Row label="Bot source" value={plainSource(source)} />
      </ScoreCard>

      <ScoreCard title="Confidence">
        <div className="progress"><span style={{ width: `${Math.round((intent.confidence ?? 0) * 100)}%` }} /></div>
        <div className="tags"><b>{plainConfidence(intent.confidence)}</b>{typeof intent.confidence === 'number' ? <b>{Math.round(intent.confidence * 100)}%</b> : null}</div>
      </ScoreCard>

      <ScoreCard title="Action taken">
        <Row label="Action" value={plainAction(action.actionType)} />
        <Row label="Status" value={plainStatus(action.status)} />
        <Row label="Missing info" value={plainList(action.missingSlots)} />
        <Row label="Trace" value={plainTraceStatus(turn.traceStatus)} />
        <Row
          label="Blocked input"
          value={turn.response?.blocked || turn.response?.input_disabled ? 'Yes' : 'No'}
        />
      </ScoreCard>

      <ScoreCard title="Detected details">
        <div className="tags wrap">
          {plainAtomBadges(atoms).map((item) => <b key={item}>{item}</b>)}
          {!plainAtomBadges(atoms).length ? <b>No extra details found</b> : null}
        </div>
      </ScoreCard>

      <ScoreCard title="Sales reviewer notes">
        <Row label="Good sign" value={reviewGoodSign(intent.query, service)} />
        <Row label="Watch for" value={reviewWatchFor(action.missingSlots)} />
        <Row label="Next step" value={reviewNextStep(action.actionType, action.missingSlots)} />
      </ScoreCard>

      <details className="raw-card"><summary>Raw response</summary><pre>{JSON.stringify(turn.response ?? {}, null, 2)}</pre></details>
      <details className="raw-card"><summary>Raw trace</summary><pre>{JSON.stringify(turn.trace ?? {}, null, 2)}</pre></details>
    </div>
  );
}

function ScoreCard({ title, children }: { title: string; children: ReactNode }) {
  return <section className="score-card"><h3>{title}</h3>{children}</section>;
}

function Row({ label, value }: { label: string; value: string }) {
  return <div className="review-row"><span>{label}</span><strong>{value}</strong></div>;
}

function Empty({ title, text }: { title: string; text: string }) {
  return <div className="empty"><strong>{title}</strong><p>{text}</p></div>;
}

function preview(thread: TestThread): string {
  const latest = thread.turns.at(-1);
  if (!latest) return 'No messages yet';
  const text = latest.assistantText || latest.customerText;
  return text.length > 88 ? `${text.slice(0, 88)}...` : text;
}

function titleFromMessage(value: string): string {
  const clean = value.replace(/\s+/g, ' ').trim();
  return clean.length > 46 ? `${clean.slice(0, 46)}...` : clean || 'New customer test';
}

function timeAgo(value: string): string {
  const minutes = Math.floor(Math.max(0, Date.now() - new Date(value).getTime()) / 60000);
  if (minutes < 1) return 'now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function safeRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function getIntent(turn: ThreadTurn): { query?: string; stage?: string; confidence?: number } {
  const responseIntent = safeRecord(turn.response?.intent);
  const traceIntent = safeRecord(turn.trace?.intent);
  const components = safeRecord(turn.trace?.components);
  const decision = safeRecord(components.decision_layer);
  return {
    query: stringValue(responseIntent.query_primary) || stringValue(traceIntent.query_primary) || stringValue(decision.query_primary),
    stage: stringValue(responseIntent.funnel_stage) || stringValue(traceIntent.funnel_stage) || stringValue(decision.funnel_stage),
    confidence: numberValue(responseIntent.confidence) ?? numberValue(traceIntent.confidence) ?? numberValue(decision.confidence)
  };
}

function getService(turn: ThreadTurn): string | undefined {
  const responseIntent = safeRecord(turn.response?.intent);
  const traceIntent = safeRecord(turn.trace?.intent);
  const components = safeRecord(turn.trace?.components);
  const decision = safeRecord(components.decision_layer);
  return stringValue(responseIntent.service_primary) || stringValue(traceIntent.service_primary) || stringValue(decision.service_primary);
}

function getAction(turn: ThreadTurn): { actionType?: string; status?: string; missingSlots: string[] } {
  const action = safeRecord(turn.trace?.action_plan);
  return {
    actionType: stringValue(action.action_type),
    status: stringValue(action.status),
    missingSlots: arrayOfStrings(action.missing_slots)
  };
}

function getSource(turn: ThreadTurn): string | undefined {
  const assistant = safeRecord(turn.trace?.assistant);
  const components = safeRecord(turn.trace?.components);
  const decision = safeRecord(components.decision_layer);
  return stringValue(assistant.source) || stringValue(decision.source);
}

function getRuntimeAtoms(turn: ThreadTurn): Record<string, unknown> {
  const traceAtoms = safeRecord(turn.trace?.runtime_atoms);
  if (Object.keys(traceAtoms).length) return traceAtoms;
  const components = safeRecord(turn.trace?.components);
  return safeRecord(components.runtime_atoms);
}

function stringValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined;
}

function numberValue(value: unknown): number | undefined {
  return typeof value === 'number' ? value : undefined;
}

function arrayOfStrings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

function numberArray(value: unknown): number[] {
  return Array.isArray(value) ? value.filter((item): item is number => typeof item === 'number') : [];
}

function plainIntent(value?: string): string {
  if (!value) return 'Not clear yet';
  return INTENT_LABELS[value] ?? humanize(value);
}

function plainService(value?: string): string {
  if (!value) return 'No specific service yet';
  return SERVICE_LABELS[value] ?? humanize(value);
}

function plainStage(value?: string): string {
  if (!value) return 'Not decided yet';
  return STAGE_LABELS[value] ?? humanize(value);
}

function plainSource(value?: string): string {
  if (!value) return 'Not available';
  if (value.includes('sonnet') || value.includes('claude')) return 'Main AI response';
  if (value.includes('pricing')) return 'Pricing engine';
  if (value.includes('deterministic')) return 'Rule-based detector';
  return humanize(value);
}

function plainAction(value?: string): string {
  if (!value) return 'No backend action needed';
  return humanize(value);
}

function plainStatus(value?: string): string {
  if (!value) return 'No action status';
  return humanize(value);
}

function plainTraceStatus(value?: ThreadTurn['traceStatus']): string {
  if (value === 'available') return 'Detailed review available';
  if (value === 'unavailable') return 'Trace not found';
  return 'Basic review from chat response';
}

function plainConfidence(value?: number): string {
  if (value === undefined) return 'Confidence not shown';
  if (value >= 0.9) return 'High confidence';
  if (value >= 0.7) return 'Medium confidence';
  return 'Low confidence';
}

function plainList(values: string[]): string {
  if (!values.length) return 'Nothing missing';
  return values.map(humanize).join(', ');
}

function plainAtomBadges(atoms: Record<string, unknown>): string[] {
  const labels: string[] = [];
  for (const service of arrayOfStrings(atoms.services)) labels.push(`Service: ${plainService(service)}`);
  for (const service of arrayOfStrings(atoms.negated_services)) labels.push(`Not needed: ${plainService(service)}`);
  for (const count of numberArray(atoms.word_counts)) labels.push(`${count.toLocaleString()} words`);
  for (const count of numberArray(atoms.page_counts)) labels.push(`${count.toLocaleString()} pages`);
  for (const email of arrayOfStrings(atoms.emails)) labels.push(`Email found: ${email}`);
  for (const phone of arrayOfStrings(atoms.phones)) labels.push(`Phone found: ${phone}`);
  return labels.slice(0, 12);
}

function reviewGoodSign(query?: string, service?: string): string {
  if (query === 'pricing_question') return 'The bot noticed a price/quote request.';
  if (query === 'consultation_request') return 'The bot noticed the customer wants to talk to a consultant.';
  if (service) return `The bot detected ${plainService(service)} as the likely service.`;
  return 'The bot responded without forcing a sales action.';
}

function reviewWatchFor(missingSlots: string[]): string {
  if (!missingSlots.length) return 'No obvious missing information from this turn.';
  return `Sales may still need: ${plainList(missingSlots)}.`;
}

function reviewNextStep(action?: string, missingSlots: string[] = []): string {
  if (action === 'price_quote' && missingSlots.length) return 'Ask the customer for the missing quote details.';
  if (action === 'schedule_consultation') return 'Confirm the appointment details and booking result.';
  if (action === 'nda') return 'Collect the customer details required for NDA preparation.';
  if (action === 'agreement') return 'Confirm approved quote and agreement details.';
  return 'Review the reply quality and continue the conversation.';
}

function humanize(value: string): string {
  return value.replace(/_/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase());
}

function shortId(value: string): string {
  if (value.length <= 18) return value;
  return `${value.slice(0, 8)}...${value.slice(-6)}`;
}
