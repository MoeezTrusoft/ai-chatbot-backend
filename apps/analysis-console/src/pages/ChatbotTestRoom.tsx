import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { Badge, Button, ChipList, EmptyState, JsonViewer, KeyValueGrid } from '../components/ui';
import type { AdminApiConfig, ChatTurnResponse, LiveTrace, ProviderVote, RuntimeAtoms, Tone } from '../types/reports';
import { AdminApiClient } from '../lib/apiClient';
import { compactDate, fmtMs, latencyTone, statusTone, titleCase, truncate } from '../lib/format';
import {
  loadChatRoomThreads,
  saveChatRoomThreads,
  type ChatBubbleMessage,
  type ChatRoomThread,
  type ChatRoomTurn
} from '../lib/chatTestRoomStorage';
import './ChatbotTestRoom.css';

const TEST_PROMPTS = [
  { label: 'Pricing', message: 'How much does ghostwriting cost for a 50,000 word fantasy novel?' },
  { label: 'Consultation', message: 'Please schedule a consultation on May 20, 2026 at 11:00 AM Houston time. My name is Maya Author and my email is maya@example.com.' },
  { label: 'NDA', message: 'I need an NDA before I share my manuscript.' },
  { label: 'Agreement', message: 'I am ready to start. Please prepare the service agreement.' },
  { label: 'Portfolio', message: 'Show me cover design portfolio samples for cozy mystery.' },
  { label: 'RAG Service', message: 'Tell me about BookCraft ghostwriting services.' },
  { label: 'Negation', message: "I don't need ghostwriting, I need editing for a finished manuscript." },
  { label: 'Multi-service', message: 'I need editing, formatting, cover design, publishing setup, and launch marketing for my manuscript.' }
];

type ChatbotTestRoomProps = {
  api: AdminApiClient;
  apiConfig: AdminApiConfig;
  setApiConfig: (config: AdminApiConfig) => void;
};

