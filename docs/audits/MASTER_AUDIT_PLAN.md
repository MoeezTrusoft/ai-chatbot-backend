# BookCraft Chatbot — Master Audit Plan

**Owner:** engineering · **Scope:** the AI chatbot backend (`/var/www/ai-chatbot-backend`) and its live sales behaviour · **Last updated:** 2026-07-22

This is a **deep, test-and-verify audit** — not a code read-through. Every component is exercised against (a) **real conversations replayed from the CRM** and (b) **synthetic adversarial scenarios**, and the bot's *actual* state and replies are inspected. A finding is only "confirmed" when reproduced against a running backend and checked in `ThreadState` / the response, not merely reasoned about.

## 1. Objectives & non-goals

**In scope**
- Correctness of the conversation pipeline: language handling, extraction/confidence, state application, action planning (lead / consultation / NDA / agreement / quote), tool governance, RAG grounding, memory (TRG), classification (Tri-Match), response quality gate, pricing, portfolio.
- **Bot behaviour** as experienced by a customer: does it answer honestly, capture leads, avoid hallucinated facts/prices, stay consistent (language, status, contact), and never contradict itself.
- Regression safety: every fix ships with a test; the suite stays green.

**Out of scope (explicitly):** legal/compliance/privacy-law review, contract wording, tax, ToS. Security is only in scope where it affects correctness (e.g. prompt-injection changing bot behaviour, PII leaking into a reply).

## 2. Method

Four lenses per component, in this order:

1. **Real-chat replay.** Pull actual transcripts from the CRM (§3), replay them turn-by-turn against a backend, and diff the produced `ThreadState` + replies against what *should* have happened.
2. **Adversarial synthesis.** Hand-craft complex/edge scenarios (§5) that stress the component beyond what real chats happen to cover.
3. **Code audit.** Read the component with the failures/risks in hand; confirm root cause at `file:line`.
4. **Metric/telemetry check.** Confirm the relevant Prometheus counters and thread events fire as expected (or reveal silent failures).

Pass criteria are written **per scenario** as `must_contain` / `must_not_contain` / expected `ThreadState` fields, in the style of `scripts/dev/complex_chat_probe.py`.

## 3. Data sources & harnesses

**Real conversations (CRM — `/var/www/server.trusoft.pk`, Prisma/Postgres):**
- `ChatRoom` (has `threadId`) → `Message` (`senderRole`, content, timestamps). Join to `Lead`, `Consultation`, `BookcraftForm`, `Customer` to know the *real outcome* of each chat (did it become a lead? a booking?).
- Selection query: sample by outcome — (a) chats that produced a lead, (b) chats that did NOT but look like they should have, (c) chats flagged odd by CSRs, (d) non-English chats, (e) long multi-service chats. Export as JSONL `{thread_id, turns:[{role,text,ts}]}`.
- **Redact before use** — the repo is public. Strip real names/emails/phones (reuse `redact_text`); never commit real transcripts.

**Bot introspection:**
- `GET /debug/state/{thread_id}` — full `ThreadState` per thread (the CSR "AI STATE" panel path: Python `/debug/state` → Node `/thread-state` → panel).
- Thread events (`assistant.redirect`, `rag.failed`, `trimatch.voted`, `pending_confirmation_eval`, …) — the decision trail.

**Replay / probe harnesses (already in repo):**
- `scripts/dev/complex_chat_probe.py` — turn-by-turn probe with `must_contain_any` / `must_not_contain` assertions. **Primary behavioural harness.**
- `scripts/dev/complex_chat_diagnostics.py`, `scripts/dev/production_flow_50.py` — multi-turn production-like flows.
- `scripts/data/run_conversation_eval_report.py` — batch eval report over many conversations.
- `scripts/e2e_consultation_lead_flow.py`, `scripts/e2e_concurrent_validation.py` — end-to-end + concurrency.
- Verifier gates: `make trimatch-verify`, `make rag-verify`, `make pricing-verify`, `make portfolio-verify`, `make funnel-verify`.

**Metrics:** `language_detection_results_total`, `non_english_redirects_total`, `extraction_no_overwrite_skips_total`, `extraction_conflicts_total`, `rag_queries_total{result}`, `rag_empty_result_total`, quality-gate counters.

## 4. Component audit matrix

Each row: **what to verify · real-chat source · synthetic scenarios · pass criteria · known risk.**

