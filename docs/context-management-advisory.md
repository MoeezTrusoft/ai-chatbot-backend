# Context-Management Advisory

_Written 2026-07-23. Scope: how conversation context is assembled for **response
generation** and where it can be improved. Now especially relevant because response
generation runs on **Opus** (more expensive per token) — context discipline directly
controls cost and coherence._

## How it works today (ground truth)

| Element | Current behavior | File |
|---|---|---|
| **Conversation history** | Exactly **1 prior turn** replayed: `(last_user_message, last_assistant_text)`, each truncated to **300 chars**. | `services/chat.py` (~1560–1563 assemble, ~1818–1821 persist) |
| History renderer | Already tolerates the **last 3** turns × 300 chars — but the caller only ever passes 1. | `components/response/generator.py` `_recent_turns_prompt_section` (~1211–1232) |
| **Structured state** (the real memory) | ThreadState facts + ContextPack (`known_facts` w/ provenance, `missing_facts`, `forbidden_reasks`, active service, allowed questions) injected every turn. Grows with accumulated facts. | `generator.py` (~1493–1683), `context/pack_builder.py` |
| **RAG** | Top **5** chunks × **600 chars** (~3 KB), intent-gated. | `generator.py` (~1603–1616); `rag_top_k=8` |
| **Prompt caching** | ON in prod, but **one** breakpoint: the stable system prompt. Date/time split into an uncached block. Facts/RAG/history are never cached (they change per turn). | `components/llm/adapters.py` (~114–134) |
| **Summarization** | `ConversationCheckpointer` + `CsrContextSummarizer` exist — but feed the **CSR dashboard**, not the generation prompt. | `main.py` (~418–431) |

**Net:** the model sees a large (cached) system prompt, a modest per-turn structured-state
block, ~3 KB RAG, and **one** ≤600-char prior exchange. Coherence beyond 1 turn relies
entirely on facts having been extracted into state — anything the extractor missed is gone.

## Recommendations (prioritized)

### 1. Widen the history window 1 → 3–5 turns _(highest value / lowest effort)_
The renderer already supports 3. Persist a small ring buffer of recent
`(user, assistant)` pairs in state instead of only the last pair, and pass them through.
Fixes the common "bot forgets what I said two turns ago" failure — especially valuable now
that non-fact conversational nuance (tone, a question just asked) is worth retaining.
Keep the existing 300-char/normalized truncation for PII safety. **~1 day.**

### 2. Rolling thread summary for long conversations
When a thread exceeds N turns (e.g. 8–10), summarize the *older* turns into a compact
"conversation so far" block and inject it ahead of the recent-turns window. Reuse the
existing `CsrContextSummarizer` infra, but run it on the **cheaper extraction adapter**
(now decoupled) — not Opus. This is real long-context management; the current design has
no path for it on the generation side.

### 3. Add a second cache breakpoint
Today only the system prompt is cached. Move stable, thread-invariant preamble (persona,
policy, service catalog) into a second cached prefix, and keep only the genuinely volatile
tail (this turn's facts, RAG, history) uncached. On Opus this measurably lowers per-turn
cost. Verify ordering so the cached prefix is byte-stable within a thread.

### 4. RAG hygiene
- Cache retrieved chunks per thread and skip re-injecting identical text turn after turn
  (saves tokens **and** reduces verbatim-bleed surface — see `rag-verbatim-bleed`).
- Keep tightening the intent gate so RAG only loads when the turn actually needs doc facts.

### 5. Bound structured-state growth
`ContextPack.known_facts` grows unbounded as facts accumulate. There's already a
disabled hint budget (`context_pack_budget_enabled` / `context_pack_hint_token_budget=1200`)
— enable and tune it, and prune to the top-K most relevant facts per turn (by provenance/
recency) so a long, fact-rich thread doesn't creep the prompt upward every turn.

### 6. Instrument the prompt budget
Log per-turn token composition (cached system vs facts vs RAG vs history). Right now there's
no visibility into which segment is growing — you can't manage what you don't measure. This
also lets you catch Opus cost regressions early.

### 7. Opus-specific
Keep `response_thinking_mode="disabled"` unless you deliberately test `adaptive` — and if you
do, raise `response_max_tokens` well above 2048 first, because thinking shares the reply's
token budget (the sonnet-5 lesson in chat 5876 applies to Opus too). Confirm `claude-opus-4-8`
accepts the `thinking` shape before flipping.

## Suggested order
1 (window) → 6 (instrument) → 3 (2nd cache breakpoint) → 5 (state budget) → 4 (RAG cache)
→ 2 (rolling summary). Items 1, 3, 6 are the fast, high-leverage wins.