export function ChatbotTestRoom({ api, apiConfig, setApiConfig }: ChatbotTestRoomProps) {
  const [threads, setThreads] = useState<ChatRoomThread[]>(() => loadChatRoomThreads());
  const [activeThreadId, setActiveThreadId] = useState<string | null>(() => loadChatRoomThreads()[0]?.id ?? null);
  const [selectedTurnId, setSelectedTurnId] = useState<string | null>(null);
  const [composer, setComposer] = useState('');
  const [search, setSearch] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [threadsOpen, setThreadsOpen] = useState(false);
  const chatEndRef = useRef<HTMLDivElement | null>(null);

  const activeThread = threads.find((thread) => thread.id === activeThreadId);
  const selectedTurn = activeThread?.turns.find((turn) => turn.id === selectedTurnId) ?? activeThread?.turns.at(-1);
  const latestTurn = activeThread?.turns.at(-1);

  useEffect(() => saveChatRoomThreads(threads), [threads]);
  useEffect(() => chatEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }), [activeThread?.turns.length, busy]);

  const groupedThreads = useMemo(() => {
    const filtered = threads
      .filter((thread) => {
        const haystack = [
          thread.title,
          thread.threadId,
          thread.customerId,
          thread.turns.at(-1)?.userMessage.text,
          thread.turns.at(-1)?.response.intent?.query_primary
        ].join(' ').toLowerCase();
        return haystack.includes(search.toLowerCase());
      })
      .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime());

    return {
      Today: filtered.filter((thread) => ageBucket(thread.updatedAt) === 'Today'),
      Yesterday: filtered.filter((thread) => ageBucket(thread.updatedAt) === 'Yesterday'),
      Older: filtered.filter((thread) => ageBucket(thread.updatedAt) === 'Older')
    };
  }, [threads, search]);

  function startNewThread() {
    setActiveThreadId(null);
    setSelectedTurnId(null);
    setComposer('');
    setError('');
    setThreadsOpen(false);
  }

  function selectThread(id: string) {
    setActiveThreadId(id);
    const thread = threads.find((item) => item.id === id);
    setSelectedTurnId(thread?.turns.at(-1)?.id ?? null);
    setError('');
    setThreadsOpen(false);
  }

  async function sendMessage() {
    const trimmed = composer.trim();
    if (!trimmed || busy) return;

    setBusy(true);
    setError('');
    const started = performance.now();

    try {
      const response = await api.sendChatTurn({
        message: trimmed,
        thread_id: activeThread?.threadId,
        customer_id: apiConfig.customerId || undefined,
        chatToken: apiConfig.chatToken || undefined
      });
      const elapsedMs = Math.round(performance.now() - started);
      const { trace, warning } = await fetchLatestTrace(response.thread_id);
      const now = new Date().toISOString();
      const localThreadId = activeThread?.id ?? makeId('local-thread');
      const turn = buildTurn(trimmed, response, trace, warning, now, elapsedMs);

      setThreads((current) => {
        const existing = current.find((thread) => thread.id === localThreadId);
        if (existing) {
          return current.map((thread) => thread.id === localThreadId
            ? {
              ...thread,
              threadId: response.thread_id,
              customerId: apiConfig.customerId || thread.customerId,
              title: thread.title || titleFromTurn(turn),
              updatedAt: now,
              turns: [...thread.turns, turn]
            }
            : thread);
        }

        const nextThread: ChatRoomThread = {
          id: localThreadId,
          threadId: response.thread_id,
          customerId: apiConfig.customerId || undefined,
          title: titleFromTurn(turn),
          createdAt: now,
          updatedAt: now,
          turns: [turn]
        };
        return [nextThread, ...current];
      });

      setActiveThreadId(localThreadId);
      setSelectedTurnId(turn.id);
      setComposer('');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function fetchLatestTrace(threadId: string): Promise<{ trace?: LiveTrace; warning?: string }> {
    if (!apiConfig.token) return { warning: 'trace unavailable: admin token not configured' };
    try {
      const traceResponse = await api.threadLiveTraces(threadId, { limit: 8 });
      return { trace: traceResponse.traces[0], warning: traceResponse.traces[0] ? undefined : 'trace unavailable: no matching trace yet' };
    } catch (err) {
      return { warning: `trace unavailable: ${err instanceof Error ? err.message : String(err)}` };
    }
  }

  function onComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void sendMessage();
    }
  }

  return (
    <div className="chat-room-page">
      <div className="chat-room-mobile-actions">
        <Button variant="ghost" onClick={() => setThreadsOpen(!threadsOpen)}>Threads</Button>
        <Button onClick={startNewThread}>+ New Thread</Button>
      </div>

      <aside className={`chat-room-threads ${threadsOpen ? 'open' : ''}`}>
        <Button onClick={startNewThread}>+ New Thread</Button>
        <input className="input" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search threads" />
        <div className="chat-room-config">
          <label className="field-label">Chat JWT</label>
          <textarea className="textarea mono" rows={3} value={apiConfig.chatToken ?? ''} onChange={(event) => setApiConfig({ ...apiConfig, chatToken: event.target.value })} placeholder="Paste generated CHAT_JWT" />
          <label className="field-label">Customer ID</label>
          <input className="input mono" value={apiConfig.customerId ?? ''} onChange={(event) => setApiConfig({ ...apiConfig, customerId: event.target.value })} placeholder="SMOKE_CUSTOMER_ID" />
        </div>
        <div className="chat-thread-groups">
          {Object.entries(groupedThreads).map(([label, items]) => (
            <div className="chat-thread-group" key={label}>
              <h3>{label}</h3>
              {items.length ? items.map((thread) => (
                <ThreadButton key={thread.id} thread={thread} active={thread.id === activeThreadId} onClick={() => selectThread(thread.id)} />
              )) : <p>No threads</p>}
            </div>
          ))}
        </div>
      </aside>

      <section className="chat-room-center">
        <header className="chat-room-header">
          <div>
            <h2>Chatbot Test Room</h2>
            <p>{activeThread?.threadId ? `Thread ${activeThread.threadId}` : 'New unsaved thread'} • {apiConfig.customerId || 'No customer ID'} • {apiConfig.enabled ? 'API configured' : 'API disabled'}</p>
          </div>
          <div className="chat-room-header-badges">
            <Badge tone={apiConfig.enabled ? 'green' : 'purple'}>{apiConfig.enabled ? 'live api' : 'local mode'}</Badge>
            <Badge tone={latencyTone(latestTurn?.elapsedMs)}>{fmtMs(latestTurn?.elapsedMs)}</Badge>
            {selectedTurn?.traceWarning ? <Badge tone="yellow">trace unavailable</Badge> : null}
          </div>
        </header>

        <div className="chat-room-stream">
          {activeThread?.turns.length ? activeThread.turns.map((turn) => (
            <TurnBubbles key={turn.id} turn={turn} selected={turn.id === selectedTurn?.id} onSelect={() => setSelectedTurnId(turn.id)} />
          )) : (
            <EmptyState title="No messages yet" body="Start with a prompt chip or type a customer message." />
          )}
          {busy ? <div className="typing-bubble">Sending turn...</div> : null}
          <div ref={chatEndRef} />
        </div>

        <footer className="chat-room-composer">
          <div className="prompt-chip-row">
            {TEST_PROMPTS.map((prompt) => (
              <button key={prompt.label} type="button" onClick={() => setComposer(prompt.message)}>{prompt.label}</button>
            ))}
          </div>
          {error ? <div className="chat-room-error">{error}</div> : null}
          <div className="composer-row">
            <textarea
              className="textarea"
              rows={3}
              value={composer}
              onChange={(event) => setComposer(event.target.value)}
              onKeyDown={onComposerKeyDown}
              placeholder="Type a customer message..."
            />
            <Button disabled={busy || !composer.trim() || !apiConfig.chatToken} onClick={sendMessage}>{busy ? 'Sending...' : 'Send'}</Button>
          </div>
        </footer>
      </section>

      <TurnDiagnosticsPanel turn={selectedTurn} />
    </div>
  );
}

