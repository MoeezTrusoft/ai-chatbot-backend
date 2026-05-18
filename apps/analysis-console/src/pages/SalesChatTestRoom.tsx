import { useEffect, useMemo, useState } from 'react';
import type { AdminApiConfig, ChatTurnResponse, LiveTrace } from '../types/reports';
import { AdminApiClient } from '../lib/apiClient';
import {
  SalesChatThread,
  SalesChatTurn,
  loadSalesChatThreads,
  makeLocalId,
  saveSalesChatThreads
} from '../lib/chatTestRoomStorage';
import './SalesChatTestRoom.css';

type Props = {
  api: AdminApiClient;
  apiConfig: AdminApiConfig;
  setApiConfig: (config: AdminApiConfig) => void;
};

type PromptPreset = {
  label: string;
  message: string;
};

const PROMPTS: PromptPreset[] = [
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
    label: 'Consultation',
    message:
      'Please schedule a consultation on May 20, 2026 at 11:00 AM Houston time. My name is Maya Author and my email is maya@example.com.'
  },
  {
    label: 'NDA',
    message: 'I need an NDA before I share my manuscript.'
  },
  {
    label: 'Agreement',
    message: 'I am ready to start. Please prepare the service agreement.'
  },
  {
    label: 'Portfolio',
    message: 'Show me cover design portfolio samples for cozy mystery.'
  },
  {
    label: 'Non-price quote',
    message:
      "I can't quote a fixed one but approximately it will be 500 pages or 150K words, but now I just have about 120 pages done."
  },
  {
    label: 'Not ghostwriting',
    message: "I don't need ghostwriting, I need editing for a finished manuscript."
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

export function SalesChatTestRoom({ api, apiConfig, setApiConfig }: Props) {
  const [threads, setThreads] = useState<SalesChatThread[]>(() => loadSalesChatThreads());
  const [activeId, setActiveId] = useState(() => threads[0]?.id ?? '');
  const [selectedTurnId, setSelectedTurnId] = useState('');
  const [composer, setComposer] = useState(
    "I have my manuscript progress to 3 chapters, I have my story but I don't have time to write it. Can you help me with it?"
  );
  const [search, setSearch] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => saveSalesChatThreads(threads), [threads]);

  const activeThread = threads.find((thread) => thread.id === activeId);
  const selectedTurn =
    activeThread?.turns.find((turn) => turn.id === selectedTurnId) ??
    activeThread?.turns.at(-1);

  const filteredThreads = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return threads;

    return threads.filter((thread) =>
      `${thread.title} ${thread.threadId ?? ''} ${latestPreview(thread)}`.toLowerCase().includes(query)
    );
  }, [threads, search]);

  function updateConfig(partial: Partial<AdminApiConfig>) {
    setApiConfig({
      ...apiConfig,
      ...partial
    });
  }

  function startNewThread() {
    const now = new Date().toISOString();
    const thread: SalesChatThread = {
      id: makeLocalId('sales-thread'),
      title: 'New sales test',
      customerId: apiConfig.customerId,
      createdAt: now,
      updatedAt: now,
      turns: []
    };

    setThreads((current) => [thread, ...current]);
    setActiveId(thread.id);
    setSelectedTurnId('');
    setComposer('');
    setError('');
  }

  async function sendMessage() {
    const trimmed = composer.trim();
    if (!trimmed || busy) return;

    const thread = activeThread ?? createTransientThread(apiConfig.customerId);
    if (!activeThread) {
      setThreads((current) => [thread, ...current]);
      setActiveId(thread.id);
    }

    setBusy(true);
    setError('');

    const started = performance.now();

    try {
      const response = await api.sendChatTurn({
        message: trimmed,
        thread_id: thread.threadId,
        customer_id: apiConfig.customerId || undefined,
        chatToken: apiConfig.chatToken || undefined
      });

      const elapsedMs = Math.round(performance.now() - started);
      const assistantText = assistantTextFromResponse(response);
      const trace = await tryFetchTrace(api, response.thread_id);

      const turn: SalesChatTurn = {
        id: makeLocalId('turn'),
        customerText: trimmed,
        assistantText,
        createdAt: new Date().toISOString(),
        elapsedMs,
        response,
        trace
      };

      setThreads((current) =>
        current.map((item) => {
          if (item.id !== thread.id) return item;

          return {
            ...item,
            threadId: response.thread_id,
            customerId: apiConfig.customerId || item.customerId,
            title: titleFromMessage(trimmed),
            updatedAt: turn.createdAt,
            turns: [...item.turns, turn]
          };
        })
      );

      setSelectedTurnId(turn.id);
      setComposer('');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  function deleteThread(threadId: string) {
    setThreads((current) => current.filter((thread) => thread.id !== threadId));
    if (activeId === threadId) {
      const next = threads.find((thread) => thread.id !== threadId);
      setActiveId(next?.id ?? '');
      setSelectedTurnId('');
    }
  }

  return (
    <div className="sales-room">
      <div className="sales-room-shell">
        <aside className="sales-panel sales-thread-panel">
          <div className="sales-panel-head">
            <p className="sales-kicker">Sales testing</p>
            <h2 className="sales-title">Chatbot Test Room</h2>
            <p className="sales-subtitle">
              Start a fresh customer-style chat, test the bot, and review plain-English performance signals.
            </p>
            <button className="sales-new-thread" onClick={startNewThread}>
              + New Thread
            </button>
          </div>

          <div className="sales-thread-search">
            <input
              className="sales-input"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search test chats..."
            />
          </div>

          <div className="sales-thread-list">
            <div className="sales-thread-group">Recent tests</div>
            {filteredThreads.length ? (
              filteredThreads.map((thread) => (
                <button
                  className={`sales-thread-item ${thread.id === activeId ? 'active' : ''}`}
                  key={thread.id}
                  onClick={() => {
                    setActiveId(thread.id);
                    setSelectedTurnId(thread.turns.at(-1)?.id ?? '');
                  }}
                >
                  <div className="sales-thread-top">
                    <div className="sales-thread-title">{thread.title}</div>
                    <div className="sales-thread-time">{timeAgo(thread.updatedAt)}</div>
                  </div>
                  <div className="sales-thread-preview">{latestPreview(thread)}</div>
                  <div className="sales-bubble-meta">
                    <span className="sales-badge info">{thread.turns.length} turns</span>
                    {thread.threadId ? <span className="sales-badge good">saved thread</span> : null}
                  </div>
                </button>
              ))
            ) : (
              <div className="sales-empty-card">
                <h3>No test chats yet</h3>
                <p>Click New Thread to begin a customer-style conversation.</p>
              </div>
            )}
          </div>
        </aside>

        <section className="sales-panel sales-chat-panel">
          <header className="sales-chat-header">
            <div className="sales-chat-identity">
              <div className="sales-avatar">BC</div>
              <div>
                <h3>{activeThread?.title ?? 'New customer chat'}</h3>
                <p>
                  {activeThread?.threadId
                    ? `Thread ${activeThread.threadId}`
                    : 'No backend thread yet'}{' '}
                  • {apiConfig.customerId || 'No customer ID'}
                </p>
              </div>
            </div>
            <div className="sales-badges">
              <span className={`sales-badge ${apiConfig.enabled ? 'good' : 'warn'}`}>
                {apiConfig.enabled ? 'API connected' : 'API not enabled'}
              </span>
              {selectedTurn?.elapsedMs ? (
                <span className="sales-badge info">{selectedTurn.elapsedMs}ms</span>
              ) : null}
              {activeThread ? (
                <button className="sales-prompt" onClick={() => deleteThread(activeThread.id)}>
                  Clear chat
                </button>
              ) : null}
            </div>
          </header>

          <main className="sales-chat-body">
            {activeThread?.turns.length ? (
              activeThread.turns.map((turn) => (
                <div key={turn.id}>
                  <Bubble
                    role="customer"
                    text={turn.customerText}
                    selected={selectedTurn?.id === turn.id}
                    onClick={() => setSelectedTurnId(turn.id)}
                    turn={turn}
                  />
                  <Bubble
                    role="assistant"
                    text={turn.assistantText}
                    selected={selectedTurn?.id === turn.id}
                    onClick={() => setSelectedTurnId(turn.id)}
                    turn={turn}
                  />
                </div>
              ))
            ) : (
              <div className="sales-empty-chat">
                <div className="sales-empty-card">
                  <h3>Ready for a new test conversation</h3>
                  <p>
                    Use the prompt chips below or type your own customer message. The right panel will explain
                    what the bot understood in sales-friendly language.
                  </p>
                </div>
              </div>
            )}
          </main>

          <footer>
            <div className="sales-prompt-strip">
              {PROMPTS.map((prompt) => (
                <button
                  className="sales-prompt"
                  key={prompt.label}
                  onClick={() => setComposer(prompt.message)}
                >
                  {prompt.label}
                </button>
              ))}
            </div>

            <div className="sales-settings">
              <label>
                API URL
                <input
                  className="sales-input"
                  value={apiConfig.baseUrl}
                  onChange={(event) => updateConfig({ baseUrl: event.target.value })}
                />
              </label>
              <label>
                Customer ID
                <input
                  className="sales-input"
                  value={apiConfig.customerId ?? ''}
                  onChange={(event) => updateConfig({ customerId: event.target.value })}
                  placeholder="Smoke/customer ID"
                />
              </label>
              <label>
                Admin analysis token
                <input
                  className="sales-input"
                  value={apiConfig.token}
                  onChange={(event) => updateConfig({ enabled: true, token: event.target.value })}
                  placeholder="Paste token without Bearer"
                />
              </label>
              <label>
                Chat token
                <input
                  className="sales-input"
                  value={apiConfig.chatToken ?? ''}
                  onChange={(event) => updateConfig({ enabled: true, chatToken: event.target.value })}
                  placeholder="Paste JWT without Bearer"
                />
              </label>
            </div>

            <div className="sales-composer">
              <div className="sales-composer-row">
                <textarea
                  className="sales-textarea"
                  value={composer}
                  onChange={(event) => setComposer(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && !event.shiftKey) {
                      event.preventDefault();
                      void sendMessage();
                    }
                  }}
                  placeholder="Type a customer message..."
                />
                <button className="sales-send" disabled={busy || !composer.trim()} onClick={() => void sendMessage()}>
                  {busy ? 'Sending...' : 'Send'}
                </button>
              </div>
              {error ? <div className="sales-error">{error}</div> : null}
            </div>
          </footer>
        </section>

        <aside className="sales-panel sales-inspector-panel">
          <div className="sales-panel-head">
            <p className="sales-kicker">Plain-English review</p>
            <h2 className="sales-title">Bot Assessment</h2>
            <p className="sales-subtitle">
              Designed for sales review: what the bot understood, how confident it was, and what action it tried.
            </p>
          </div>

          <Inspector turn={selectedTurn} />
        </aside>
      </div>
    </div>
  );
}