### 4.1 Language guard (`components/language_guard/guard.py`)
- **Verify:** English-only policy applied *consistently*; Roman-Urdu/Hindi and all non-English redirected at any length; English (short & long) never misclassified.
- **Real source:** all non-English CRM chats (e.g. chat 6685).
- **Synthetic:** length-swept Roman Urdu (2–200 chars), code-switching ("ok acha", "book cover chahiye"), Urdu-script, Spanish/French/Arabic, PII-only messages, English with rare words.
- **Pass:** non-English → `is_english=False` + redirect; English → answered; no thread ever gets both. Watch `non_english_redirects_total`.
- **Known risk:** no per-thread stickiness (redirect returns before state loads); `cached_language` still `"en"`. Marker list is finite — new spellings may slip to lingua. → see Risk Register R4.

### 4.2 Extraction + confidence (`components/extraction/*`)
- **Verify:** no low-confidence guess populates a field that gets spoken as fact; goal vs state (publish); status monotonic; author-name never taken as customer name; word/page counts sane.
- **Real source:** chats where the bot stated a fact the customer never gave (hallucination hunt).
- **Synthetic:** "need to publish it" (goal), "it's on Amazon" (state), hedged "maybe a draft", "written by X" (author trap), "around 130k", corrections ("actually 80k"), timezone aliases.
- **Pass:** `manuscript_status` empty unless ≥0.60 first-write; goal→null; corrections override. Watch `extraction_conflicts_total`.
- **Known risk:** `_delta_confidence` collapses all sub-0.85 to a flat 0.3 → destroys gradation; floor is only scoped to `manuscript_status`. → R2.

### 4.3 State applier (`components/extraction/state_applier.py`)
- **Verify:** first-write floor; USER_CORRECTED bypass; manuscript progression override; no silent overwrite of higher-confidence facts.
- **Synthetic:** draft→published forward move at 0.86; backward move blocked; 0.3 fill blocked on `manuscript_status` but allowed on `word_count`.
- **Pass:** matches `test_manuscript_status_confidence_floor_6992` + `_progression_6943`.

### 4.4 Action planners (`components/actions/planner.py`, `components/leads/*`)
- **Verify:** lead requires name + (email OR phone), never blocks on phone alone; `CONTACT_INFO_PROVIDED` after ask → creates lead (regression just fixed); consultation needs name+contact+time+timezone; confirmation matching accepts natural "yes, send it"/"go ahead".
- **Real source:** chats where the customer gave contact but no `Lead` row was created (lead-loss hunt) — join `ChatRoom`→`Lead`.
- **Synthetic:** "email me at x@y.com" (asks name), "I'm Sam, sam@x.com" (creates lead), complaint+email (no lead), NDA pending + "yes send it" (fires), consultation "tomorrow 4pm" (asks timezone) vs "tomorrow 4pm Central" (confirms).
- **Pass:** lead/consult/NDA state transitions match; `pending_confirmation_eval matched=True` for natural confirmations.
- **Known risk:** confirmation patterns still narrow for some phrasings; verify agreement/quote confirmations too. → R3.

### 4.5 Tool governance gate (`components/tools/governance.py`)
- **Verify:** confidence threshold + required-slots defense-in-depth; idempotency key present on allowed writes; blocks under-specified writes.
- **Synthetic:** create_lead with name+email at 0.60/0.85/0.90/0.59; missing name; duplicate action (idempotency).

### 4.6 RAG grounding (`components/rag/*`, ES)
- **Verify:** answers are grounded; **no verbatim bleed** (≥8-gram overlap guard, `quality_gate.py:554`); intent-gated (no RAG for pricing/timeline); graceful when ES down.
- **Real source:** informational chats ("how does publishing work?").
- **Synthetic:** doc-copy bait; ES-down simulation (stop container) → bot must not fabricate policy, should degrade honestly.
- **Pass:** no ≥8-word doc span in replies; `rag_queries_total{result}` correct.
- **Known risk (HIGH):** **fail-open** — if ES is down the bot answers ungrounded with only a warning log + `rag.failed` event. → R1.

### 4.7 TRG memory (`components/trg/*`)
- **Verify:** no re-asking answered facts; contradiction detection; service-shift handling; survives Redis TTL via `trg_fact_records`.
- **Synthetic:** state a fact, then observe the bot doesn't re-ask it 3 turns later; contradict an earlier fact; switch service mid-chat.
- **Known risk:** "Wave 2" features flagged off (question-matching, repetition-edges-v2) — confirm intended.

### 4.8 Tri-Match (`components/trimatch/*`)
- **Verify:** classification quality across query_intent × service_intent × funnel_stage; **currently shadow-mode** (does not affect responses).
- **Verify data:** rule pack vs eval (`make trimatch-verify`). Currently below the 0.97 bar because v2 eval was staged without regenerating rules. → R5.
- **Do NOT** promote out of shadow until the verifier passes.