function ThreadButton({ thread, active, onClick }: { thread: ChatRoomThread; active: boolean; onClick: () => void }) {
  const latest = thread.turns.at(-1);
  const intent = latest?.response.intent?.query_primary ?? traceStringValue(latest?.trace, 'query_primary');
  const confidence = latest?.response.intent?.confidence ?? traceConfidence(latest?.trace);
  return (
    <button className={`chat-thread-item ${active ? 'active' : ''}`} onClick={onClick}>
      <span className="thread-title">{thread.title}</span>
      <span className="thread-preview">{truncate(latest?.userMessage.text ?? 'No message yet', 64)}</span>
      <span className="thread-meta">
        <Badge tone="blue">{intent || 'unknown'}</Badge>
        <Badge tone={confidenceTone(confidence)}>{formatConfidence(confidence)}</Badge>
        <small>{compactDate(thread.updatedAt)}</small>
      </span>
    </button>
  );
}

function TurnBubbles({ turn, selected, onSelect }: { turn: ChatRoomTurn; selected: boolean; onSelect: () => void }) {
  const chips = <BubbleChips turn={turn} />;
  return (
    <div className={`chat-turn ${selected ? 'selected' : ''}`} onClick={onSelect}>
      <button className="chat-bubble customer" type="button">
        <p>{turn.userMessage.text}</p>
        {chips}
      </button>
      {turn.assistantMessages.map((message) => (
        <button className="chat-bubble assistant" key={message.id} type="button">
          <p>{message.text}</p>
          {chips}
        </button>
      ))}
      {turn.traceWarning ? <Badge tone="yellow">{turn.traceWarning}</Badge> : null}
    </div>
  );
}

function BubbleChips({ turn }: { turn: ChatRoomTurn }) {
  const intent = turn.response.intent?.query_primary ?? traceStringValue(turn.trace, 'query_primary');
  const source = assistantSource(turn);
  const confidence = turn.response.intent?.confidence ?? traceConfidence(turn.trace);
  return (
    <span className="bubble-chips">
      <Badge tone="blue">{intent || 'unknown'}</Badge>
      <Badge tone={confidenceTone(confidence)}>{formatConfidence(confidence)}</Badge>
      <Badge tone={latencyTone(turn.elapsedMs)}>{fmtMs(turn.elapsedMs)}</Badge>
      <Badge tone="purple">{source}</Badge>
    </span>
  );
}