function Bubble({
  role,
  text,
  selected,
  onClick,
  turn
}: {
  role: 'customer' | 'assistant';
  text: string;
  selected: boolean;
  onClick: () => void;
  turn: SalesChatTurn;
}) {
  const intent = getIntent(turn);
  return (
    <div className={`sales-bubble-row ${role}`}>
      <button className={`sales-bubble ${selected ? 'selected' : ''}`} onClick={onClick}>
        <div className="sales-bubble-text">{text}</div>
        <div className="sales-bubble-meta">
          {role === 'assistant' ? <span className="sales-badge info">{plainIntent(intent.query)}</span> : null}
          {role === 'assistant' ? <span className="sales-badge good">{plainConfidence(intent.confidence)}</span> : null}
          {turn.elapsedMs ? <span className="sales-badge">{turn.elapsedMs}ms</span> : null}
        </div>
      </button>
    </div>
  );
}

function Inspector({ turn }: { turn?: SalesChatTurn }) {
  if (!turn) {
    return (
      <div className="sales-inspector-body">
        <div className="sales-empty-card">
          <h3>No message selected</h3>
          <p>Send a message or click a chat bubble to review the bot’s understanding.</p>
        </div>
      </div>
    );
  }

  const intent = getIntent(turn);
  const service = getService(turn);
  const action = getAction(turn);
  const atoms = getRuntimeAtoms(turn);
  const source = getSource(turn);

  return (
    <div className="sales-inspector-body">
      <section className="sales-score-card">
        <h4>Overall read</h4>
        <div className="sales-kv">
          <Row label="Customer need" value={plainIntent(intent.query)} />
          <Row label="Service" value={plainService(service)} />
          <Row label="Sales stage" value={plainStage(intent.stage)} />
          <Row label="Bot source" value={plainSource(source)} />
        </div>
      </section>

      <section className="sales-score-card">
        <h4>Confidence</h4>
        <div className="sales-progress">
          <span style={{ width: `${Math.round((intent.confidence ?? 0) * 100)}%` }} />
        </div>
        <div className="sales-bubble-meta">
          <span className={`sales-badge ${confidenceTone(intent.confidence)}`}>
            {plainConfidence(intent.confidence)}
          </span>
          {typeof intent.confidence === 'number' ? (
            <span className="sales-badge info">{Math.round(intent.confidence * 100)}%</span>
          ) : null}
        </div>
      </section>

      <section className="sales-score-card">
        <h4>Action taken</h4>
        <div className="sales-kv">
          <Row label="Action" value={plainAction(action.actionType)} />
          <Row label="Status" value={plainStatus(action.status)} />
          <Row label="Missing info" value={plainList(action.missingSlots)} />
          <Row label="Timing" value={turn.elapsedMs ? `${turn.elapsedMs}ms` : 'Not measured'} />
        </div>
      </section>

      <section className="sales-score-card">
        <h4>Detected details</h4>
        <div className="sales-bubble-meta">
          {plainAtomBadges(atoms).map((item) => (
            <span className="sales-badge info" key={item}>
              {item}
            </span>
          ))}
          {!plainAtomBadges(atoms).length ? <span className="sales-badge">No extra details found</span> : null}
        </div>
      </section>

      <section className="sales-score-card">
        <h4>Sales reviewer notes</h4>
        <div className="sales-kv">
          <Row label="Good sign" value={reviewGoodSign(intent.query, service)} />
          <Row label="Watch for" value={reviewWatchFor(action.missingSlots)} />
          <Row label="Next step" value={reviewNextStep(action.actionType, action.missingSlots)} />
        </div>
      </section>

      <details className="sales-score-card sales-details">
        <summary>View raw response</summary>
        <pre className="sales-json">{JSON.stringify(turn.response ?? {}, null, 2)}</pre>
      </details>

      <details className="sales-score-card sales-details">
        <summary>View raw trace</summary>
        <pre className="sales-json">{JSON.stringify(turn.trace ?? {}, null, 2)}</pre>
      </details>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="sales-kv-row">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

async function tryFetchTrace(api: AdminApiClient, threadId: string): Promise<LiveTrace | undefined> {
  try {
    const response = await api.threadLiveTraces(threadId, { limit: 20 });
    return response.traces.at(-1) ?? response.traces[0];
  } catch {
    return undefined;
  }
}

function createTransientThread(customerId?: string): SalesChatThread {
  const now = new Date().toISOString();
  return {
    id: makeLocalId('sales-thread'),
    title: 'New sales test',
    customerId,
    createdAt: now,
    updatedAt: now,
    turns: []
  };
}

function assistantTextFromResponse(response: ChatTurnResponse): string {
  return response.bubbles.map((bubble) => bubble.text).filter(Boolean).join('\n\n') || 'No response text returned.';
}

function titleFromMessage(message: string): string {
  const clean = message.trim().replace(/\s+/g, ' ');
  if (!clean) return 'New sales test';
  return clean.length > 48 ? `${clean.slice(0, 48)}...` : clean;
}

function latestPreview(thread: SalesChatThread): string {
  const latest = thread.turns.at(-1);
  if (!latest) return 'No messages yet';
  return latest.assistantText || latest.customerText;
}

function timeAgo(value: string): string {
  const then = new Date(value).getTime();
  const diff = Math.max(0, Date.now() - then);
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return 'now';
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

function safeRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function getIntent(turn: SalesChatTurn): {
  query?: string;
  stage?: string;
  confidence?: number;
} {
  const responseIntent = safeRecord(turn.response?.intent);
  const traceIntent = safeRecord(turn.trace?.intent);
  const components = safeRecord(turn.trace?.components);
  const decision = safeRecord(components.decision_layer);

  const query =
    stringValue(responseIntent.query_primary) ||
    stringValue(traceIntent.query_primary) ||
    stringValue(decision.query_primary);

  const stage =
    stringValue(responseIntent.funnel_stage) ||
    stringValue(traceIntent.funnel_stage) ||
    stringValue(decision.funnel_stage);

  const confidence =
    numberValue(responseIntent.confidence) ??
    numberValue(traceIntent.confidence) ??
    numberValue(decision.confidence);

  return { query, stage, confidence };
}

function getService(turn: SalesChatTurn): string | undefined {
  const responseIntent = safeRecord(turn.response?.intent);
  const traceIntent = safeRecord(turn.trace?.intent);
  const components = safeRecord(turn.trace?.components);
  const decision = safeRecord(components.decision_layer);

  return (
    stringValue(responseIntent.service_primary) ||
    stringValue(traceIntent.service_primary) ||
    stringValue(decision.service_primary)
  );
}

function getAction(turn: SalesChatTurn): {
  actionType?: string;
  status?: string;
  missingSlots: string[];
} {
  const action = safeRecord(turn.trace?.action_plan);
  return {
    actionType: stringValue(action.action_type),
    status: stringValue(action.status),
    missingSlots: arrayOfStrings(action.missing_slots)
  };
}

function getSource(turn: SalesChatTurn): string | undefined {
  const assistant = safeRecord(turn.trace?.assistant);
  const components = safeRecord(turn.trace?.components);
  const decision = safeRecord(components.decision_layer);
  return stringValue(assistant.source) || stringValue(decision.source);
}

function getRuntimeAtoms(turn: SalesChatTurn): Record<string, unknown> {
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

function plainConfidence(value?: number): string {
  if (value === undefined) return 'Confidence not shown';
  if (value >= 0.9) return 'High confidence';
  if (value >= 0.7) return 'Medium confidence';
  return 'Low confidence';
}

function confidenceTone(value?: number): string {
  if (value === undefined) return '';
  if (value >= 0.9) return 'good';
  if (value >= 0.7) return 'warn';
  return 'bad';
}

function plainList(values: string[]): string {
  if (!values.length) return 'Nothing missing';
  return values.map(humanize).join(', ');
}

function plainAtomBadges(atoms: Record<string, unknown>): string[] {
  const labels: string[] = [];

  for (const service of arrayOfStrings(atoms.services)) {
    labels.push(`Service: ${plainService(service)}`);
  }

  for (const service of arrayOfStrings(atoms.negated_services)) {
    labels.push(`Not needed: ${plainService(service)}`);
  }

  for (const count of numberArray(atoms.word_counts)) {
    labels.push(`${count.toLocaleString()} words`);
  }

  for (const count of numberArray(atoms.page_counts)) {
    labels.push(`${count.toLocaleString()} pages`);
  }

  for (const email of arrayOfStrings(atoms.emails)) {
    labels.push(`Email found: ${email}`);
  }

  for (const phone of arrayOfStrings(atoms.phones)) {
    labels.push(`Phone found: ${phone}`);
  }

  return labels.slice(0, 12);
}

function numberArray(value: unknown): number[] {
  return Array.isArray(value) ? value.filter((item): item is number => typeof item === 'number') : [];
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
  return value
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase());
}