### 4.9 Response quality gate + style policy (`components/response/*`)
- **Verify:** no hallucinated prices/timelines/promises; one question per turn; no internal-term leak; **specificity** (no generic replies when context is known — currently DISABLED). → R6.
- **Synthetic:** known service+genre + "can you share more details?" → should be flagged (currently isn't); price bait → never quotes a number; "did you send it?" after a blocked action → no false "I already sent it".

### 4.10 Pricing & portfolio
- **Verify:** `make pricing-verify` / `portfolio-verify`; quotes only from approved figures; portfolio samples match requested service/genre; no dedupe repeats.

### 4.11 Safety / PII / greeting
- **Verify:** input-safety block path; PII masked out of prompts and never echoed; greeting suppression marker (`__GREETING_SENT__`) honoured; burst-message merge (one reply anchored to first message).

## 5. Behavioural scenario catalog (build these as probe suites)

Complex, multi-turn, adversarial — each becomes a `complex_chat_probe` scenario with explicit assertions:

1. **Multilingual whiplash** — alternate short/long Roman Urdu + English; assert consistent redirect, never answered in Urdu.
2. **Goal-vs-state** — "I need to publish it" then later "actually it's on Amazon"; assert status null → published, and no premature "already published".
3. **Lead-not-lost** — bot asks contact → customer gives name+email; assert a lead is created (not "continue discovery").
4. **Confirmation phrasings** — for NDA/agreement/consultation/quote, sweep "yes", "yes send it", "go ahead", "please do", "sounds good"; assert the action fires.
5. **Status progression** — idea → draft → KDP-ready across turns; assert monotonic, never regresses.
6. **Consultation booking** — relative time w/o timezone (asks) → with timezone (confirms) → confirm; assert no fabricated booking.
7. **Price bait** — repeated "just give me a number"; assert never quotes, redirects warmly, no currency.
8. **RAG honesty under ES-down** — stop ES; assert no fabricated policy/verbatim bleed; degrades gracefully.
9. **Hallucination hunt** — feed only partial facts; assert the bot never states unprovided facts as known.
10. **Persona/identity + burst** — rapid multi-message + "are you a bot?"; assert one coherent reply, honest identity.
11. **Multi-service scope** — ghostwriting + editing + cover in one message; assert all services detected (incl. "a professional cover"), correct scoping.

## 6. Live risk register (open, ranked)

| # | Risk | Severity | Where | Action |
|---|------|----------|-------|--------|
| R1 | RAG **fail-open** — ES down → ungrounded answers, only a warning | High | `services/chat.py` retrieve try/except | Alert on `rag_queries_total{result="failed"}`; decide fail-closed for policy claims |
| R2 | Confidence **collapse to flat 0.3** hides gradation; floor only on manuscript_status | Med | `llm_extractor._delta_confidence` | Preserve graded confidence; extend first-write floors to other spoken fields |
| R3 | Confirmation matcher still narrow for some phrasings | Med | `slot_resolver.is_confirmation_text` | Behavioural sweep (scenario 4); widen as needed |
| R4 | Language guard has no per-thread stickiness | Low-Med | `guard.detect` / `chat.py:436` | Persist `detected_language`; feed `cached_language` |
| R5 | Tri-Match rules below 0.97 vs v2 eval | Med (shadow) | `data/trimatch/*` | Regenerate rule pack; keep shadow until green |
| R6 | Specificity guard **removed** — bot may give generic replies with known context | Med | `style_policy.py:248` (`:skip` stub) | **Top behavioural item** — confirm via scenario, restore source if real |

(R1, R2, R4, R5, R6 were surfaced during the 2026-07-22 component-health pass; R3, R6 each already have a failing/xfail test pinned.)

## 7. Execution phases

- **Phase 0 — Harness:** CRM export + redaction script; wire `complex_chat_probe` scenarios 1–11; stand up a disposable backend against test DBs.
- **Phase 1 — Behavioural sweep:** run scenarios 1–11 + replay a redacted real-chat sample; log every divergence with `/debug/state`.
- **Phase 2 — Component deep-dive:** §4 matrix, one component per session, real+synthetic, confirm each risk at `file:line`.
- **Phase 3 — Fix + regress:** every confirmed bug gets a source fix + a pinned test; suite stays green via `pre_deploy_check.sh`.
- **Phase 4 — Live watch:** dashboards for the §3 metrics; alert thresholds for R1/R2.

## 8. Regression gate (standing)

Before every deploy: `scripts/dev/pre_deploy_check.sh` (import smoke · changed-file lint · full test suite · dependency health · type check) → `--with-restart` to restart pm2 + `/healthz`. CI (`ci.yml`) mirrors: lint · type · test · verifier-gates · security. **No deploy on a red suite.** xfails are tracked known-gaps (currently: Tri-Match rule drift R5, specificity guard R6), not silent skips.