function TurnDiagnosticsPanel({ turn }: { turn?: ChatRoomTurn }) {
  if (!turn) {
    return (
      <aside className="turn-diagnostics">
        <h2>Turn Inspector</h2>
        <EmptyState title="No turn selected" body="Send or click a message to inspect intent, trace, atoms, and action plan." />
      </aside>
    );
  }

  const intent = turn.response.intent;
  const trace = turn.trace;
  const atoms = trace?.runtime_atoms;
  const actionPlan = objectRecord(trace?.action_plan);
  const result = objectRecord(actionPlan?.result);

  return (
    <aside className="turn-diagnostics">
      <div className="inspector-title">
        <h2>Turn Inspector</h2>
        <Badge tone={turn.traceWarning ? 'yellow' : 'green'}>{turn.traceWarning ? 'partial' : 'trace linked'}</Badge>
      </div>

      <InspectorSection title="Intent">
        <KeyValueGrid items={[
          ['Query primary', intent?.query_primary ?? traceStringValue(trace, 'query_primary')],
          ['Query secondary', <ChipList values={toStringArray(intent?.query_secondary)} tone="blue" />],
          ['Service primary', intent?.service_primary ?? traceStringValue(trace, 'service_primary')],
          ['Service secondary', <ChipList values={toStringArray(intent?.service_secondary).length ? toStringArray(intent?.service_secondary) : traceSecondaryServices(trace)} tone="cyan" />],
          ['Funnel stage', intent?.funnel_stage ?? traceStringValue(trace, 'funnel_stage')],
          ['Needs clarification', String(intent?.needs_clarification ?? traceNeedsClarification(trace) ?? '—')],
          ['Confidence', formatConfidence(intent?.confidence ?? traceConfidence(trace))],
          ['Language', turn.response.language_status ?? trace?.language_status ?? '—']
        ]} />
        {intent?.rationale ? <p className="diagnostic-note">{intent.rationale}</p> : null}
        <ChipList values={toStringArray(intent?.evidence)} tone="purple" empty="No evidence" />
      </InspectorSection>

      <InspectorSection title="Detectors">
        <ProviderVotes votes={traceProviderVotes(trace)} />
        <KeyValueGrid items={[
          ['Assistant source', assistantSource(turn)],
          ['Tri-Match vote', traceStringValue(trace, 'service_primary')],
          ['Deterministic cues', <ChipList values={toStringArray(atoms?.query_cues)} tone="yellow" />]
        ]} />
      </InspectorSection>

      <InspectorSection title="Timing">
        <KeyValueGrid items={[
          ['Browser total', fmtMs(turn.elapsedMs)],
          ['Trace total', fmtMs(trace?.elapsed_ms)],
          ['Intent', componentMs(trace, ['intent_ms', 'intent_elapsed_ms', 'decision_ms'])],
          ['Preprocessor', componentMs(trace, ['preprocessor_ms', 'runtime_atoms_ms'])],
          ['RAG', componentMs(trace, ['rag_ms', 'retrieval_ms'])],
          ['Response', componentMs(trace, ['response_ms', 'assistant_ms'])]
        ]} />
      </InspectorSection>

      <InspectorSection title="Runtime Atoms">
        <AtomGrid atoms={atoms} />
      </InspectorSection>

      <InspectorSection title="Action Plan">
        <KeyValueGrid items={[
          ['Type', stringValue(actionPlan?.action_type)],
          ['Status', stringValue(actionPlan?.status)],
          ['Missing slots', <ChipList values={toStringArray(actionPlan?.missing_slots)} tone="yellow" />],
          ['Success', stringValue(result?.success)],
          ['Error code', stringValue(result?.error_code)],
          ['Safe summary', stringValue(result?.customer_safe_summary)]
        ]} />
        <JsonViewer value={result?.payload ?? actionPlan?.payload ?? { payload: 'unavailable' }} maxHeight={180} />
      </InspectorSection>

      <InspectorSection title="Tri-Match Evidence">
        <JsonViewer value={traceEvidence(trace)} maxHeight={220} />
      </InspectorSection>

      <InspectorSection title="Raw JSON">
        <details open>
          <summary>ChatTurnResponse</summary>
          <JsonViewer value={turn.response} maxHeight={260} />
        </details>
        <details>
          <summary>Trace payload</summary>
          <JsonViewer value={trace ?? { warning: turn.traceWarning ?? 'Trace unavailable' }} maxHeight={360} />
        </details>
      </InspectorSection>
    </aside>
  );
}

function InspectorSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="inspector-section">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

function ProviderVotes({ votes }: { votes: ProviderVote[] }) {
  if (!votes.length) return <EmptyState title="No provider votes" body="The turn may have used a deterministic shortcut or the trace has not landed yet." />;
  return (
    <div className="provider-vote-list">
      {votes.map((vote, index) => (
        <div className="provider-vote" key={`${vote.provider ?? 'provider'}-${index}`}>
          <div>
            <strong>{prettyProviderName(vote.provider ?? 'unknown')}</strong>
            <Badge tone={statusTone(vote.status)}>{vote.status ?? 'unknown'}</Badge>
          </div>
          <p>{titleCase(vote.vote?.query_primary ?? undefined)} • {titleCase(vote.vote?.service_primary ?? undefined)} • {formatConfidence(vote.vote?.confidence ?? undefined)}</p>
          {vote.error ? <small>{vote.error}</small> : null}
        </div>
      ))}
    </div>
  );
}

function AtomGrid({ atoms }: { atoms?: RuntimeAtoms }) {
  const items: Array<[string, unknown, Tone]> = [
    ['services', atoms?.services, 'cyan'],
    ['negated_services', atoms?.negated_services, 'red'],
    ['word_counts', atoms?.word_counts, 'blue'],
    ['page_counts', atoms?.page_counts, 'blue'],
    ['emails', atoms?.emails, 'green'],
    ['phones', atoms?.phones, 'green'],
    ['urls', atoms?.urls, 'purple'],
    ['forbid_markers', atoms?.forbid_markers, 'red'],
    ['context_markers', atoms?.context_markers, 'purple']
  ];
  return (
    <div className="diagnostic-atom-grid">
      {items.map(([label, value, tone]) => (
        <div key={label}>
          <span>{titleCase(label)}</span>
          <ChipList values={toStringArray(value)} tone={tone} />
        </div>
      ))}
    </div>
  );
}

function buildTurn(message: string, response: ChatTurnResponse, trace: LiveTrace | undefined, traceWarning: string | undefined, now: string, elapsedMs: number): ChatRoomTurn {
  const turnId = makeId('turn');
  const userMessage: ChatBubbleMessage = {
    id: makeId('customer'),
    role: 'customer',
    text: message,
    createdAt: now
  };
  const assistantMessages = (response.bubbles.length ? response.bubbles : [{ text: trace?.assistant?.preview ?? 'No assistant response text returned.' }]).map((bubble, index) => ({
    id: makeId('assistant'),
    role: 'assistant' as const,
    text: bubble.text,
    createdAt: now,
    bubbleIndex: bubble.bubble_index ?? index
  }));
  return { id: turnId, userMessage, assistantMessages, response, trace, traceWarning, sentAt: now, elapsedMs };
}

function titleFromTurn(turn: ChatRoomTurn): string {
  const intent = turn.response.intent?.query_primary ?? traceStringValue(turn.trace, 'query_primary');
  const basis = intent && intent !== '—' ? titleCase(intent) : turn.userMessage.text;
  return truncate(basis, 42);
}

function ageBucket(value: string): 'Today' | 'Yesterday' | 'Older' {
  const date = new Date(value);
  const now = new Date();
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const startYesterday = startToday - 24 * 60 * 60 * 1000;
  const time = date.getTime();
  if (time >= startToday) return 'Today';
  if (time >= startYesterday) return 'Yesterday';
  return 'Older';
}

function traceProviderVotes(trace?: LiveTrace): ProviderVote[] {
  const topLevel = trace?.provider_votes;
  if (Array.isArray(topLevel)) return topLevel as ProviderVote[];
  const decisionVotes = trace?.decision?.provider_votes;
  if (Array.isArray(decisionVotes)) return decisionVotes as ProviderVote[];
  const finalVote = objectRecord(trace?.decision?.final_vote);
  return finalVote ? [{ provider: 'decision_layer', status: 'selected', vote: finalVote as ProviderVote['vote'] }] : [];
}

function traceEvidence(trace?: LiveTrace): unknown[] {
  const trimatch = objectRecord(trace?.trimatch);
  const evidence = trimatch?.evidence;
  return Array.isArray(evidence) ? evidence : [];
}

function traceSecondaryServices(trace?: LiveTrace): string[] {
  const fromIntent = objectRecord(trace?.intent)?.service_secondary;
  if (Array.isArray(fromIntent)) return fromIntent.map(String);
  const fromFinal = objectRecord(trace?.decision?.final_vote)?.service_secondary;
  return Array.isArray(fromFinal) ? fromFinal.map(String) : [];
}

function traceNeedsClarification(trace?: LiveTrace): boolean | undefined {
  const fromIntent = objectRecord(trace?.intent)?.needs_clarification;
  if (typeof fromIntent === 'boolean') return fromIntent;
  const fromFinal = objectRecord(trace?.decision?.final_vote)?.needs_clarification;
  return typeof fromFinal === 'boolean' ? fromFinal : undefined;
}

function traceStringValue(trace: LiveTrace | undefined, key: string): string {
  const fromIntent = objectRecord(trace?.intent)?.[key];
  if (typeof fromIntent === 'string' && fromIntent) return fromIntent;
  const fromDecision = objectRecord(trace?.decision)?.[key];
  if (typeof fromDecision === 'string' && fromDecision) return fromDecision;
  const fromFinal = objectRecord(trace?.decision?.final_vote)?.[key];
  return typeof fromFinal === 'string' && fromFinal ? fromFinal : '—';
}

function traceConfidence(trace?: LiveTrace): number | undefined {
  const fromIntent = objectRecord(trace?.intent)?.confidence;
  if (typeof fromIntent === 'number') return fromIntent;
  const fromDecision = objectRecord(trace?.decision)?.confidence;
  if (typeof fromDecision === 'number') return fromDecision;
  const fromFinal = objectRecord(trace?.decision?.final_vote)?.confidence;
  return typeof fromFinal === 'number' ? fromFinal : undefined;
}

function componentMs(trace: LiveTrace | undefined, keys: string[]): string {
  const components = objectRecord(trace?.components);
  for (const key of keys) {
    const value = components?.[key] ?? trace?.[key];
    if (typeof value === 'number') return fmtMs(value);
  }
  return '—';
}

function assistantSource(turn: ChatRoomTurn): string {
  const source = turn.trace?.assistant?.source;
  return typeof source === 'string' && source ? source : 'response';
}

function confidenceTone(value?: number): Tone {
  if (typeof value !== 'number') return 'neutral';
  if (value >= 0.9) return 'green';
  if (value >= 0.7) return 'cyan';
  if (value >= 0.45) return 'yellow';
  return 'red';
}

function prettyProviderName(provider: string): string {
  const normalized = provider.toLowerCase();
  if (normalized.includes('openai')) return 'OpenAI';
  if (normalized.includes('claude') || normalized.includes('haiku')) return 'Claude';
  if (normalized.includes('trimatch')) return 'Tri-Match';
  if (normalized.includes('deterministic')) return 'Deterministic';
  return provider;
}

function formatConfidence(value?: number | null): string {
  return typeof value === 'number' ? value.toFixed(3) : '—';
}

function toStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map(String).filter(Boolean);
}

function stringValue(value: unknown): string {
  if (value === undefined || value === null || value === '') return '—';
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}

function objectRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}

function makeId(prefix: string): string {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}
