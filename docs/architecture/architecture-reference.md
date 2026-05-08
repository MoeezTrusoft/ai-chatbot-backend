# BookCraft AI Chatbot — Architecture Reference

> **Document Version:** 2.0 &nbsp;•&nbsp; **Status:** Approved for Implementation &nbsp;•&nbsp; **Last Updated:** 2026-05-05
> 
> This is the canonical architecture reference for the BookCraft AI sales chatbot. It supersedes all prior design documents. Any deviation during implementation must be recorded as an Architecture Decision Record (ADR) referencing the specific section being modified.

---

## Document Information

| Attribute | Value |
|---|---|
| Document type | Architecture Reference |
| Audience | Engineering, Operations, Finance, Legal, Leadership |
| Implementation status | Design-locked, ready for build |
| Review cadence | Quarterly until launch, then annually |
| Format | Markdown, versioned in Git alongside source code |
| Cross-reference style | `§N.M` (sections), `D-NNN` (decisions), `R-NNN` (risks), `Appendix X` |

### How to use this document

- **Engineers:** §6 component reference + Appendices A-D for schemas and contracts
- **Operations:** §8 operational excellence + Appendix E observability spec
- **Finance:** §10 cost model with sensitivity analysis
- **Leadership:** §1 executive summary + §11 risk register
- **Legal:** §6.8 NDA/agreement engine + §7.5 security and privacy
- **Onboarding new team members:** read linearly through §1-§5, then drill into §6 components as needed

---

## Glossary

| Term | Definition |
|---|---|
| **ADR** | Architecture Decision Record — versioned justification for a significant design choice |
| **Decision Layer** | Component that aggregates Tri-Match + 3 LLM votes into a final intent classification |
| **Ensemble** | The set of three LLM classifiers (Haiku, GPT-mini, DeepSeek) running in parallel |
| **FieldMeta** | Pydantic generic wrapper providing provenance (`value`, `confidence`, `source`, `extracted_at`, `raw_excerpt`) for any meaningful state field |
| **Hot graph** | The recent N nodes of a thread's TRG, kept in Redis for sub-millisecond access |
| **MCP** | Model Context Protocol — Anthropic's standard for tool definitions and invocation |
| **PII** | Personally Identifiable Information — names, emails, phones, project content |
| **PreExtractedFacts** | Deterministic atoms (email, phone, currency, dates) extracted before the LLM call |
| **ProcessedMessage** | Shared preprocessing artifact (tokens, lemmas, negation spans, atoms, embedding) consumed by Tri-Match, Extraction, and other components |
| **Quorum** | Minimum agreement threshold (2 of 3 LLM votes) that allows early termination of remaining LLM calls |
| **RAG** | Retrieval Augmented Generation — fetching relevant documents to ground LLM responses |
| **SLO** | Service Level Objective — internal performance target |
| **SLA** | Service Level Agreement — external commitment to users |
| **Shadow mode** | Component runs and logs results but doesn't influence decisions |
| **Shortcut** | Tri-Match returns its result without invoking the LLM ensemble (Phase 5+ behavior) |
| **TEI** | Text Embeddings Inference — HuggingFace's optimized embedding server |
| **TRG** | Temporal Relational Graph — conversation-level signals (relations, compliance, repetition, outstanding questions) |
| **Tri-Match** | In-house deterministic classifier (preprocessing → matchers → semantic → aggregation → calibration) |

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [System Overview](#2-system-overview)
3. [Stack & Infrastructure](#3-stack--infrastructure)
4. [SLAs, SLOs, and Performance Budgets](#4-slas-slos-and-performance-budgets)
5. [Per-Turn Request Flow](#5-per-turn-request-flow)
6. [Component Reference](#6-component-reference)
7. [Cross-Cutting Concerns](#7-cross-cutting-concerns)
8. [Operational Excellence](#8-operational-excellence)
9. [Build Sequence](#9-build-sequence)
10. [Cost Model](#10-cost-model)
11. [Risk Register](#11-risk-register)
12. [Decision Ledger](#12-decision-ledger)
13. [Roadmap & Future Work](#13-roadmap--future-work)
14. [Appendices](#14-appendices)

---

## 1. Executive Summary

BookCraft AI's chatbot is a 24/7 web sales assistant built for production-grade reliability with state-of-the-art techniques. It handles the full sales lifecycle — initial inquiry, discovery, scoping, quoting, NDA generation, agreement issuance — with conversational quality that compounds over time as the in-house Tri-Match engine learns from operational data.

**Key architectural innovations:**

- **Quad-source intent classification.** Three LLM vendors (Anthropic Haiku, OpenAI GPT-5.4 mini, DeepSeek V3 self-hosted) plus an in-house deterministic Tri-Match engine vote into a Decision Layer with race-with-quorum termination.
- **Temporal Relational Graph (TRG).** Conversation-level signals — relations between turns, compliance scoring, outstanding question tracking, repetition detection — that no single LLM can compute alone.
- **Strict-template document generation.** LLMs never write a character of legal text. NDA and service agreement generation use lawyer-reviewed Jinja2 templates, typed parameters, two-LLM verification, hash-chained audit, and phased autonomous rollout.
- **Self-improving rule engine.** Daily batch jobs mine LLM disagreements, generate Tri-Match rule suggestions via Sonnet, evolve the deterministic engine without manual rule writing.

**Operating economics at locked scale (2,400 conv/mo × 30 turns):**

| Phase | LLM cost/month | Per-turn cost | Tri-Match hit rate |
|---|---|---|---|
| Launch (months 0-2) | ~$2,030 | ~$0.0282 | 0% (shadow mode) |
| Maturing (months 3-6) | ~$1,945 | ~$0.0270 | ~25% |
| Mature (months 12+) | ~$1,857 | ~$0.0258 | ~55% |

The system gets cheaper and higher-quality with operational age. This is unusual and intentional.

---

## 2. System Overview

### 2.1 Strategic properties

Five architectural properties distinguish this system from conventional LLM-based chatbots. Every design decision was evaluated against these:

| Property | What it means | How achieved |
|---|---|---|
| **Cost decreases with maturity** | Per-turn cost falls as the system ages | Tri-Match shortcut hit rate grows; TRG compaction prevents context bloat |
| **Quality compounds across components** | No single component is the system's quality ceiling | Tri-Match learns from LLM disagreements; LLMs validated against deterministic patterns; TRG provides context to all |
| **Real fault tolerance** | Single vendor outages do not break the service | 3-LLM ensemble with race-with-quorum; circuit breakers; graceful degradation paths |
| **Bounded blast radius** | Single bugs cannot produce thousands of bad outcomes | Centralized MCP dispatcher; idempotency keys; phased autonomous rollout for high-stakes ops |
| **Forensic auditability** | Any past decision can be reconstructed | Hash-chained event log; full ensemble vote capture; FieldMeta provenance; signed PDFs |

### 2.2 Architectural principles

1. **The LLM is a component, not the system.** Deterministic logic owns control flow; LLMs are queried for inference.
2. **Every meaningful state field carries provenance.** `value` alone is insufficient. We need `confidence`, `source`, `extracted_at`, `raw_excerpt`.
3. **Tools are typed and gated.** No free-form HTTP calls from inside the LLM's loop. Every external interaction goes through the MCP dispatcher with schema validation, gating policy, idempotency, and audit.
4. **Concurrency is explicit.** When work can run in parallel, it does. When it must serialize, the dependency is documented and budgeted.
5. **Latency is a first-class budget.** Each component has a target; the orchestrator enforces timeouts; slow paths degrade gracefully.
6. **Cost is monitored continuously.** Every LLM call is tagged, every token counted, and per-turn cost is a tracked metric with alerting.
7. **State is versioned and migrate-able.** Schema evolution doesn't require database downtime.

### 2.3 Non-goals

The architecture deliberately does not address:

- Multi-language support (English only at launch; D-018)
- Voice or audio interaction (web text only)
- Mobile-app native integration (responsive web chat sufficient)
- Document attachment delivery (URL-only at launch; D-039)
- Dynamic portfolio gallery generation (curated static; D-038)
- Role-based access control for CSRs (uniform v1; D-046)
- EU GDPR-specific data flow (mentioned for DeepSeek sovereignty; full GDPR DPA is a separate compliance project)
- SOC 2 / enterprise certifications (separate operational project)
- Telephony or IVR integration

### 2.4 Capacity targets

| Metric | Launch target | Burst capacity | Notes |
|---|---|---|---|
| Conversations per month | 2,400 | 7,200 | 3× burst tolerance |
| Turns per month | 72,000 | 216,000 | At 30 turns/conv avg |
| Concurrent active threads | 10–15 | 50 | Most threads are dormant between user turns |
| Peak QPS (chat WebSocket) | 5 | 30 | Messages incoming |
| Anthropic API rate limit | Tier 2 | Tier 4 | Request upgrade before Phase 4 |
| OpenAI API rate limit | Tier 4+ | Tier 5 | Required for ensemble |
| Postgres connections | 50 | 100 | Via pgbouncer |
| Redis memory | 2 GB | 8 GB | Hot graphs + cache + idempotency |
| ES storage | 10 GB | 50 GB | RAG corpus + dense vectors |
| TEI throughput | 30 req/s | 100 req/s | CPU-only at launch is sufficient |

---

## 3. Stack & Infrastructure

### 3.1 Application stack

| Layer | Technology | Version | Rationale |
|---|---|---|---|
| Language | Python | 3.12+ | Best AI/ML ecosystem |
| Web framework | FastAPI | 0.115+ | Async-native, WebSocket support |
| ORM | SQLModel + SQLAlchemy 2.0 async | Latest | Pydantic-integrated |
| Validation | Pydantic | v2 | Used throughout for schema enforcement |
| Database driver | asyncpg | Latest | Fastest Postgres async driver |
| Connection pooling | pgbouncer | Latest | Independent worker scaling |
| Cache client | redis-py async | Latest | Cluster-ready |
| HTTP client | httpx async | Latest | OpenAI/DeepSeek/HTTP MCP tools |
| Workflow orchestration | Temporal | Latest | NDA/agreement durable workflows |
| Background jobs | Arq | Latest | Lightweight async tasks |
| NLP preprocessing | spaCy + en_core_web_sm | Latest | Tokenization, lemmas, negation |
| Fuzzy matching | rapidfuzz | Latest | C++-backed, faster than fuzzywuzzy |
| Phone parsing | phonenumbers | Latest | Google's i18n phone library |
| PDF rendering | WeasyPrint | Latest | Markdown→PDF for documents |
| Templating | Jinja2 (StrictUndefined) | Latest | Document templates |

### 3.2 Data stack

| Layer | Technology | Version | Sizing |
|---|---|---|---|
| Primary database | PostgreSQL | 16 | Single primary + 1 read replica |
| Vector extension | pgvector | 0.7+ | HNSW index, m=16 |
| Hot cache & pub/sub | Redis | 7 | 2 GB instance, cluster-ready |
| Search engine | Elasticsearch | 8 | Single-node initially, 3-node cluster post-Phase 3 |
| Embedding service | TEI (BGE-small-en-v1.5) | Latest | CPU sidecar |
| Object storage | S3-compatible | — | PDF documents, parquet archives |
| Email delivery | SendGrid or SES | — | NDA/agreement delivery |

### 3.3 LLM providers

| Provider | Model | Use case | Pricing (per MTok) |
|---|---|---|---|
| Anthropic | Claude Sonnet 4.6 | Response generation, agreement verifier | $3.00 / $15.00 |
| Anthropic | Claude Haiku 4.5 | Intent (one of three), extraction, NDA verifier, TRG relation, batch suggestions | $1.00 / $5.00 |
| OpenAI | GPT-5.4 mini | Intent ensemble vote | $0.75 / $4.50 |
| DeepSeek | V3 (self-hosted) | Intent ensemble vote | Compute cost only |

**DeepSeek deployment:** Self-hosted on a single A100 or equivalent. The hosted DeepSeek API is **not approved** for production due to data sovereignty (CN-hosted) — D-026.

### 3.4 Observability stack

| Layer | Technology | Purpose |
|---|---|---|
| Distributed tracing | OpenTelemetry + Tempo/Jaeger | Per-turn end-to-end traces |
| Metrics | Prometheus + Grafana | Time-series metrics + dashboards |
| Logs | Loki or OpenSearch | Structured JSON logs |
| Exception tracking | Sentry | Application errors |
| Alerting | Alertmanager → PagerDuty | Tiered escalation |
| Status page | StatusPage.io or equivalent | External status communication |

### 3.5 Deployment topology

```
                     ┌─────────────────────────────┐
                     │   CDN / WAF (Cloudflare)    │  TLS 1.3, DDoS, rate limit
                     └──────────────┬──────────────┘
                                    │
                       ┌────────────┴────────────┐
                       ▼                         ▼
                ┌──────────────┐          ┌──────────────┐
                │ WS Gateway   │          │ HTTP API     │  Stateless
                │ (sticky)     │          │ (stateless)  │  ASGI workers
                └──────┬───────┘          └──────┬───────┘
                       │                         │
                       └────────────┬────────────┘
                                    ▼
                  ┌──────────────────────────────┐
                  │     Application Tier         │
                  │   (FastAPI workers, async)   │
                  │     [horizontal scaling]     │
                  └──┬───┬───┬──────────┬─────┬──┘
                     │   │   │          │     │
        ┌────────────┘   │   └──┐       │     └──────┐
        ▼                ▼      ▼       ▼            ▼
  ┌──────────┐   ┌──────────┐  ┌──────────┐  ┌──────────────┐
  │ Postgres │   │  Redis   │  │   ES     │  │ TEI sidecar  │
  │ + pgvec  │   │          │  │          │  │ BGE-small    │
  │ + repli  │   │ Cluster- │  │ + dense  │  │              │
  │   ca     │   │ ready    │  │ vectors  │  │              │
  └────┬─────┘   └──────────┘  └──────────┘  └──────────────┘
       │
       ▼
  ┌──────────────────┐                ┌──────────────────────┐
  │ Logical decoding │  ────►  Kafka  │  Async Workers       │
  │                  │                │  (Temporal/Arq)      │
  └──────────────────┘                │  - NDA generation    │
                                      │  - Tri-Match batch   │
                                      │  - Lead re-scoring   │
                                      └──────┬───────────────┘
                                             │
                                             ▼
                                      ┌──────────────────┐
                                      │ S3 / Object Store│
                                      │ - PDF documents  │
                                      │ - Cold archives  │
                                      └──────────────────┘

External LLM endpoints (HTTPS, outbound only):
  - api.anthropic.com         → Haiku, Sonnet
  - api.openai.com            → GPT-5.4 mini
  - <internal>:8000           → DeepSeek V3 (self-hosted)

Cross-cutting:
  - OpenTelemetry collector → Tempo
  - Prometheus              → Grafana
  - Loki                    → log aggregation
  - Sentry                  → exceptions
  - Vault / Secrets Manager → API keys, document signing keys
```

---

## 4. SLAs, SLOs, and Performance Budgets

### 4.1 User-facing SLAs

| SLA | Target | Measurement window | Penalty for breach |
|---|---|---|---|
| Availability | 99.5% | Monthly | Internal escalation |
| First-token latency | p95 < 1.5s | Daily rolling | Engineering investigation |
| Full-response latency | p95 < 5s | Daily rolling | Engineering investigation |
| Document delivery | < 60s after request | Per-event | Manual fallback |

### 4.2 Component SLOs

| Component | p50 | p95 | p99 | Error budget |
|---|---|---|---|---|
| Language guard | 5ms | 30ms | 50ms | 0.1% |
| Preprocessor | 25ms | 50ms | 100ms | 0.5% |
| Tri-Match | 10ms | 30ms | 60ms | 0.5% |
| LLM ensemble (with quorum) | 500ms | 1.2s | 2.5s | 1% |
| Extraction | 400ms | 1.0s | 2.0s | 1% |
| Decision Layer | 5ms | 15ms | 30ms | 0.1% |
| RAG retrieval | 80ms | 250ms | 500ms | 1% |
| Sonnet generation (full) | 1.5s | 3.5s | 6.0s | 1% |
| Pricing/Timeline tool | 100ms | 1.0s | 2.5s | 2% |
| Portfolio tool | 5ms | 20ms | 50ms | 0.5% |
| Document generation | 2s | 8s | 20s | 5% |
| Per-tool dispatcher overhead | 1ms | 3ms | 5ms | — |

### 4.3 Per-turn latency budget

The full per-turn budget targeting p95 < 5s end-to-end:

```
Phase 1 (Pre-flight, parallel)        :   30ms
Phase 2 (Tri-Match)                    :   30ms (in parallel with Phase 3)
Phase 3 (LLM ensemble + extraction +RAG):  500ms (parallel, bounded by quorum)
Phase 4 (Decision Layer)               :    5ms
Phase 5 (Routing decision)             :    5ms
Phase 6 (Sonnet generation)            : 2,500ms (streamed; first token <1.5s)
Phase 7 (Format + send first bubble)   :   50ms
                                       ────────
Total to first user-visible bubble     : ~3,100ms (under 5s p95 SLA)
                                       
Phase 8 (Post-response work)           :  Async, non-blocking
```

### 4.4 Cost budget

| Budget | Target | Alert threshold | Action on breach |
|---|---|---|---|
| Per-turn LLM cost | $0.028 launch / $0.026 mature | $0.040 | Investigate prompt regression |
| Per-conversation cost | $0.85 avg | $1.50 | Investigate context bloat |
| Daily LLM spend | $65 / day target | $85 / day | On-call investigation |
| Monthly LLM spend | $2,000 / month target | $2,600 / month | Engineering deep-dive |
| Cache hit rate | ≥ 90% steady state | < 80% | Investigate cache invalidation |

---

## 5. Per-Turn Request Flow

This is the canonical flow for every inbound user message, with latency annotations.

```
┌─────────────────────────────────────────────────────────────────────┐
│ Inbound user message arrives via WebSocket                          │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│ PHASE 1 — Pre-flight (parallel, ~30ms total)                        │
├─────────────────────────────────────────────────────────────────────┤
│  ├─ Language guard (lingua-py + ASCII heuristic)         [§6.3]     │
│  │     └─ if non-English: short-circuit → polite redirect           │
│  ├─ Thread state load (Redis hot → Postgres fallback)    [§6.1]     │
│  ├─ Preprocessor.process() → ProcessedMessage           [§6.13]     │
│  │     ├─ spaCy tokens, lemmas, negation spans                      │
│  │     ├─ deterministic atoms (email/phone/dates/currency)          │
│  │     └─ ONE TEI embedding (reused 4-5x downstream)                │
│  └─ TRG: load hot graph (last 12 turns), build TRGContext [§6.2]   │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│ PHASE 2 — Tri-Match classifier (parallel, ~5–30ms)                  │
├─────────────────────────────────────────────────────────────────────┤
│  Tri-Match.classify(processed_message, trg_context)      [§6.4]     │
│      ├─ Lexical + pattern matchers (token-level evidence)           │
│      ├─ Semantic matcher (sentence-level via TEI)                   │
│      ├─ Evidence aggregation with TRG-context-conditional rules     │
│      └─ Repetition-damped confidence (TRG counter feeds modifier)   │
│                                                                     │
│  TRIMATCH_MODE check:                                               │
│      ├─ "shadow" (default at launch) → continues to Phase 3         │
│      └─ "shortcut_enabled" + confidence ≥ 0.95 + layer ∈            │
│        {exact, regex} → SHORTCUT (Phase 5+ rollout only)            │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│ PHASE 3 — Parallel LLM and retrieval (~500ms, race-with-quorum)     │
├─────────────────────────────────────────────────────────────────────┤
│  Concurrent (asyncio.gather):                                       │
│  ├─ Claude Haiku 4.5     ──┐                                        │
│  ├─ GPT-5.4 mini         ──┤  Race-with-quorum (2 of 3) [§6.4]      │
│  ├─ DeepSeek V3          ──┘  Per-vendor timeouts                   │
│  ├─ Extraction (Haiku) — Q&A + metadata + state deltas   [§6.5]     │
│  └─ RAG retrieval (ES hybrid, top-k=8 chunks)            [§7.1]     │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│ PHASE 4 — Intent Decision Layer (~5ms)                              │
├─────────────────────────────────────────────────────────────────────┤
│  Decide({trimatch_vote, llm_votes, weights})            [§6.11]     │
│      ├─ Weighted voting per dimension (query, service, funnel)      │
│      ├─ Validate stage transition against current funnel stage      │
│      ├─ Apply needs_clarification gate                              │
│      └─ Emit final IntentClassification + audit trail               │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│ PHASE 5 — Routing decision                                          │
├─────────────────────────────────────────────────────────────────────┤
│  Branch on intent + extraction:                                     │
│      ├─ needs_clarification → return clarifying question            │
│      ├─ pricing/timeline → invoke MCP tool                [§6.7]    │
│      ├─ portfolio request → invoke MCP tool               [§6.9]    │
│      ├─ NDA/agreement (gated) → Document Engine MCP       [§6.8]    │
│      ├─ greeting (high conf) → templated response                   │
│      └─ Else → response generation                                  │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│ PHASE 6 — Sonnet response generation (~2.5s, streamed)              │
├─────────────────────────────────────────────────────────────────────┤
│  Sonnet 4.6 with cached system prompt + RAG + intent +    [§6.6]    │
│  state + TRG context + extraction                                   │
│  Streaming begins as tokens arrive                                  │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│ PHASE 7 — Format + stream to user (deterministic, <50ms/bubble)     │
├─────────────────────────────────────────────────────────────────────┤
│  Sanitize → paragraph split → bubble chunk → rich segments [§6.6]   │
│  Humanized inter-bubble pacing (180ms/word + bonuses)               │
│  Typing indicator emitted during inter-bubble delays                │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
                                  ▼
       ──────◄── USER SEES FIRST BUBBLE HERE (~3.1s p95) ──◄──
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│ PHASE 8 — Post-response work (async, fire-and-forget)               │
├─────────────────────────────────────────────────────────────────────┤
│  Parallel:                                                          │
│  ├─ TRG: add bot node, classify relation, compute compliance        │
│  ├─ TRG: trigger compaction if hot graph > 24 nodes                 │
│  ├─ Apply extraction state deltas via narrow MCP tools              │
│  ├─ Append IntentClassificationLog                                  │
│  ├─ Append ThreadEvent (hash-chained)                               │
│  └─ Update Tri-Match calibration counters                           │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 6. Component Reference

Each component follows a standardized format: purpose, build phase, SLO, architecture summary, schema reference, integration points, failure modes, locked decisions.

### 6.1 Component 1 — Thread State & Storage

- **Purpose:** Authoritative store for conversation state, customer identity, and audit trail
- **Build phase:** Phase 1 (foundation)
- **SLO:** read p95 < 5ms (Redis hit), p95 < 50ms (Postgres fallback); write p95 < 30ms

#### Architecture

Three-tier storage with write-through caching:

- **Postgres 16** — authoritative, JSONB for flexible state, typed columns for indexed queries
- **Redis 7** — hot cache, write-through, 24h TTL on inactive threads
- **S3 cold archive** — thread events older than 90 days, parquet partitions

Identity is normalized into a separate `customers` table; threads link to customers with `merged_into_id` for de-dup of duplicate customer records.

#### Key tables

See **Appendix B** for full DDL. Summary:

| Table | Purpose | Partitioning |
|---|---|---|
| `customers` | Identity, lifetime aggregates | None |
| `threads` | Materialized state, optimistic locking | None |
| `thread_events` | Append-only, hash-chained audit | Monthly by `created_at` |
| `intent_classifications` | Full ensemble vote capture per turn | Monthly |
| `tool_invocation_logs` | Every dispatcher invocation | Monthly |
| `trimatch_rules` | Rule store with calibration counters | None |
| `deferred_tool_invocations` | Human-review queue | None |
| `graph_nodes` | TRG nodes with embeddings | Monthly |
| `graph_edges` | TRG relations | Monthly |

#### Schema patterns

- `FieldMeta[T]` Pydantic generic — see Appendix A.2
- `Source` enum: `user_stated`, `user_confirmed`, `ai_extracted`, `csr_entered`, `system`
- Auto-upgrading schema migrators on read (D-002)
- Hash-chained event log for tamper evidence (D-001)

#### Concurrency model

Optimistic locking with `version` column. Every read returns version; every write is `UPDATE ... WHERE id = ? AND version = ?`. On conflict, retry with fresh read up to 3 times. The same transaction commits both the materialized state and the event log row, so they cannot diverge.

#### Failure modes

| Failure | Detection | Response |
|---|---|---|
| Postgres primary down | Health check + Sentry | Read from replica; queue writes; alert |
| Redis miss on hot thread | Application log | Hydrate from Postgres on next read; transparent to user |
| Optimistic lock conflict | Application metric | Retry up to 3x; if all fail, error to caller |
| Schema migration error | Application log + Sentry | Halt thread processing; alert; manual intervention |

#### Locked decisions

D-001, D-002, D-003, D-004 — see §12.

---

### 6.2 Component 2 — Temporal Relational Graph (TRG)

- **Purpose:** Conversation-level signals — relations between turns, compliance scoring, repetition detection, outstanding question tracking, escalation triggers
- **Build phase:** Phase 3 (production-ready)
- **SLO:** Hot graph load p95 < 5ms; relation classification p95 < 50ms; compliance scoring p95 < 100ms

#### Architecture

```
              User message arrives
                       │
                       ▼
              ┌──────────────────┐
              │  Embedder (TEI)  │  Reuses ProcessedMessage.embedding
              └────────┬─────────┘
                       │
                       ▼
              ┌──────────────────┐
              │  TRG Service     │
              │                  │
              │  Hot graph load  │  Redis, hash-tagged keys
              │       │          │
              │       ▼          │
              │  3-tier classifier:
              │   1. Cache       │  ~70% steady state
              │   2. Fast path   │  ~25% (cosine + features)
              │   3. Haiku LLM   │  ~5% (cached on text-pair hash)
              │       │          │
              │       ▼          │
              │  Compliance      │  Semantic Q&A matching
              │       │          │
              │       ▼          │
              │  Stage triggers  │  Escalation per stage
              │                  │
              └────────┬─────────┘
                       │
                       ▼ async
       ┌───────────────┴───────────────┐
       ▼                               ▼
  ┌─────────┐                 ┌────────────────────┐
  │ Redis   │                 │ Postgres + pgvector│
  │ Hot     │                 │ Authoritative      │
  └─────────┘                 │ HNSW index         │
                              └────────┬───────────┘
                                       │
                                       ▼ 90+ days
                              ┌────────────────────┐
                              │ S3 parquet archive │
                              └────────────────────┘
```

#### Signals produced per turn

| Signal | Type | Consumed by |
|---|---|---|
| Relation label | One of 14 enum values | Response generator, audit |
| Compliance score | 0-1 float | Response generator (repair signal), escalation |
| Outstanding questions | List | Response generator, extraction |
| Repetition counter | Int per question | Tri-Match (confidence damping), escalation |
| Rolling summary | String | Compaction, response generator context |

#### Compaction strategy

When hot graph exceeds 24 nodes, oldest 12 fold into a `system_summary` node via Haiku batch call. The summary updates `ThreadState.rolling_summary`. Compacted nodes persist in Postgres for replay/audit; only the hot graph in Redis is trimmed.

#### Stage-aware behavior

Per-stage configuration table for compliance threshold, repetition limit, escalation triggers:

```python
STAGE_CONFIGS = {
    INITIAL_INQUIRY: StageConfig(0.80, 0.25, 0.55, unaddressed_alert=5, repetition_alert=4),
    DISCOVERY:       StageConfig(0.78, 0.28, 0.60, unaddressed_alert=4, repetition_alert=3),
    LEAD:            StageConfig(0.75, 0.30, 0.62, unaddressed_alert=3, repetition_alert=3),
    PROPOSAL:        StageConfig(0.72, 0.32, 0.70, unaddressed_alert=2, repetition_alert=2),
    HIGH_INTENT:     StageConfig(0.70, 0.35, 0.72, unaddressed_alert=2, repetition_alert=2),
    SALE:            StageConfig(0.70, 0.35, 0.72, unaddressed_alert=1, repetition_alert=1),
}
```

#### Failure modes

| Failure | Detection | Response |
|---|---|---|
| Embedder unavailable | Health check | Skip TRG for this turn; log; continue without context |
| Compliance LLM call fails | Exception | Skip compliance score; log; relation classification continues |
| Compaction job fails | Job runner alert | Retry up to 3x; manual intervention if persistent |
| Hot graph corruption | Pydantic validation | Rebuild from Postgres; alert |

#### Locked decisions

D-005, D-006, D-007, D-008, D-009 — see §12.

---

### 6.3 Component 3 — Language Guard (English-only)

- **Purpose:** Detect non-English messages and politely redirect; never block legitimate English users
- **Build phase:** Phase 1 (foundation)
- **SLO:** p95 < 30ms

#### Detection cascade

```
1. Cache check (thread.language)
2. Length guard (< 12 chars trusts cache or defaults to "en")
3. ASCII + English-stopword heuristic (~80% English fast path, microseconds)
4. lingua-py against 11 candidate languages (5-30ms)
5. Low-confidence → default to "en" (don't over-block)
```

#### Re-detection cadence

- First 3 turns of any thread (highest detection error rate)
- Every 10th turn thereafter (drift safety check)
- Long messages (>200 chars) when 5+ turns since last check

#### Non-English handling

Hardcoded translations for top 10 languages (es, fr, de, pt, it, zh, ja, ar, hi, ru) — see Appendix H. After 5 consecutive non-English attempts, thread flips to `unqualified` (D-013).

#### Failure modes

| Failure | Detection | Response |
|---|---|---|
| lingua-py error on input | Exception | Default to "en"; log; continue |
| All-emoji or all-punctuation | Length guard | Trust cached language |
| Mixed-language message | Low confidence | Default to "en" (don't over-block) |

#### Locked decisions

D-010, D-011, D-012, D-013 — see §12.

---

### 6.4 Component 4 — Intent Classification

- **Purpose:** Classify each user message across three intent dimensions (service, query, funnel stage) using deterministic engine + multi-LLM ensemble
- **Build phase:** Basic Haiku-only in Phase 2; full ensemble + Tri-Match in Phase 4
- **SLO:** Ensemble p95 < 1.2s with quorum; Tri-Match p95 < 30ms

This component has three sub-components: Tri-Match engine (§6.4.1), LLM ensemble (§6.4.2), Decision Layer (§6.11). Their integration is detailed below.

#### 6.4.1 Tri-Match engine

**State-of-the-art design** (NOT the simpler regex+fuzzy+semantic cascade from the reference document). Five-layer architecture:

| Layer | Purpose | Implementation |
|---|---|---|
| 1. Preprocessing | Tokenize, lemmatize, mark negation | Shared `ProcessedMessage` from §6.13 |
| 2. Lexical matchers | Token-level evidence | Lemma dict lookup |
| 3. Pattern matchers | Sequence patterns | spaCy `Matcher` + `PhraseMatcher` |
| 4. Semantic matcher | Sentence-level | Cosine vs. canonical phrases per intent |
| 5. Aggregation | Weighted scoring | Per-intent score = weighted sum across layers |

**Calibration:** `times_matched`, `times_correct`, `times_overruled` per rule. Empirical precision = `times_correct / times_matched` becomes the rule's actual confidence weight, replacing hardcoded numbers. Rules below 0.85 empirical precision auto-deprecate after 100+ matches.

**TRG integration:** Conditional rules fire based on TRG context (previous bot turn relation, repetition count, outstanding questions). The same "yes" gets different intent depending on what AI just asked.

**Output:** `query_intent`, `service_intent`, and `funnel_stage`. Per D-081, funnel-stage output launches shadow-only with Decision Layer weight 0 and does not directly mutate thread state.

**Mode flag (`.env`-driven):**

```
TRIMATCH_MODE = "shadow"                  # default at launch
TRIMATCH_SHORTCUT_LAYERS = ""             # default empty
# Phase 5+: "exact"
# Then:    "exact,regex"
# Then:    "exact,regex,pattern"
# Never:   includes "semantic" or "fuzzy"
```

**Rule storage:** Postgres `trimatch_rules` table. See Appendix B for DDL. Hot-reload via atomic state swap on signal.

#### 6.4.2 LLM ensemble

Three vendor adapters with unified `LLMClassifier` interface:

| Vendor | Model | Mechanism | Per-vendor timeout |
|---|---|---|---|
| Anthropic | Haiku 4.5 | Tool use + ephemeral cache | 2.5s |
| OpenAI | GPT-5.4 mini | Function calling + automatic cache | 2.5s |
| DeepSeek | V3 (self-hosted) | JSON mode | 4.0s |

**Race-with-quorum:** All three fire concurrently. As soon as 2 of 3 agree on `query.primary` AND `service.primary_service` AND `funnel.stage`, the remaining task is cancelled. This typically saves the 400ms tail latency of DeepSeek.

#### 6.4.3 Combined system prompt

The prompt (~3,500 tokens) defines:
- BookCraft service catalog (9 services with sub-services)
- 18 query intent definitions
- 11 sales funnel stages
- Stickiness rules (don't downgrade without strong evidence)
- Multi-intent extraction rules
- needs_clarification flag semantics

Identical prompt across all three vendors. See Appendix F.1 for the full prompt.

#### Failure modes

| Failure | Detection | Response |
|---|---|---|
| Single LLM vendor down | Per-vendor circuit breaker | Continue with 2 of 3; reduced quality |
| All 3 LLMs down | All circuit breakers open | Tri-Match-only fallback if any match; else rule-based fallback |
| Tri-Match rule fires confidently wrong | Calibration counter | Auto-deprecate after 100 matches with <0.85 precision |
| Stage transition invalid | Decision Layer validator | Pin to current stage; log warning |
| Tool use schema malformed | Pydantic validation | Retry with same vendor; if persistent, drop vote |

#### Locked decisions

D-014 through D-029 — see §12.

---

### 6.5 Component 5 — Combined Extraction

- **Purpose:** Extract questions, personal info, project info, service mentions, commercial signals, sample requests, and consultation requests from each user message
- **Build phase:** Phase 2 (intelligence)
- **SLO:** p95 < 1.0s

#### Architecture

```
Pre-extraction (deterministic atoms + negation)        ~20ms
   │
   ▼
Haiku extraction call (with atoms + state in prompt)   ~800ms
   │
   ▼
Post-extraction TRG cross-reference                    ~20ms
   │
   ▼
ExtractionResult (atoms + LLM extractions + TRG tags)
   │
   ▼ (post-response, async)
StateUpdater applies deltas via narrow MCP tools
```

**Pre-extraction (D-035):** `DeterministicPreExtractor` pulls atoms before Haiku call. Saves output tokens (LLM doesn't restate atoms), improves accuracy on email/phone/dates.

**Post-extraction TRG cross-reference (D-037):** Newly extracted questions matched against TRG outstanding queue via embedding cosine similarity. Threshold 0.85 → flagged as `is_repeat_of`.

**Negation handling (D-036):** Service mentions inside negation spans get `negated=true` flag. StateUpdater records the mention but suppresses auto-escalation of `interest_level`.

#### Schema

See Appendix A.4 for full `ExtractionResult` Pydantic schema. Output sections:

| Section | Purpose | Consumer |
|---|---|---|
| `questions` | Q&A extraction | TRG, response generator |
| `personal` | Name, email, phone, etc. with `confidence_per_field` | StateUpdater |
| `project` | Title, genre, word count, manuscript status | StateUpdater |
| `services` | Sparse service mentions with interest_level | StateUpdater |
| `commercial_signals` | Budget, urgency, decision authority | Lead scoring, funnel |
| `sample_requests` | Cover/interior/audio sample requests | Portfolio MCP routing |
| `consultation` | Calendar booking request | Consultation flow |

#### State application

All extracted fields wrap in `FieldMeta` (Appendix A.2) with provenance. No-overwrite-of-higher-confidence rule prevents low-quality extractions from corrupting state.

Service interest auto-escalation (D-033):
- `mentioned → considering` on non-negated repeat mention
- `considering → committed` requires explicit commercial signal

#### Failure modes

| Failure | Detection | Response |
|---|---|---|
| Haiku call fails | Exception | Empty `ExtractionResult`; rely on next turn for state updates |
| Pydantic validation error | Validation log | Skip the malformed field; log; continue |
| Pre-extractor regex error | Exception | Skip atom extraction for this turn |
| TRG cross-ref fails | Exception | Skip `is_repeat_of` tagging; log |

#### Locked decisions

D-030 through D-037 — see §12.

---

### 6.6 Component 6 — Response Generation + Formatting

- **Purpose:** Produce the user-facing reply
- **Build phase:** Basic Sonnet in Phase 2; full formatting + streaming in Phase 3
- **SLO:** First-token p95 < 1.5s; full response p95 < 3.5s

#### Two-stage architecture

**Stage A — Generation (Sonnet 4.6).** Sonnet receives 7 context blocks: thread state summary, intent classification, TRG context (outstanding questions, last bot relation, repetition signals, compliance), extraction, RAG retrieval, recent turns, current message + atoms. Outputs markdown reply.

**Stage B — Formatting (deterministic, no LLM).** Pure regex/parser code; ~50ms per bubble.

#### Format rules

| Operation | Method | Purpose |
|---|---|---|
| Emoji strip | Unicode range regex | Forbidden by brand voice |
| Special char normalize | Replace map | em-dash, smart quotes, ellipsis → ASCII |
| Bold strip | Regex | Forbidden by brand voice (defensive) |
| Header strip | Line-prefix regex | Forbidden by brand voice (defensive) |
| Paragraph split | Blank-line split + min-length merge | One bubble candidate per paragraph |
| Bubble chunk | 500-char max bubbles | Multi-message pacing |
| Rich segments | Email/URL/phone regex | Frontend styling metadata |

#### Humanized inter-bubble pacing (D-041)

Calculated from previous bubble's word count:

```
delay_ms = transition_ms (600) + word_count × base_ms_per_word (180)
         + (400 if previous ended with "?")
         + (500 if previous_words > 35)
         clamp(800, 7000)
```

Examples:
- Greeting (~9 words): ~2.2s pause
- Service answer (~19 words): ~4.4s pause (+ question bonus if applicable)
- Detailed explanation (~24 words): ~5.4s pause

Typing indicator (`typing_start`/`typing_stop` WebSocket events) emitted during delays.

#### Greeting templates (D-043)

High-confidence greeting messages skip Sonnet entirely. ~$200/month savings at locked scale.

```
{("hi", "hello"): "Hello! How can I help with your book project today?"}
{("thanks", "thank you"): "You're welcome — let me know if you need anything else."}
{("bye", "goodbye"): "Take care! We're here whenever you'd like to continue."}
```

Conditions: greeting intent confidence > 0.9 AND funnel stage NOT in {proposal, high_intent} AND first matching trigger keyword.

#### Streaming protocol

WebSocket message types:
- `typing_start` — emitted at start of inter-bubble delay
- `typing_stop` — emitted just before next bubble appears
- `message_bubble` — payload includes `text`, `rich_segments`, `bubble_index`

Frontend renders typing indicator during typing_start window. Bubble content arrives mid-stream.

#### Tool use

Sonnet invokes tools via Anthropic's tool_use mechanism. Tool selection is context-aware:

| Tool | Available when |
|---|---|
| `get_pricing_quote` | Always |
| `get_timeline_estimate` | Always |
| `get_portfolio_samples` | Always |
| `request_consultation_booking` | Always |
| `generate_nda` | Funnel stage ∈ {LEAD, PROPOSAL, HIGH_INTENT} |
| `generate_service_agreement` | Funnel stage ∈ {HIGH_INTENT, SALE} |

Sonnet's system prompt includes hard rules: never invent prices, never fabricate tool results, never retry failed tools. See Appendix F.3.

#### Failure modes

| Failure | Detection | Response |
|---|---|---|
| Sonnet API timeout | Per-request timeout | Fallback message "I'm taking a moment to think — let me follow up shortly"; queue for retry |
| Stream interruption | WebSocket error | Partial bubble flushed; reconnect protocol |
| Sonnet returns no tool when expected | Application logic | Retry once with explicit prompt; else degrade |
| Format parser exception | Sentry | Send raw Sonnet output (sanitized); alert |

#### Locked decisions

D-040 through D-044 — see §12.

---

### 6.7 Component 7 — Pricing & Timeline Engine Integration

- **Purpose:** MCP tool integration with the existing pricing/timeline engine
- **Build phase:** Phase 3
- **SLO:** Tool call p95 < 1s

#### Two narrow tools

| Tool | Inputs | Outputs |
|---|---|---|
| `get_pricing_quote` | service + sub-services + sizing + modifiers | Range (always), validity, caveats, suggested_phrasing, quote_id |
| `get_timeline_estimate` | service + sizing + urgency | Week range (always), earliest_start_date, caveats, suggested_phrasing, estimate_id |

See Appendix C.1, C.2 for full schemas.

#### Pre-pricing validation (D-047)

Orchestrator refuses to invoke pricing without service + sizing identified. Asks clarifying question instead. Better than a low-confidence quote.

```python
def should_invoke_pricing(state, extraction) -> tuple[bool, str | None]:
    if not state.project.services_discussed and not extraction.services:
        return False, "Which service are you most interested in pricing?"
    if not _has_sizing(state, extraction):
        return False, "How long is the manuscript — roughly how many words or pages?"
    if state.project.category.value == "Fiction" and not state.project.genre.value:
        return False, "What genre is the book?"
    return True, None
```

#### Quote persistence

Issued quotes recorded in `ThreadState.commercial.quotes` list with full event log entry. Customer acceptance auto-detected via TRG `confirms` relation on the next turn (D-048).

#### Engine deployment (D-049)

Wrapped HTTP service. Pricing engine runs as existing internal service; HTTP wrapper exposes it as MCP tool through the dispatcher.

#### Failure modes

| Failure | Detection | Response |
|---|---|---|
| Engine unreachable | Circuit breaker | Fallback message: lead capture instead of fabricated quote |
| Engine timeout | Request timeout | Same as unreachable |
| Engine validation error | Engine response | Treat as `needs_clarification`; ask user follow-up |
| Quote validity expired | Engine logic | New quote required; engine returns `expired` flag |

#### Locked decisions

D-045 through D-049 — see §12.

---

### 6.8 Component 8 — Agreement & NDA Engine

- **Purpose:** Generate and deliver legally binding documents (NDAs, service agreements) with bounded blast radius
- **Build phase:** Phase 6 (final phase, deferred until system stable)
- **SLO:** Document generation p95 < 8s

#### The architectural rule (absolute)

> **The LLM never produces a single character of legal text.**
>
> Every word in every document comes from one of three sources:
> 1. Lawyer-reviewed Jinja2 template prose
> 2. Typed parameters from thread state
> 3. Static enum values from catalog

#### 10-stage pipeline per document

```
1. Trigger detection (intent + funnel stage gate)
2. Build parameters from thread state (deterministic, not LLM-derived)
3. Confidence gate (sources ∈ allowed set, confidence ≥ floor)
4. Idempotency check (SHA-256 of canonical parameters)
5. Render via Jinja2 with StrictUndefined
6. Verifier (separate LLM call, strict yes/no on rendered text vs. state)
7. PDF generation (WeasyPrint) + S3 upload + content hash
8. Persist to thread state (ThreadState.documents)
9. Send to customer via email
10. Append immutable hash-chained event with full forensic payload
```

#### Confidence gate

```python
ALLOWED_SOURCES = {USER_STATED, USER_CONFIRMED, CSR_ENTERED}
CONFIDENCE_FLOOR_NDA = 0.90
CONFIDENCE_FLOOR_AGREEMENT = 0.95

# Gate fails if any field in parameters has:
#   source NOT in ALLOWED_SOURCES, OR
#   confidence < threshold
```

Gate failure → orchestrator generates user confirmation request, document not rendered. On user confirm, source flips to `user_confirmed` and gate passes on retry.

#### Verifier model selection (D-052)

| Document type | Verifier model | Rationale |
|---|---|---|
| NDA | Haiku 4.5 | Lower stakes, cost-efficient |
| Service agreement | Sonnet 4.6 | Higher stakes; ~3× cost worth it |

Verifier returns strict yes/no on whether rendered document matches thread state. Anomalies categorized as `critical` (auto-reject) or `minor` (allow with logging). See Appendix F.4 for full prompt.

#### Idempotency

`compute_idempotency_key(parameters)` is SHA-256 of canonical parameter dict including template version. Identical inputs return cached result. Customer cannot accidentally receive duplicate documents.

#### Retraction window (D-053)

24 hours. STOP signal received within window auto-voids document, flags for CSR follow-up. After 24 hours, document is final.

#### Anomaly detection auto-suspend

The system enters "human review only" mode when any of these alerts fire:

```yaml
- DocumentGenerationVolumeAnomaly: 3σ outside 7-day baseline
- VerifierRejectionRateHigh: > 10% over 1 hour
- SingleCustomerDocumentBurst: > 3 documents in 15 min for one customer
- AgreementWithLowConfidenceInputs: > 0 (should be impossible)
```

#### Phased rollout (D-051)

| Months | NDA mode | Agreement mode |
|---|---|---|
| 1-3 | Manual (CSR sends) | Manual (CSR sends) |
| 4-6 | Verifier-gated (CSR confirms) | Manual |
| 7-11 | Autonomous | Verifier-gated |
| 12+ | Autonomous | Autonomous (after 6 months NDA-clean) |

Each transition requires 60+ days of clean operational data — no exceptions.

#### Document storage retention (D-054)

| Status | Retention |
|---|---|
| Accepted/signed | Indefinite (compliance) |
| Voided | 90 days |
| Verifier-rejected | 30 days |

Bound by overall data retention policy (§7.6).

#### Cost

Per document marginal cost: ~$0.004 (verifier + PDF + S3 + email). At 144 documents/month, ~$0.60/month operating cost. Real cost is engineering + legal review.

#### Failure modes

| Failure | Detection | Response |
|---|---|---|
| Confidence gate fails | Gate check | Pivot to confirmation flow with user |
| Verifier rejects | Verifier output | Flag for human review; do not send |
| PDF render fails | Exception | Retry once; if persistent, alert + manual generation |
| S3 upload fails | Exception | Retry with exponential backoff; queue for retry |
| Email delivery fails | Email provider response | Retry; eventually mark for CSR follow-up |
| Anomaly threshold breached | Prometheus alert | Auto-suspend autonomous mode; require manual ack to resume |

#### Locked decisions

D-050 through D-055 — see §12.

---

### 6.9 Component 9 — Portfolio Request Engine

- **Purpose:** Deliver curated portfolio gallery URLs in response to sample requests
- **Build phase:** Phase 3
- **SLO:** Tool call p95 < 50ms

#### Static galleries (D-056)

Pre-built curated pages in BookCraft's CMS. Map maintained as YAML in version control (D-060). Marketing team owns the map; engineering consumes via file-watcher reload.

#### Cascading specificity

```
Try most specific first: sample_type:category:genre
Fall back: sample_type:category
Fall back: sample_type:default
Final fallback: general:default (always exists)
```

`matched_specificity` field returned to Sonnet — Sonnet honestly frames the tailoring level. Generic gallery isn't pitched as fantasy-specific.

#### Multi-sample requests (D-058)

Up to 3 sample types per turn handled in parallel. Beyond 3, additional requests deferred to next turn.

#### Sample tracking

Delivered URLs recorded in `ThreadState.samples.requests` list. TRG sees subsequent turns reference what's been delivered ("you've already seen our cover gallery, want to look at interiors next?").

#### Failure modes

| Failure | Detection | Response |
|---|---|---|
| Map file missing | File watcher | Alert; serve from cached map |
| Gallery URL 404s | External monitoring | Alert immediately; deploy fix |
| All galleries fall back to default | Specificity metric | Marketing team adds entries |

#### Locked decisions

D-056 through D-060 — see §12.

---

### 6.10 Component 10 — MCP Tool Layer

- **Purpose:** Single dispatcher for all tool invocations across the system
- **Build phase:** Phase 1 (foundation, skeleton); tools added per their phase
- **SLO:** Dispatcher overhead p95 < 3ms

#### Tool inventory

~22 tools across 7 categories. See Appendix C for full catalog.

| Category | Count | Class |
|---|---|---|
| Pricing & timeline | 2 | Read |
| Portfolio | 1 | Read |
| Documents | 3 | Write-gated initially |
| Consultation | 3 | Write-gated initially |
| State updates | 8 | Write-autonomous |
| Lead/CRM | 3 | Write-autonomous |
| Tri-Match admin | 3 | Write-autonomous (admin only) |

#### Dispatcher responsibilities

1. Resolve tool from registry by name + version
2. Validate input against schema (Pydantic)
3. Apply gating policy (ToolClass)
4. Idempotency check (24h Redis cache for write tools — D-062)
5. Circuit breaker check (5 failures, 60s recovery)
6. Execute with timeout + exponential backoff retry
7. Validate output against schema
8. Audit log (every state)

#### Tool versioning (D-064)

Semver-style baked into tool name: `get_pricing_quote.v2`. Parallel versions supported during migration. Sonnet sees only the active version per environment.

#### Deferred queue SLA (D-063)

| Window | SLA |
|---|---|
| Business hours | 4 hours |
| Overnight/weekend | 24 hours |
| Past SLA | Invocation expires; user gets "team will follow up via email" |

#### Tool discovery for Sonnet

Context-filtered tool list per turn. Funnel-stage gates which tools Sonnet sees. State-update tools never exposed to Sonnet — orchestrator-only.

#### No RBAC at launch (D-065)

Uniform CSR permissions in v1. Defer role differentiation until BookCraft scales the team.

#### Failure modes

| Failure | Detection | Response |
|---|---|---|
| Tool not in registry | Lookup miss | Error to caller; alert if Sonnet invoked unknown tool |
| Input validation error | Pydantic | Error to caller; log full payload |
| Output validation error | Pydantic | Error to caller; alert (tool drift) |
| Tool execution timeout | asyncio timeout | Retry per tool config; circuit-break if persistent |
| Circuit breaker open | State check | Fail fast; user gets graceful degradation message |

#### Locked decisions

D-061 through D-065 — see §12.

---

### 6.11 Component 11 — Intent Decision Layer

- **Purpose:** Aggregate Tri-Match + 3 LLM votes into a final intent classification
- **Build phase:** Phase 4 (with full ensemble)
- **SLO:** p95 < 15ms after all votes received

This is an emergent component from the multi-source classification design (§6.4).

#### Source weights (calibrated monthly)

| Source | Initial weight | Notes |
|---|---|---|
| `tri_match` | 0.4 | Initially low; grows with empirical precision |
| `claude_haiku` | 1.0 | — |
| `gpt_5_mini` | 1.0 | — |
| `deepseek_v3` | 0.9 | Slightly lower due to historical variance |

Weights adjusted monthly based on per-source accuracy from `IntentClassificationLog`.

#### Per-dimension voting

Voting happens independently for query, service, and funnel stage. A source's weight contributes to whichever value it voted for. The value with highest weighted score wins.

#### Consensus boost

When 2+ sources agree on a dimension's value, confidence gets +0.05 boost. This rewards convergence and disambiguates close votes.

#### Stage transition validation

Invalid transitions (e.g., `proposal → initial_inquiry`) get pinned to current stage with logged warning. Prevents classifier hallucinations from breaking funnel logic.

```python
ALLOWED_TRANSITIONS = {
    INITIAL_INQUIRY: {INITIAL_INQUIRY, DISCOVERY, LEAD, UNQUALIFIED, VIOLATOR},
    DISCOVERY:       {DISCOVERY, LEAD, PROPOSAL, UNQUALIFIED, VIOLATOR},
    LEAD:            {LEAD, DISCOVERY, PROPOSAL, HIGH_INTENT, UNQUALIFIED, LOST},
    PROPOSAL:        {PROPOSAL, LEAD, HIGH_INTENT, SALE, LOST},
    HIGH_INTENT:     {HIGH_INTENT, PROPOSAL, SALE, CONVERTED, LOST},
    SALE:            {SALE, CONVERTED, LOST},
    CONVERTED:       {CONVERTED},
    LOST:            {LOST, LEAD},          # can re-engage
    UNQUALIFIED:     {UNQUALIFIED, LEAD},
    VIOLATOR:        {VIOLATOR},            # terminal
}
```

#### `needs_clarification` gate

If any source flagged `needs_clarification = true`, the orchestrator skips Sonnet response generation and asks the suggested clarifying question directly. Saves a Sonnet call on ambiguous turns.

#### Failure modes

| Failure | Detection | Response |
|---|---|---|
| All sources fail | Empty vote list | Fallback classification; preserve current funnel stage |
| Stage transition invalid | Validator | Pin to current stage; log; reduce confidence |
| Source weights out of date | Calibration job miss | Use last-known weights; alert |

#### Locked decisions

Captured under §6.4 decisions (D-014 through D-029).

---

### 6.12 Component 12 — Tri-Match Auto-correction Loop

- **Purpose:** Mine LLM ensemble disagreements daily, generate Tri-Match rule suggestions via Sonnet batch calls, evolve the deterministic engine without manual rule writing
- **Build phase:** Phase 5 (after operational data accumulates)

#### Daily schedule

| UTC | Action |
|---|---|
| 03:00 | Mine disagreements from `IntentClassificationLog` (last 24h) |
| 03:15 | Submit Sonnet batch (50% off) with rule suggestion prompts |
| 09:00 | Collect batch results, parse suggestions |
| 09:15 | Hot-reload Tri-Match in production with new auto-approved rules |

#### Two-tier approval

**Auto-approval threshold:**
- `expected_precision >= 0.95`
- AND covers ≥ 10 examples
- AND no false-positive risks flagged
- AND target intent has ≥ 95% empirical precision in existing rules

**Manual queue:** Everything else goes to CSR-reviewable list.

#### Phased rollout

| Day | Auto-correction state |
|---|---|
| 0 | Manual rule writing only; weekly review reports |
| 30 | Sonnet batch suggestions enabled; all suggestions land in pending queue; manual approval required |
| 60+ | Auto-approval enabled with strict gates above |
| Always | Auto-approved rules enter shadow-on-shadow for first 100 matches before counting toward decisions |

#### Empirical calibration

Per-rule counters:
- `times_matched` — every match increments
- `times_correct` — incremented when ensemble final decision matches Tri-Match
- `times_overruled` — incremented when ensemble overrules Tri-Match

Empirical precision = `times_correct / times_matched`. Rules below 0.85 empirical precision auto-deprecate after 100+ matches.

#### Cost

Marginal Sonnet batch cost: ~$15-30/month. Savings from Tri-Match shortcut hits compound over time and exceed this within months.

#### Failure modes

| Failure | Detection | Response |
|---|---|---|
| Disagreement mining returns empty | Job log | Skip batch; alert if persistent (system not learning) |
| Sonnet batch fails | Anthropic API | Retry next day; alert if 3 consecutive failures |
| Auto-approved rule causes mass misclassification | Calibration counters | Auto-deprecate; alert; rollback if widespread |
| Hot-reload fails | Application log | Continue with previous state; alert; manual intervention |

#### Locked decisions

Captured under §6.4 decisions.

---

### 6.13 Component 13 — Shared Preprocessing Layer

- **Purpose:** Single preprocessing pass per turn produces a `ProcessedMessage` artifact consumed by Tri-Match, Extraction, RAG retrieval, TRG, and Response generation context
- **Build phase:** Phase 1 (foundation)
- **SLO:** p95 < 50ms

This is an emergent component from the integration design.

#### Output contract

See Appendix A.5 for full Pydantic schema. Summary:

```python
class ProcessedMessage(BaseModel):
    raw: str
    normalized: str
    tokens: list[TokenInfo]              # with lemmas, POS, negation flags
    negation_spans: list[NegationSpan]
    deterministic_atoms: dict[str, Any]  # email, phone, currency, dates, word_counts
    embedding: list[float]               # 384-dim BGE-small, reused everywhere
    language: str
    char_count: int
```

#### Pipeline

```
1. Unicode normalize (NFKC)
2. spaCy parse → tokens, lemmas, POS tags
3. Negation span detection (custom matcher + dependencies)
4. Deterministic atom extraction:
   - Email: regex + Pydantic EmailStr
   - Phone: phonenumbers library
   - URLs: regex
   - Currency: locale-aware regex
   - Dates: dateparser
   - Word/page counts: pattern match
5. TEI embedding (one call, cached on text hash)
6. Assembly into ProcessedMessage
```

#### Consumers

| Consumer | What it uses |
|---|---|
| Tri-Match | tokens, negation_spans, embedding |
| Extraction | deterministic_atoms, negation_spans |
| LLM ensemble | normalized (in user message context) |
| RAG retrieval | embedding directly (no re-embedding) |
| TRG | embedding for new node, language |
| Response generator | normalized, language for context |

This shared artifact saves ~75ms per turn vs. each component preprocessing independently.

#### Failure modes

| Failure | Detection | Response |
|---|---|---|
| spaCy model not loaded | Startup check | Refuse to start; alert |
| TEI sidecar unavailable | Health check + circuit breaker | Use cached embedding if same text; else fail fast and retry |
| Atom regex error | Exception | Skip that atom; log; continue |

#### Locked decisions

D-066 — see §12.

---

## 7. Cross-Cutting Concerns

### 7.1 Elasticsearch RAG

- **Purpose:** Retrieve relevant context chunks from BookCraft's content corpus
- **SLO:** p95 < 250ms
- **Build phase:** Phase 3

#### Index contents

- Service descriptions and process documentation
- FAQs and policy documents
- Past resolved conversations (anonymized) as exemplars
- Portfolio metadata (titles, descriptions, genres)
- Pricing engine context (capability descriptions, NOT actual prices)

#### Hybrid retrieval

BM25 + dense vector via `dense_vector` field. Reciprocal Rank Fusion (RRF) for ranking. Top-k=8 chunks at 200 tokens each maximum.

#### Embeddings

Reuses BGE-small from TEI sidecar. Document corpus embeddings precomputed at ingest; query embeddings reused from `ProcessedMessage`.

#### Token budget

Hard cap: 1,600 tokens (8 chunks × 200 tokens). Prevents context bloat into Sonnet.

### 7.2 MCP Tool Layer

Covered in §6.10. Cross-cutting because every component that talks to external systems flows through it.

### 7.3 Observability

Three-pillar stack: traces, metrics, logs. See Appendix E for full specification.

#### Key dashboards

| Dashboard | Purpose | Audience |
|---|---|---|
| Cost dashboard | Per-model spend, cache hit rate, daily forecast | Finance, engineering |
| Latency dashboard | Per-component p50/p95/p99 | Engineering |
| Quality dashboard | TRG compliance, intent confidence, fallback rates | Engineering, product |
| Tri-Match dashboard | Shortcut hit rate, rule precision, queue depth | Engineering, product |
| Document dashboard | Generation rate, verifier rejection, queue depth | Engineering, legal |
| Conversation funnel | Stage transitions, conversion rates | Product, sales |

### 7.4 Audit & Compliance

Three layers of forensic record:

| Layer | Purpose | Tamper-evidence |
|---|---|---|
| `thread_events` (hash-chained) | Significant per-thread actions | Yes (SHA-256 chain) |
| `tool_invocation_logs` | Every dispatcher invocation | No (immutable by application) |
| `intent_classifications` | Every ensemble vote, decision | No (immutable by application) |

In a dispute, any document delivered can be reconstructed forensically. The `thread_events` hash chain breaks if any event is retroactively edited.

#### Retention by event type

| Event type | Retention |
|---|---|
| `document_delivered` | Indefinite (legal) |
| `quote_issued`, `quote_accepted` | 7 years (commercial records) |
| `personal_extraction_applied` | 7 years (PII audit) |
| `intent_classification` | 90 days hot, 1 year cold archive |
| `trg_compaction` | 30 days |
| All others | Per `data_retention_policy` table |

### 7.5 Security & Privacy

#### Threat model

| Threat | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Credential leak (LLM API keys) | Medium | High | Vault/Secrets Manager; rotation; least-privilege |
| Prompt injection | High | Medium | Input sanitization; tool gating; strict template rendering |
| PII exfiltration via LLM | Low | High | Zero-retention API mode where available; logged content review |
| Document tampering | Low | High | Hash-chained events; signed PDFs; idempotency keys |
| Conversation hijacking | Low | High | Thread tokens; WebSocket origin validation; CSRF protection |
| DoS / abuse | Medium | Medium | Rate limiting per IP/customer; CDN/WAF; circuit breakers |
| Data residency violation | High (DeepSeek hosted) | High | Self-host DeepSeek (D-026); document mandatory |

#### Encryption

| Data at | Standard | Notes |
|---|---|---|
| Rest (Postgres) | AES-256 | RDS encryption or equivalent |
| Rest (Redis) | AES-256 | Redis encryption at rest |
| Rest (S3) | AES-256 | SSE-S3 or SSE-KMS |
| Rest (logs) | AES-256 | Loki/OpenSearch encryption |
| In transit (all) | TLS 1.3 | Minimum |
| Document signing | RSA-2048 / Ed25519 | Per signed URL |

#### Authentication

- WebSocket connection: thread-scoped JWT, expires after 24h, rotates with new conversation
- API endpoints: Bearer token validated by gateway
- Internal service-to-service: mTLS or service mesh
- Document URLs: time-limited signed URLs (24h default)

#### LLM provider data agreements

| Provider | Data retention | Notes |
|---|---|---|
| Anthropic | Zero-retention enabled via Enterprise tier | Required before launch |
| OpenAI | Zero-retention via Enterprise / API opt-out | Required before launch |
| DeepSeek | Self-hosted | No external data flow |

### 7.6 Data retention

Per data category:

| Category | Active retention | Cold archive | Final deletion |
|---|---|---|---|
| Thread state (active) | While thread active | — | — |
| Thread state (closed) | 90 days hot | 7 years cold | 7 years |
| Customer identity | While customer relationship | 7 years post-close | 10 years total |
| Signed documents | Indefinite | — | Never |
| Voided documents | 90 days | — | 90 days |
| Verifier-rejected docs | 30 days | — | 30 days |
| TRG events | 30 days hot | 90 days cold | 1 year |
| Tool invocation logs | 30 days hot | 1 year cold | 1 year |
| Intent classifications | 90 days hot | 1 year cold | 1 year |

### 7.7 PII Handling

#### Identification

PII fields in this system: customer name, email, phone, project title (sometimes contains author identity), project synopsis (contains creative content). Treated with elevated care per data category.

#### Access controls

- Application access: via authenticated thread context
- Database direct access: restricted to ops team with audit logging
- LLM provider access: per provider agreement (zero-retention)
- Log scrubbing: PII fields hashed in application logs (full values only in `thread_events` for audit)

#### Customer rights

- **Export:** Full thread + state + extraction history available via authenticated request
- **Deletion (right to be forgotten):** Cascade delete from threads, customers, events; retain only minimum legal records (signed documents)
- **Correction:** CSR-driven via admin tools

---

## 8. Operational Excellence

### 8.1 Testing Strategy

Coverage targets per layer:

| Test type | Target coverage | Frequency | Tooling |
|---|---|---|---|
| Unit tests | 80%+ | Every commit | pytest |
| Integration tests | Key paths | Every commit | pytest + testcontainers |
| Contract tests (MCP tools) | All tools | Every commit | pytest + JSON Schema validation |
| Property-based tests (state machine) | All transitions | Every commit | hypothesis |
| Load tests | Per phase launch | Per release | k6 or Locust |
| Chaos engineering | Vendor outages | Quarterly | Custom chaos toolkit |
| Eval harness (intent quality) | Labeled corpus | Weekly | Custom + Anthropic eval framework |
| Document generation regression | Golden samples | Per template change | Custom |

#### Eval harness for intent classification

Maintains a labeled corpus of ~500-1,000 example messages with expected `IntentClassification`. Run weekly against current production prompts. Catches:
- Prompt regressions (any drop in accuracy → alert)
- Classifier drift (single-source accuracy trending down)
- New failure modes (failures clustered in new patterns)

#### Document generation regression

For each template version, maintain a "golden" parameter set with expected rendered output (text + content hash). On any template change or library version bump, regenerate and compare hashes. Any change requires explicit lawyer sign-off.

### 8.2 Deployment & CI/CD

#### Branching strategy

- `main` — always deployable
- `release/*` — release candidates
- Feature branches — short-lived, rebased

#### CI checks (per commit)

1. Lint (ruff)
2. Type-check (mypy strict)
3. Unit tests (pytest)
4. Integration tests
5. Contract tests
6. Security scan (Snyk or equivalent)
7. Secret scanning (gitleaks)
8. Coverage report

#### CD strategy

- **Application tier:** Blue/green deployment per environment
- **Database migrations:** Forward-only; expand-contract pattern; tested in staging
- **LLM prompt updates:** Feature-flagged; A/B tested via shadow mode for 24h before promotion
- **Tri-Match rules:** Hot-reload (no deployment); approved rules from queue auto-applied per schedule

#### Feature flags

Tri-Match shortcut layers, autonomous document modes, new LLM ensemble vendors — all flag-controlled via environment variables (no code change to flip).

#### Database migration strategy

Expand-contract for backward-incompatible changes:

1. **Expand:** New schema deployed, code reads/writes both old and new
2. **Backfill:** Migrate existing data
3. **Cut over:** Code reads/writes only new schema
4. **Contract:** Drop old schema

`ThreadState.schema_version` migrators run on read (auto-upgrade); explicit DDL for table-level changes.

### 8.3 Monitoring & Alerting

See Appendix E for full specification. Tiered alerting:

| Severity | Examples | Response |
|---|---|---|
| **P0 (page)** | Service down, document misgenerated, mass classifier failure | On-call page; 15-min response SLA |
| **P1 (urgent)** | Single LLM vendor down, high cost overrun, queue backlog | Slack alert; 1-hour response |
| **P2 (degraded)** | Cache hit rate drop, latency drift, single-source classifier accuracy drop | Daily review; 24-hour response |
| **P3 (informational)** | Schema migration completed, weekly summary, threshold approached | Email digest |

### 8.4 Incident Response

#### Severity classification

| Severity | Definition | Owner |
|---|---|---|
| SEV-1 | Service outage or document misgeneration | Engineering on-call + incident commander |
| SEV-2 | Significant feature degradation (e.g., one LLM down, ensemble degraded) | Engineering on-call |
| SEV-3 | Minor degradation (slow path, increased fallback rate) | Engineering team |
| SEV-4 | Cosmetic or low-impact bug | Normal sprint |

#### Incident playbook structure

Each playbook covers:
1. Detection (how we know)
2. Triage (severity assessment)
3. Mitigation (immediate actions)
4. Resolution (root cause fix)
5. Postmortem template

Playbooks for: vendor outage, document misgeneration, mass classifier failure, cost spike, data corruption, prompt injection attack.

### 8.5 Disaster Recovery

#### RPO/RTO targets

| Data | RPO | RTO |
|---|---|---|
| Postgres (state, events) | 5 minutes | 30 minutes |
| Redis (cache only) | N/A — rebuildable | 5 minutes |
| Elasticsearch (RAG) | 1 hour | 1 hour |
| S3 documents | 0 (cross-region replication) | 5 minutes |
| Application code | N/A | 5 minutes (deploy) |

#### Backup strategy

- **Postgres:** Continuous WAL archiving to S3; point-in-time recovery
- **Redis:** Snapshot every 6 hours (cache only; data is rebuildable)
- **S3:** Versioning enabled; cross-region replication
- **Configuration:** Git-versioned; deployable from any commit

#### DR exercises

Quarterly: simulate primary region outage, fail over to secondary, verify data integrity, measure actual RTO/RPO. Update procedures based on findings.

### 8.6 Capacity Planning

Per the §2.4 capacity targets, planned scaling:

| Resource | Trigger to scale | Action |
|---|---|---|
| App workers | CPU > 70% sustained | Add 1 worker |
| Postgres connections | > 80% pool utilization | Scale pgbouncer pool, then upgrade instance |
| Redis memory | > 70% | Upgrade instance or shift to cluster mode |
| ES storage | > 70% | Add data node |
| TEI throughput | p95 > 50ms sustained | Add instance, load balance |
| Anthropic rate limit | > 80% utilization sustained | Request tier increase 2 weeks ahead |
| OpenAI rate limit | Same | Same |

---

## 9. Build Sequence

### 9.1 Phase overview

| Phase | Weeks | Focus | Components | Success criteria |
|---|---|---|---|---|
| 1 — Foundation | 1-4 | Storage, identity, dispatcher skeleton, preprocessing | 1, 3, 10 (skeleton), 13 | Thread state CRUD; events log correctly; preprocessor produces ProcessedMessage |
| 2 — Intelligence | 5-8 | Single-LLM intelligence stack | 4 (basic), 5, 6 (basic) | End-to-end conversation works on Haiku-only intent + extraction + Sonnet response |
| 3 — Production-ready | 9-12 | Streaming, TRG, integration tools | 6 (full), 2, 7, 9 | Streaming chat with full state; pricing/portfolio working; soft-launch candidate |
| 4 — Ensemble + Tri-Match | 13-16 | 3-LLM ensemble + Tri-Match shadow | 4 (revised), 11, 12 (basic) | Tri-Match votes alongside ensemble; calibration data accumulating |
| 5 — Self-improvement | Week 17+ | Auto-correction loop | 12 (full) | Sonnet batch suggestions; Day 30 manual approval; Day 60+ auto-approval |
| 6 — High-stakes | Months 7-12+ | Document generation | 8 | Manual → verifier-gated → autonomous progression |

### 9.2 Phase acceptance criteria

#### Phase 1
- [ ] Postgres schema deployed; all tables created
- [ ] FieldMeta provenance flow tested end-to-end
- [ ] Hash-chained event log verified (manual tampering test)
- [ ] Schema migration framework working
- [ ] Language guard passes 95%+ accuracy on labeled set
- [ ] Preprocessor produces complete ProcessedMessage with all fields
- [ ] Tool dispatcher framework deployed (no tools yet)

#### Phase 2
- [ ] Single Haiku intent classification deployed
- [ ] Combined extraction with FieldMeta state updates working
- [ ] Sonnet response generation producing valid markdown
- [ ] End-to-end conversation works (chat → response)
- [ ] Eval harness baseline established

#### Phase 3
- [ ] WebSocket streaming with humanized pacing deployed
- [ ] TRG operational with relations, compliance, compaction
- [ ] Pricing engine HTTP wrapper deployed
- [ ] Portfolio map deployed with first 30+ entries
- [ ] Soft-launch ready: internal team can demo full conversation
- [ ] All Phase 1-3 metrics within SLO

#### Phase 4
- [ ] OpenAI GPT-5.4 mini integrated and voting
- [ ] DeepSeek V3 self-hosted and voting
- [ ] Decision Layer aggregating 4 sources
- [ ] Tri-Match shadow mode shipping
- [ ] Rule write/read APIs deployed
- [ ] First Tri-Match rules manually authored

#### Phase 5
- [ ] Daily disagreement mining job running
- [ ] Sonnet batch suggestion submission verified
- [ ] Manual approval queue operational
- [ ] Day 60 milestone: Auto-approval enabled with strict gates
- [ ] Tri-Match rule corpus growing organically

#### Phase 6
- [ ] NDA template lawyer-reviewed and frozen
- [ ] Service agreement template lawyer-reviewed and frozen
- [ ] Document generation pipeline deployed (manual mode)
- [ ] Verifier integrated and rejecting bad inputs
- [ ] CSR admin UI deployed (separate frontend track)
- [ ] Phased autonomous rollout begins per D-051

---

## 10. Cost Model

### 10.1 Per-turn cost breakdown

#### At launch (Tri-Match shadow, hit rate 0%)

```
INTENT (3-LLM ensemble):
  Haiku 4.5:           $0.00244
  GPT-5.4 mini:        $0.00078
  DeepSeek V3:         $0.0001 (compute, self-hosted)
  ─────────────────────────────
  Subtotal:             $0.0033

EXTRACTION (Haiku 4.5):
  Cached input:  3,800 × $0.10/M  = $0.00038
  Fresh input:     780 × $1.00/M  = $0.00078
  Output:          400 × $5.00/M  = $0.00200
  ─────────────────────────────
  Subtotal:             $0.00316

RESPONSE GENERATION (Sonnet 4.6):
  Cached input:  2,500 × $0.30/M  = $0.00075
  Fresh input:   7,000 × $3.00/M  = $0.02100
  Output:          400 × $15.00/M = $0.00600
  ─────────────────────────────
  Subtotal:             $0.02775

TRG (amortized):
  Relation classification:  $0.00005
  Compaction:               $0.00021
  ─────────────────────────────
  Subtotal:             $0.00026

Cache write amortization:    $0.00021
NDA + agreement (3% conv):   $0.00011
─────────────────────────────────────
TOTAL PER TURN:               $0.0282
```

#### At maturity (Tri-Match shortcut hit rate 55%)

```
INTENT (45% ensemble, 55% Tri-Match shortcut):
  Effective cost:                    $0.0015

EXTRACTION (with pre-extraction + TRG):
  Subtotal:                          $0.00298

RESPONSE GENERATION (Sonnet, unchanged):
  Subtotal:                          $0.02100

TRG, cache, documents (unchanged):
  Subtotal:                          $0.00058

TOTAL PER TURN:                       $0.0258
```

### 10.2 Monthly projections

```
At launch volume (72,000 turns/month):
  Launch:    72,000 × $0.0282 = $2,030
  Maturing:  72,000 × $0.0270 = $1,944
  Mature:    72,000 × $0.0258 = $1,857
```

### 10.3 Sensitivity analysis

| Variable | Δ from baseline | Monthly impact |
|---|---|---|
| Conversations doubled | +100% | +$2,030 (linear) |
| Avg turns +10 | +33% | +$680 |
| Cache hit rate drops to 70% | -20pp | +$200 |
| Output tokens +50% (verbosity drift) | +50% | +$300 |
| RAG context +50% | +50% | +$700 |
| Tri-Match shortcut at 70% (vs. 55%) | +15pp | -$60 |
| Sonnet swapped for Opus 4.7 | — | +$1,300 |
| All output via Batch API (would not work for chat) | -50% | -$1,000 (theoretical) |

### 10.4 Cost optimization roadmap

Ranked by impact:

| # | Lever | Phase | Estimated savings/mo |
|---|---|---|---|
| 1 | Prompt caching (designed in) | Already done | $1,200 baseline |
| 2 | Trim RAG context to top-5 if quality permits | Post-launch eval | $300-500 |
| 3 | Cap Sonnet output to 600 tokens | Phase 3 | $0 (cap, not save) |
| 4 | Greeting templates | Phase 3 | $200 |
| 5 | Tri-Match shortcut promotion (per layer) | Phase 5+ | $200+ |
| 6 | Run analytics via Batch API | Phase 5 | $50 |
| 7 | Self-host TEI from day 1 | Phase 1 | (avoided cost) |

---

## 11. Risk Register

Each risk: severity (S), likelihood (L), detection signal, response, owner.

| ID | Risk | S | L | Detection | Response | Owner |
|---|---|---|---|---|---|---|
| R-001 | LLM vendor outage | M | M | Circuit breaker open | Race-with-quorum continues with 2; alerts | Eng on-call |
| R-002 | DeepSeek data sovereignty (CN-hosted) | H | H | Code review | Self-host V3 mandatory (D-026) | Architecture |
| R-003 | NDA/agreement misgeneration | C | L | Verifier reject + anomaly detection | Auto-suspend; manual review; phased rollout | Legal + Eng |
| R-004 | Rule poisoning (bad Tri-Match rule) | H | M | Empirical precision counters | Auto-deprecate; conservative auto-approval | Eng |
| R-005 | Cost overrun | M | L | Daily spend alert at 1.3× | Investigate prompt regression / verbosity | Eng + Finance |
| R-006 | Tri-Match plateau (~70% ceiling) | L | H | Calibration trends | Acknowledge ceiling; ensemble carries rest | Eng |
| R-007 | TRG compaction failures | M | L | Job alert; graph size outliers | Idempotent retries; manual intervention | Eng on-call |
| R-008 | Thread state corruption | H | VL | Pydantic validation; locking errors | Reconstruct from event log | Eng on-call |
| R-009 | Customer disputes delivered document | H | L | CSR escalation; legal notice | Hash audit; FieldMeta provenance; retraction window | Legal |
| R-010 | Operational complexity (3 vendors, 13 components) | M | M | Incident frequency | Centralized MCP; unified observability; runbooks | Eng leadership |
| R-011 | Pricing engine downtime | M | L | Circuit breaker | Lead-capture fallback (no fabricated prices) | Eng |
| R-012 | Prompt injection attack | H | M | Anomaly in classifier output | Input sanitization; tool gating; audit trail | Security |
| R-013 | Schema migration breaks production | H | L | Health check + Sentry | Forward-only migrations; staging validation | Eng |
| R-014 | Anthropic rate limit hit | M | L | API 429 errors | Tier upgrade; circuit breaker; degraded service | Eng |
| R-015 | Embedding service unavailable | M | L | Health check | TRG and pre-extraction skip; degraded TEI | Eng |
| R-016 | Cache poisoning | H | VL | Audit log review | Invalidate cache; alert; investigate | Security |
| R-017 | PII leak through LLM logs | H | M | Quarterly audit | Zero-retention API mode; log scrubbing | Security |
| R-018 | Brand voice drift | M | M | Eval harness; manual review | Lock prompt; A/B test changes; marketing review | Marketing + Eng |
| R-019 | CSR queue overflow (deferred tools) | M | M | Queue depth metric | Alert on depth; workflow review | Operations |
| R-020 | Test coverage gap → production bug | M | M | Bug report frequency | Coverage gates; mandatory PR review | Eng |

S/L scale: VL=Very Low, L=Low, M=Medium, H=High, C=Critical

---

## 12. Decision Ledger

Every locked decision, numbered for cross-reference.

### Component 1 — Thread State

- **D-001:** Hash-chained event log. *Rationale:* Tamper-evident audit for autonomous document generation.
- **D-002:** Auto-upgrade with versioned migrators on read. *Rationale:* No batch migration needed; threads heal as touched.
- **D-003:** Separate `customers` table. *Rationale:* Multi-thread customer support; identity normalization.
- **D-004:** Soft-delete with retention policies. *Rationale:* Compliance retention while honoring deletion requests.

### Component 2 — TRG

- **D-005:** Embedding model: BGE-small (English-only, 384-dim).
- **D-006:** Deploy embeddings via TEI sidecar.
- **D-007:** pgvector index: HNSW (m=16, ef_construction=64).
- **D-008:** Hot node limit: 24, compact 12 (defaults adjustable).
- **D-009:** Relation cache TTL: 24 hours.

### Component 3 — Language Guard

- **D-010:** Candidate language list: 11 languages (en, es, fr, de, pt, it, zh, ja, ar, hi, ru).
- **D-011:** Re-detect cadence: first 3 turns + every 10th turn.
- **D-012:** Non-English attempts before unqualified: 5.
- **D-013:** Strict redirect, no auto-translation.

### Component 4 — Intent Classification

- **D-014:** Multi-intent support via `secondary` list (2+ supported).
- **D-015:** `is_repeat_user`: both thread-state and classifier-judged.
- **D-016:** Prompt cache TTL: ephemeral (5-min).
- **D-017:** Fallback keyword set: BookCraft team provides.
- **D-018:** Explicit `needs_clarification` flag in schema.
- **D-019:** Tri-Match mode: shadow by default.
- **D-020:** `.env`-driven flag (`TRIMATCH_SHORTCUT_LAYERS`) for layer-by-layer shortcut promotion.
- **D-021:** Tri-Match never shortcuts on `semantic` or `fuzzy` layers.
- **D-022:** Superseded by D-081 for funnel-stage output. Tri-Match emits funnel-stage votes in shadow mode with Decision Layer weight 0.
- **D-023:** Tri-Match shortcut threshold: 0.95 (when enabled).
- **D-024:** Tri-Match preprocessing: state-of-the-art (lemmatization, negation, evidence pool), NOT simple regex+fuzzy+semantic cascade.
- **D-025:** OpenAI model: GPT-5.4 mini (not nano).
- **D-026:** DeepSeek deployment: self-hosted V3 (mandatory, not hosted API).
- **D-027:** Quorum size: 2 of 3 LLM agreement.
- **D-028:** Rule auto-approval threshold: precision ≥ 0.95 + 10 examples + no FP risk + target intent precision ≥ 0.95.
- **D-029:** Tri-Match admin UI: NOT in scope (separate frontend track).

### Component 5 — Extraction

- **D-030:** Max tokens: 2048.
- **D-031:** Drop fields below confidence 0.5.
- **D-032:** Auto-escalate `mentioned → considering` on non-negated repeat.
- **D-033:** `considering → committed` requires explicit commercial signal.
- **D-034:** Commercial signals denormalized to thread state.
- **D-035:** Pre-extraction atom set: email, phone, URLs, currency, dates, word counts.
- **D-036:** Negation flag suppresses auto-escalation but records mention.
- **D-037:** TRG repeat-question threshold: 0.85 cosine similarity.

### Component 6 — Response Generation

- **D-040:** Bubble splitting threshold: 500 characters.
- **D-041:** Pacing: humanized (180ms/word + transition + question/length bonuses, ~5s typical).
- **D-042:** Brand voice: marketing team owns prompt.
- **D-043:** Greeting templates: hi/thanks/bye, expand from logs.
- **D-044:** Streaming required from day one.

### Component 7 — Pricing/Timeline

- **D-045:** Always returns a range, never a single number.
- **D-046:** Pre-pricing clarification preferred over low-confidence quote.
- **D-047:** Quote validity: engine-determined.
- **D-048:** TRG-driven auto-acceptance tracking.
- **D-049:** Engine surface: HTTP service wrapped as MCP tool.

### Component 8 — NDA/Agreement

- **D-050:** LLM never produces legal text; templates lawyer-reviewed.
- **D-051:** Phased rollout: manual (months 1-3) → verifier-gated (4-6) → NDA autonomous (7-11) → agreements autonomous (12+).
- **D-052:** Verifier: Sonnet for agreements, Haiku for NDA.
- **D-053:** Confidence floors: 0.90 NDA, 0.95 agreement.
- **D-054:** Retraction window: 24 hours.
- **D-055:** Retention policy: indefinite signed / 90d voided / 30d rejected.

### Component 9 — Portfolio

- **D-056:** Static galleries (not dynamic).
- **D-057:** Initial map: 5-7 entries per service across 9 services (~30-50 total).
- **D-058:** Multi-sample cap: 3 per turn.
- **D-059:** URL-only at launch; attachments deferred.
- **D-060:** Map storage: file in version control.

### Component 10 — MCP Layer

- **D-061:** Code-based tool registry.
- **D-062:** Idempotency cache: 24 hours in Redis.
- **D-063:** Deferred queue SLA: 4h business / 24h overnight/weekend.
- **D-064:** Versioning: semver in tool name.
- **D-065:** No RBAC at launch (uniform CSR permissions in v1).

### Cross-cutting

- **D-066:** Single shared `ProcessedMessage` artifact across components.
- **D-067:** Stack: Python 3.12+ FastAPI + SQLModel + Pydantic + asyncpg + redis-py.
- **D-068:** Self-hosted infrastructure (out of scope for cost projections).
- **D-069:** Full extraction always runs (never skipped on Tri-Match shortcut).

---

## 13. Roadmap & Future Work

### 13.1 Post-launch (months 1-12)

- Phased autonomous rollout per D-051
- Tri-Match shortcut promotion per layer (D-020)
- Cost optimization sweep (D-019, RAG trimming)
- Eval harness expansion (more labeled examples)
- A/B testing for bubble pacing and brand voice variations

### 13.2 Post-maturity (year 2+)

- **Multi-language support.** If non-English redirects exceed 20% of conversations, evaluate full multilingual support.
- **Advanced analytics.** Conversation funnel BI, lead scoring ML model trained on historical data.
- **CSR live takeover.** Seamless handoff from bot to human CSR mid-conversation.
- **Voice channel.** Phone integration if business model expands.
- **Mobile app native client.** If web traffic moves significantly to mobile.
- **Document attachments.** Direct file delivery (not just URLs) for samples.
- **RBAC for CSRs.** When team scales beyond uniform permissions.
- **Customer self-service portal.** Authenticated portal for quote review, agreement signing, project status.

### 13.3 Continuous improvements

- Quarterly Tri-Match calibration review
- Quarterly source weight calibration in Decision Layer
- Quarterly DR exercises
- Annual full architecture review
- Annual security audit + threat model update

---

## 14. Appendices

### Appendix A — Pydantic Schemas

#### A.1 Source enum and FieldMeta

```python
from datetime import datetime, timezone
from enum import StrEnum
from typing import Generic, TypeVar
from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")

class Source(StrEnum):
    USER_STATED       = "user_stated"
    USER_CONFIRMED    = "user_confirmed"
    AI_EXTRACTED      = "ai_extracted"
    CSR_ENTERED       = "csr_entered"
    SYSTEM            = "system"

class FieldMeta(BaseModel, Generic[T]):
    """Wraps any value with confidence + provenance.
    Required for every meaningful state field."""
    model_config = ConfigDict(frozen=False)
    
    value: T | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: Source = Source.SYSTEM
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    extracted_by: str | None = None
    raw_excerpt: str | None = None
    
    def is_high_confidence(self) -> bool:
        return self.source in {Source.USER_STATED, Source.USER_CONFIRMED, Source.CSR_ENTERED} \
               or self.confidence >= 0.95
```

#### A.2 ThreadState

```python
class ThreadState(BaseModel):
    schema_version: int = 1
    personal: PersonalInfo = Field(default_factory=PersonalInfo)
    project: ProjectInfo = Field(default_factory=ProjectInfo)
    origin: OriginInfo = Field(default_factory=OriginInfo)
    samples: SamplesInfo = Field(default_factory=SamplesInfo)
    consultation: ConsultationInfo = Field(default_factory=ConsultationInfo)
    commercial: CommercialInfo = Field(default_factory=CommercialInfo)
    documents: DocumentsInfo = Field(default_factory=DocumentsInfo)
    project_status: ProjectStatusInfo = Field(default_factory=ProjectStatusInfo)
    rolling_summary: str = ""

class PersonalInfo(BaseModel):
    name: FieldMeta[str] = Field(default_factory=FieldMeta)
    email: FieldMeta[EmailStr] = Field(default_factory=FieldMeta)
    phone: FieldMeta[str] = Field(default_factory=FieldMeta)
    preferred_contact_method: FieldMeta[ContactMethod] = Field(default_factory=FieldMeta)
    preferred_contact_time: FieldMeta[ContactTime] = Field(default_factory=FieldMeta)
    timezone: FieldMeta[str] = Field(default_factory=FieldMeta)

class ProjectInfo(BaseModel):
    title: FieldMeta[str] = Field(default_factory=FieldMeta)
    category: FieldMeta[ProjectCategory] = Field(default_factory=FieldMeta)
    genre: FieldMeta[str] = Field(default_factory=FieldMeta)
    sub_genre: FieldMeta[str] = Field(default_factory=FieldMeta)
    synopsis: FieldMeta[str] = Field(default_factory=FieldMeta)
    word_count: FieldMeta[int] = Field(default_factory=FieldMeta)
    pages_count: FieldMeta[int] = Field(default_factory=FieldMeta)
    manuscript_status: FieldMeta[ManuscriptStatus] = Field(default_factory=FieldMeta)
    target_completion_date: FieldMeta[datetime] = Field(default_factory=FieldMeta)
    services_discussed: list[ServiceInterest] = Field(default_factory=list)

# ... ServiceInterest, OriginInfo, SamplesInfo, ConsultationInfo, 
# CommercialInfo, Quote, DocumentsInfo, DocumentRecord, ProjectStatusInfo
# follow same pattern — full schemas in source repository
```

#### A.3 IntentClassification

```python
class IntentClassification(BaseModel):
    service: ServiceIntent
    query: QueryIntent
    funnel: SalesFunnelResult
    is_violator: bool = False
    is_unqualified: bool = False
    overall_confidence: float = Field(ge=0.0, le=1.0)
    classifier_notes: str = ""

class QueryIntent(BaseModel):
    primary: QueryIntentType
    secondary: list[QueryIntentType] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    needs_clarification: bool = False
    clarification_question: str | None = None

class ServiceIntent(BaseModel):
    primary_service: ServiceCategory | None = None
    additional_services: list[ServiceCategory] = Field(default_factory=list)
    sub_services: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)

class SalesFunnelResult(BaseModel):
    stage: SalesStage
    is_repeat_user: bool = False
    transition_reason: str = ""
    confidence: float = Field(ge=0.0, le=1.0)
```

#### A.4 ExtractionResult

```python
class ExtractionResult(BaseModel):
    questions: list[ExtractedQuestion] = Field(default_factory=list)
    personal: PersonalDelta = Field(default_factory=PersonalDelta)
    project: ProjectDelta = Field(default_factory=ProjectDelta)
    services: list[ServiceMention] = Field(default_factory=list)
    commercial_signals: list[CommercialSignal] = Field(default_factory=list)
    sample_requests: list[SampleRequestExtraction] = Field(default_factory=list)
    consultation: ConsultationRequestExtraction = Field(default_factory=ConsultationRequestExtraction)
    extraction_confidence: float = Field(ge=0.0, le=1.0)
    extraction_notes: str = ""
    
    @field_validator('questions')
    def cap_questions(cls, v):
        return v[:5]

class ExtractedQuestion(BaseModel):
    text: str
    canonical_form: str
    question_type: Literal["factual", "decision", "process", "scope", "confirmation", "clarification"]
    related_to: Literal["service", "pricing", "timeline", "process", "samples", "policy", "company", "other"]
    confidence: float
    is_repeat_of: UUID | None = None       # set by post-extraction TRG cross-ref
    repeat_count: int | None = None
```

#### A.5 ProcessedMessage

```python
class TokenInfo(BaseModel):
    text: str
    lemma: str
    pos: str
    negated: bool

class NegationSpan(BaseModel):
    start: int      # token index
    end: int        # exclusive
    trigger: str

class ProcessedMessage(BaseModel):
    raw: str
    normalized: str
    tokens: list[TokenInfo]
    negation_spans: list[NegationSpan]
    deterministic_atoms: dict[str, Any]
    embedding: list[float]
    language: str
    char_count: int
    
    def lemmatized_text(self) -> str:
        return " ".join(t.lemma for t in self.tokens)
```

#### A.6 TriMatchResult

```python
class TriMatchLayer(StrEnum):
    EXACT     = "exact"
    REGEX     = "regex"
    KEYWORD   = "keyword"
    FUZZY     = "fuzzy"
    SEMANTIC  = "semantic"
    NONE      = "none"

class TriMatchResult(BaseModel):
    query_intent: QueryIntentType | None = None
    query_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    service_intent: ServiceCategory | None = None
    service_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    sub_services: list[str] = Field(default_factory=list)
    layer: TriMatchLayer = TriMatchLayer.NONE
    matched_patterns: list[str] = Field(default_factory=list)
    overall_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    latency_ms: float = 0.0
```

### Appendix B — Database Schema (DDL)

```sql
-- Customers
CREATE TABLE customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE,
    phone VARCHAR(50),
    name VARCHAR(255),
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    total_threads INT NOT NULL DEFAULT 0,
    total_quotes_value DOUBLE PRECISION NOT NULL DEFAULT 0,
    has_signed_agreement BOOLEAN NOT NULL DEFAULT FALSE,
    merged_into_id UUID REFERENCES customers(id),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ
);
CREATE INDEX ix_customers_email ON customers(email) WHERE deleted_at IS NULL;
CREATE INDEX ix_customers_phone ON customers(phone) WHERE deleted_at IS NULL;
CREATE INDEX ix_customers_signed ON customers(has_signed_agreement) WHERE deleted_at IS NULL;

-- Threads
CREATE TABLE threads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID REFERENCES customers(id),
    sales_stage VARCHAR(32) NOT NULL,
    priority VARCHAR(16) NOT NULL DEFAULT 'medium',
    language VARCHAR(8) NOT NULL DEFAULT 'en',
    is_lead_created BOOLEAN NOT NULL DEFAULT FALSE,
    is_escalated BOOLEAN NOT NULL DEFAULT FALSE,
    version INT NOT NULL DEFAULT 0,
    turn_count INT NOT NULL DEFAULT 0,
    last_redetect_turn INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_message_at TIMESTAMPTZ,
    state JSONB NOT NULL DEFAULT '{}'::jsonb,
    deleted_at TIMESTAMPTZ,
    deletion_reason TEXT,
    retention_until TIMESTAMPTZ
);
CREATE INDEX ix_threads_customer ON threads(customer_id);
CREATE INDEX ix_threads_stage ON threads(sales_stage);
CREATE INDEX ix_threads_priority ON threads(priority);
CREATE INDEX ix_threads_active ON threads(last_message_at) 
    WHERE deleted_at IS NULL AND is_escalated = FALSE;
CREATE INDEX ix_threads_customer_stage ON threads(customer_id, sales_stage);

-- Thread events (hash-chained, partitioned monthly)
CREATE TABLE thread_events (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    thread_id UUID NOT NULL,
    sequence BIGINT NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    actor VARCHAR(16) NOT NULL,
    payload JSONB NOT NULL,
    confidence DOUBLE PRECISION,
    prev_hash CHAR(64),
    content_hash CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);
CREATE UNIQUE INDEX ix_thread_events_seq ON thread_events(thread_id, sequence, created_at);
CREATE INDEX ix_thread_events_type ON thread_events(event_type, created_at);
CREATE INDEX ix_thread_events_actor ON thread_events(actor, created_at);

-- Tri-Match rules
CREATE TABLE trimatch_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_type VARCHAR(32) NOT NULL,
    target_dimension VARCHAR(16) NOT NULL,
    target_value VARCHAR(128) NOT NULL,
    pattern TEXT NOT NULL,
    base_confidence DOUBLE PRECISION NOT NULL DEFAULT 0.95,
    source VARCHAR(32) NOT NULL DEFAULT 'manual',
    suggested_by_run_id UUID,
    approval_status VARCHAR(16) NOT NULL DEFAULT 'pending',
    times_matched BIGINT NOT NULL DEFAULT 0,
    times_correct BIGINT NOT NULL DEFAULT 0,
    times_overruled BIGINT NOT NULL DEFAULT 0,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_trimatch_rules_type ON trimatch_rules(rule_type);
CREATE INDEX ix_trimatch_rules_dim ON trimatch_rules(target_dimension);
CREATE INDEX ix_trimatch_rules_value ON trimatch_rules(target_value);
CREATE INDEX ix_trimatch_rules_active ON trimatch_rules(enabled, approval_status);

-- Graph nodes (with pgvector HNSW)
CREATE TABLE graph_nodes (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    thread_id UUID NOT NULL,
    sequence BIGINT NOT NULL,
    node_type VARCHAR(16) NOT NULL,
    text TEXT NOT NULL,
    embedding VECTOR(384) NOT NULL,
    questions JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);
CREATE UNIQUE INDEX ix_graph_nodes_seq ON graph_nodes(thread_id, sequence, created_at);
CREATE INDEX ix_graph_nodes_type ON graph_nodes(node_type, created_at);
CREATE INDEX ix_graph_nodes_hnsw ON graph_nodes 
    USING hnsw (embedding vector_cosine_ops) 
    WITH (m=16, ef_construction=64);

-- Graph edges
CREATE TABLE graph_edges (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    thread_id UUID NOT NULL,
    source_id UUID NOT NULL,
    target_id UUID NOT NULL,
    relation VARCHAR(32) NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    classifier VARCHAR(32) NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);
CREATE INDEX ix_graph_edges_source ON graph_edges(thread_id, source_id, created_at);
CREATE INDEX ix_graph_edges_target ON graph_edges(thread_id, target_id, created_at);
CREATE INDEX ix_graph_edges_rel ON graph_edges(relation, created_at);

-- Intent classifications
CREATE TABLE intent_classifications (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    thread_id UUID NOT NULL,
    turn_sequence INT NOT NULL,
    message_text TEXT NOT NULL,
    votes JSONB NOT NULL,
    trimatch_result JSONB,
    final_decision JSONB NOT NULL,
    trimatch_diverged BOOLEAN NOT NULL DEFAULT FALSE,
    llms_diverged BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);
CREATE INDEX ix_intent_thread ON intent_classifications(thread_id, created_at);
CREATE INDEX ix_intent_diverged ON intent_classifications(trimatch_diverged, created_at);

-- Tool invocation logs
CREATE TABLE tool_invocation_logs (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    correlation_id UUID NOT NULL,
    tool_name VARCHAR(128) NOT NULL,
    thread_id UUID NOT NULL,
    turn_sequence INT NOT NULL,
    invoked_by VARCHAR(32) NOT NULL,
    params_hash CHAR(64) NOT NULL,
    params JSONB NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    duration_ms INT,
    status VARCHAR(16) NOT NULL,
    result JSONB,
    error_kind VARCHAR(64),
    error_detail TEXT,
    PRIMARY KEY (id, started_at)
) PARTITION BY RANGE (started_at);
CREATE INDEX ix_tool_logs_correlation ON tool_invocation_logs(correlation_id);
CREATE INDEX ix_tool_logs_tool ON tool_invocation_logs(tool_name, started_at);
CREATE INDEX ix_tool_logs_thread ON tool_invocation_logs(thread_id, started_at);
CREATE INDEX ix_tool_logs_status ON tool_invocation_logs(status, started_at);

-- Deferred tool invocations (human-review queue)
CREATE TABLE deferred_tool_invocations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    queue_id VARCHAR(64) NOT NULL,
    tool_name VARCHAR(128) NOT NULL,
    thread_id UUID NOT NULL,
    turn_sequence INT NOT NULL,
    params JSONB NOT NULL,
    context JSONB NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    deferred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at TIMESTAMPTZ,
    decided_by VARCHAR(128),
    decision_notes TEXT,
    invocation_result JSONB,
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX ix_deferred_queue ON deferred_tool_invocations(queue_id, status);
CREATE INDEX ix_deferred_tool ON deferred_tool_invocations(tool_name, status);
CREATE INDEX ix_deferred_thread ON deferred_tool_invocations(thread_id);
CREATE INDEX ix_deferred_expires ON deferred_tool_invocations(expires_at) 
    WHERE status = 'pending';

-- Partition management: create initial monthly partitions and 
-- automate partition creation via pg_partman or scheduled job.
```

### Appendix C — MCP Tool Catalog

#### C.1 get_pricing_quote

```python
{
    "name": "get_pricing_quote",
    "version": "v1",
    "tool_class": "read",
    "timeout_seconds": 5.0,
    "max_retries": 2,
    "input_schema": PricingQuoteRequest.model_json_schema(),
    "output_schema": PricingQuoteResponse.model_json_schema(),
}

class PricingQuoteRequest(BaseModel):
    service: ServiceCategory
    sub_services: list[str] = Field(default_factory=list)
    word_count: int | None = None
    page_count: int | None = None
    duration_minutes: int | None = None
    project_category: Literal["Fiction", "Non-Fiction"] | None = None
    genre: str | None = None
    tier: Literal["basic", "standard", "premium"] | None = None
    rush: bool = False
    thread_id: UUID
    extracted_at: datetime
    confidence: float = Field(ge=0.0, le=1.0)
    raw_user_request: str
    
    @model_validator(mode="after")
    def at_least_one_sizing(self):
        if not any([self.word_count, self.page_count, self.duration_minutes]):
            raise ValueError("Must provide at least one sizing parameter")
        return self

class PricingQuoteResponse(BaseModel):
    quote_id: UUID
    service: ServiceCategory
    price_low: float
    price_high: float
    currency: Literal["USD", "EUR", "GBP", "CAD", "AUD"]
    valid_until: datetime
    caveats: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    needs_clarification: list[str] = Field(default_factory=list)
    suggested_phrasing: str
```

#### C.2 get_timeline_estimate

```python
{
    "name": "get_timeline_estimate",
    "version": "v1",
    "tool_class": "read",
    "timeout_seconds": 5.0,
    "max_retries": 2,
}

class TimelineEstimateResponse(BaseModel):
    estimate_id: UUID
    service: ServiceCategory
    weeks_low: int
    weeks_high: int
    earliest_start_date: datetime
    caveats: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    needs_clarification: list[str] = Field(default_factory=list)
    suggested_phrasing: str
```

#### C.3 get_portfolio_samples

```python
{
    "name": "get_portfolio_samples",
    "version": "v1",
    "tool_class": "read",
    "timeout_seconds": 2.0,
    "max_retries": 1,
}

class PortfolioRequest(BaseModel):
    sample_type: Literal["cover", "interior", "trailer", "marketing", "editing", "general"]
    project_category: Literal["Fiction", "Non-Fiction"] | None = None
    genre: str | None = None
    thread_id: UUID
    raw_user_request: str

class PortfolioResponse(BaseModel):
    gallery_url: str
    gallery_name: str
    sample_count: int
    suggested_phrasing: str
    matched_specificity: Literal["sample_type", "sample_type_category", "sample_type_category_genre"]
```

#### C.4 generate_nda

```python
{
    "name": "generate_nda",
    "version": "v1",
    "tool_class": "write_gated",
    "timeout_seconds": 30.0,
    "max_retries": 1,
    "requires_idempotency_key": true,
}

class NDAGenerationRequest(BaseModel):
    customer_name: str
    customer_email: EmailStr
    project_summary: str
    effective_date: datetime
    customer_name_meta: FieldMeta[str]
    customer_email_meta: FieldMeta[EmailStr]
    project_summary_meta: FieldMeta[str]
    thread_id: UUID

class NDAGenerationResponse(BaseModel):
    document_id: UUID
    status: Literal["delivered", "deferred_for_review", "verifier_rejected", "gate_failed"]
    s3_key: str | None = None
    signed_url: str | None = None
    content_hash: str | None = None
    delivered_to: EmailStr | None = None
    delivered_at: datetime | None = None
    rejection_reason: str | None = None
```

#### C.5–C.22

All remaining tools follow the same pattern. For full schemas, see `/src/tools/` in the source repository.

| Tool | Class | Notes |
|---|---|---|
| `generate_service_agreement.v1` | write_gated | Higher confidence floor (0.95) |
| `void_document.v1` | write_autonomous | Marks document voided in state |
| `request_consultation_booking.v1` | write_gated | Calendar integration |
| `update_personal.v1` | write_autonomous | Narrow state-update |
| `update_project.v1` | write_autonomous | Narrow state-update |
| `add_service_interest.v1` | write_autonomous | Sparse service tracking |
| `record_consultation_request.v1` | write_autonomous | Consultation request flag |
| `record_sample_request.v1` | write_autonomous | Sample request flag |
| `record_quote.v1` | write_autonomous | Quote persistence |
| `mark_lead_created.v1` | write_autonomous | Lead creation flag |
| `flag_for_escalation.v1` | write_autonomous | Escalation trigger |
| `create_lead.v1` | write_autonomous | CRM integration |
| `update_lead_status.v1` | write_autonomous | CRM update |
| `notify_csr.v1` | write_autonomous | CSR notification |
| `propose_trimatch_rule.v1` | write_autonomous | Auto-correction loop |
| `approve_trimatch_rule.v1` | write_autonomous | CSR/admin action |
| `deprecate_trimatch_rule.v1` | write_autonomous | Auto-deprecation or admin |

### Appendix D — Environment Configuration

Complete `.env` specification with descriptions and security levels:

```bash
# ─────────────────────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────────────────────
DATABASE_URL=postgresql+asyncpg://user:pass@host/bookcraft
DATABASE_POOL_SIZE=20
DATABASE_MAX_OVERFLOW=10
DATABASE_REPLICA_URL=postgresql+asyncpg://user:pass@replica/bookcraft

# ─────────────────────────────────────────────────────────────
# Redis
# ─────────────────────────────────────────────────────────────
REDIS_URL=redis://host:6379/0
REDIS_HOT_TTL_HOURS=24
REDIS_IDEMPOTENCY_TTL_HOURS=24
REDIS_RELATION_CACHE_TTL_HOURS=24

# ─────────────────────────────────────────────────────────────
# Elasticsearch
# ─────────────────────────────────────────────────────────────
ELASTICSEARCH_URL=https://host:9200
ELASTICSEARCH_USER=elastic
ELASTICSEARCH_PASSWORD=<secret>     # SECURITY: Vault-managed
ELASTICSEARCH_INDEX_PREFIX=bookcraft_

# ─────────────────────────────────────────────────────────────
# TEI Embedder
# ─────────────────────────────────────────────────────────────
TEI_URL=http://tei-sidecar:8080
TEI_TIMEOUT_SECONDS=10
TEI_BATCH_SIZE=128

# ─────────────────────────────────────────────────────────────
# LLM Providers
# ─────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY=<secret>          # SECURITY: Vault-managed
ANTHROPIC_BASE_URL=https://api.anthropic.com
OPENAI_API_KEY=<secret>             # SECURITY: Vault-managed
OPENAI_BASE_URL=https://api.openai.com/v1
DEEPSEEK_BASE_URL=http://deepseek-internal:8000
DEEPSEEK_API_KEY=<internal-token>   # SECURITY: Vault-managed

# ─────────────────────────────────────────────────────────────
# Tri-Match
# ─────────────────────────────────────────────────────────────
TRIMATCH_MODE=shadow                # "shadow" | "shortcut_enabled"
TRIMATCH_SHORTCUT_LAYERS=           # comma-separated: "exact,regex,pattern"
TRIMATCH_SHORTCUT_THRESHOLD=0.95
TRIMATCH_AUTOCORRECT_ENABLED=false  # enable Day 30 manual approval loop
TRIMATCH_AUTOAPPROVE_ENABLED=false  # enable Day 60 auto-approval

# ─────────────────────────────────────────────────────────────
# Document generation
# ─────────────────────────────────────────────────────────────
NDA_MODE=manual                     # "manual" | "verifier_gated" | "autonomous"
AGREEMENT_MODE=manual               # "manual" | "verifier_gated" | "autonomous"
NDA_TEMPLATE_VERSION=v1.0
AGREEMENT_TEMPLATE_VERSION=v1.0
DOCUMENT_RETRACTION_HOURS=24
S3_DOCUMENTS_BUCKET=bookcraft-documents
S3_REGION=us-east-1
DOCUMENT_SIGNED_URL_TTL_HOURS=24

# ─────────────────────────────────────────────────────────────
# Email
# ─────────────────────────────────────────────────────────────
EMAIL_PROVIDER=sendgrid             # "sendgrid" | "ses"
SENDGRID_API_KEY=<secret>           # SECURITY: Vault-managed
EMAIL_FROM_ADDRESS=hello@bookcraft.ai
EMAIL_FROM_NAME=BookCraft AI

# ─────────────────────────────────────────────────────────────
# Observability
# ─────────────────────────────────────────────────────────────
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
SENTRY_DSN=<secret>                 # SECURITY: Vault-managed
SENTRY_ENVIRONMENT=production
LOG_LEVEL=INFO
LOG_FORMAT=json

# ─────────────────────────────────────────────────────────────
# Rate limits and tuning
# ─────────────────────────────────────────────────────────────
INTENT_ENSEMBLE_TIMEOUT_SECONDS=2.5
DEEPSEEK_TIMEOUT_SECONDS=4.0
RAG_TOP_K=8
RAG_MAX_TOKENS_PER_CHUNK=200
SONNET_MAX_TOKENS=600
HAIKU_MAX_TOKENS=2048
SHARED_PROCESSOR_CACHE_SIZE=1000
TRG_HOT_NODES_LIMIT=24
TRG_COMPACT_KEEP=12
TRG_RELATION_FAST_PATH_THRESHOLD=0.85
TRG_COMPLIANCE_THRESHOLD_DEFAULT=0.62

# ─────────────────────────────────────────────────────────────
# Security
# ─────────────────────────────────────────────────────────────
JWT_SIGNING_KEY=<secret>            # SECURITY: Vault-managed
JWT_TTL_HOURS=24
WS_ALLOWED_ORIGINS=https://bookcraft.ai,https://www.bookcraft.ai
RATE_LIMIT_PER_IP_PER_MINUTE=30
```

### Appendix E — Observability Specification

#### E.1 Metrics

Full Prometheus metric specification:

| Metric | Type | Labels | Description |
|---|---|---|---|
| `anthropic_input_tokens_total` | Counter | model, component, kind (cache_read/cache_write/fresh) | LLM input token usage |
| `anthropic_output_tokens_total` | Counter | model, component | LLM output token usage |
| `anthropic_cost_dollars_total` | Counter | model, component | LLM cost |
| `anthropic_call_seconds` | Histogram | model, component | LLM call duration |
| `anthropic_cache_hit_ratio` | Gauge | component | Cache hit ratio |
| `language_detection_seconds` | Histogram | source | Language guard latency |
| `language_detection_results_total` | Counter | language | Detected languages |
| `non_english_redirects_total` | Counter | language | Non-English handling |
| `trimatch_classification_seconds` | Histogram | layer | Tri-Match latency |
| `trimatch_layer_distribution_total` | Counter | layer | Match layer distribution |
| `trimatch_shortcut_total` | Counter | enabled | Shortcuts taken |
| `trimatch_overruled_total` | Counter | rule_id | Decision Layer overrules |
| `intent_classification_seconds` | Histogram | source | Per-source latency |
| `intent_consensus_total` | Counter | quorum_size | Quorum sizes achieved |
| `intent_fallback_total` | Counter | reason | Fallback invocations |
| `intent_invalid_stage_transition_total` | Counter | from, to | Invalid transitions detected |
| `extraction_seconds` | Histogram | — | Extraction duration |
| `extraction_fields_per_turn` | Histogram | category | Fields extracted per turn |
| `extraction_no_overwrite_skips_total` | Counter | field | No-overwrite suppressions |
| `trg_relation_classification_seconds` | Histogram | classifier | TRG classification latency |
| `trg_relation_classifier_distribution_total` | Counter | classifier | Path distribution |
| `trg_compliance_score` | Histogram | sales_stage | Compliance distribution |
| `trg_unaddressed_questions_total` | Counter | sales_stage | Unaddressed questions |
| `trg_escalation_triggers_total` | Counter | reason | Escalations |
| `trg_compaction_total` | Counter | — | Compactions performed |
| `trg_graph_size_nodes` | Histogram | — | Hot graph size |
| `embedder_latency_seconds` | Histogram | — | TEI latency |
| `sonnet_response_seconds` | Histogram | — | Response generation latency |
| `tool_invocations_total` | Counter | tool_name, status | Tool calls |
| `tool_duration_seconds` | Histogram | tool_name | Tool latency |
| `tool_circuit_breaker_state` | Gauge | tool_name | Circuit state (0/1/2) |
| `tool_deferred_queue_depth` | Gauge | tool_name | Deferred queue depth |
| `tool_validation_failures_total` | Counter | tool_name, validation_phase | Schema validation failures |
| `document_generated_total` | Counter | type | Documents generated |
| `document_verifier_invoked_total` | Counter | type | Verifier invocations |
| `document_verifier_rejected_total` | Counter | type, reason | Verifier rejections |
| `template_response_rate` | Counter | template | Template responses (greetings) |
| `formatter_bubble_count` | Histogram | — | Bubbles per response |

#### E.2 Alerts

Full Alertmanager rule specification:

```yaml
groups:
- name: cost_alerts
  rules:
    - alert: AnthropicCacheHitRateLow
      expr: avg by (component) (anthropic_cache_hit_ratio) < 0.80
      for: 30m
      labels: { severity: P2 }
    
    - alert: DailySpendExceedsForecast
      expr: |
        (sum(increase(anthropic_cost_dollars_total[24h]))) > 65 * 1.3
      labels: { severity: P1 }
    
    - alert: SingleTurnCostHigh
      expr: histogram_quantile(0.99, anthropic_turn_cost_dollars) > 0.10
      labels: { severity: P2 }
    
    - alert: UnexpectedOpusUsage
      expr: rate(anthropic_cost_dollars_total{model="claude-opus-4-7"}[1h]) > 0
      labels: { severity: P2 }

- name: latency_alerts
  rules:
    - alert: SonnetP95High
      expr: histogram_quantile(0.95, sonnet_response_seconds) > 4
      for: 10m
      labels: { severity: P2 }
    
    - alert: IntentEnsembleP95High
      expr: histogram_quantile(0.95, intent_classification_seconds) > 1.5
      for: 10m
      labels: { severity: P2 }

- name: quality_alerts
  rules:
    - alert: ExtractionFallbackRateHigh
      expr: rate(extraction_fallback_total[1h]) / rate(extraction_total[1h]) > 0.05
      for: 1h
      labels: { severity: P1 }
    
    - alert: TRGComplianceScoreLow
      expr: |
        avg by (sales_stage) (rate(trg_compliance_score_sum[1h]) /
                              rate(trg_compliance_score_count[1h])) < 0.5
      for: 1h
      labels: { severity: P2 }

- name: document_alerts
  rules:
    - alert: DocumentGenerationVolumeAnomaly
      expr: |
        abs(rate(document_generated_total[1h]) -
            avg_over_time(rate(document_generated_total[1h])[7d])) >
            3 * stddev_over_time(rate(document_generated_total[1h])[7d])
      for: 30m
      labels: { severity: P1 }
    
    - alert: VerifierRejectionRateHigh
      expr: |
        rate(document_verifier_rejected_total[1h]) /
        rate(document_verifier_invoked_total[1h]) > 0.10
      for: 1h
      labels: { severity: P0 }
    
    - alert: SingleCustomerDocumentBurst
      expr: max by (customer_email) (rate(document_generated_total[15m])) > 3
      labels: { severity: P0 }

- name: tool_alerts
  rules:
    - alert: CircuitBreakerOpen
      expr: tool_circuit_breaker_state >= 2
      for: 5m
      labels: { severity: P1 }
    
    - alert: DeferredQueueGrowing
      expr: |
        tool_deferred_queue_depth > 20 OR
        deriv(tool_deferred_queue_depth[1h]) > 5
      for: 1h
      labels: { severity: P2 }
```

#### E.3 Dashboards

Six core Grafana dashboards. Each panel specification documented separately in `/ops/dashboards/`.

| Dashboard | Panels |
|---|---|
| Cost | Daily spend by model; cache hit rate; per-turn cost trend; per-conversation cost trend |
| Latency | Per-component p50/p95/p99; per-phase budget consumption; LLM call duration distribution |
| Quality | TRG compliance distribution; intent confidence; fallback rate; eval harness scores |
| Tri-Match | Shortcut hit rate; rule precision distribution; queue depth; layer distribution |
| Document | Generation rate; verifier rejection; deferred queue; idempotency cache hit |
| Conversation Funnel | Stage transitions; dropout points; conversion rate; average turn count by outcome |

### Appendix F — System Prompts

The cached system prompts. Subject to BookCraft team customization but the structural shape and constraints are locked.

#### F.1 Intent classification prompt

(See §6.4.3 for full reference. The complete prompt is ~3,500 tokens with the BookCraft service catalog, query intent definitions, sales funnel rules, and stickiness rules. Lives in `/src/prompts/intent_classification.txt`.)

#### F.2 Extraction prompt

(See §6.5 for structure. Complete prompt ~3,500 tokens, lives in `/src/prompts/extraction.txt`.)

#### F.3 Response generation prompt template

(Brand voice owned by BookCraft marketing team. Structural template lives in `/src/prompts/response_generation.txt`.)

#### F.4 Verifier prompt

```
You are the final safety gate for legally binding documents at BookCraft AI.
Your job is strict cross-validation between thread state and rendered text.

Approve only if every parameter in the document precisely matches what's
in the thread state. Reject for any of these reasons:

- Customer name in the document differs from thread state
- Customer email in the document differs from thread state
- Project description in the document is inconsistent with thread state
- Pricing or terms in the document differ from the most recent confirmed quote
- Any field in the document appears fabricated (not present in thread state)
- Any obvious typographical error in customer-provided fields

When in doubt, reject. False rejections delay one customer; false approvals
can produce legal exposure.

Anomalies are scored:
- "critical": affects identity, scope, money, or terms — auto-reject
- "minor": cosmetic — log but allow approval

Always invoke the verify_document_correctness tool.
```

#### F.5 Tri-Match rule generation prompt

Used by the auto-correction loop (Component 12). See `/src/prompts/rule_suggestion.txt`.

### Appendix G — Failure Mode Catalog

Consolidated catalog from each component's failure modes section. Indexed by failure ID for runbook reference.

| FM ID | Component | Failure | Detection | Auto-response | Severity |
|---|---|---|---|---|---|
| FM-001 | Thread state | Postgres primary down | Health check | Read from replica; queue writes | P0 |
| FM-002 | Thread state | Optimistic lock conflict | Application metric | Retry up to 3x | P3 |
| FM-003 | TRG | Embedder unavailable | Health check | Skip TRG; continue without context | P2 |
| FM-004 | Language guard | lingua-py error | Exception | Default to "en"; continue | P3 |
| FM-005 | Intent | Single LLM vendor down | Per-vendor circuit breaker | Continue with 2 of 3 | P1 |
| FM-006 | Intent | All 3 LLMs down | All breakers open | Tri-Match-only fallback | P0 |
| FM-007 | Intent | Stage transition invalid | Validator | Pin to current stage | P3 |
| FM-008 | Extraction | Haiku call fails | Exception | Empty extraction; rely on next turn | P2 |
| FM-009 | Response gen | Sonnet timeout | Timeout | Fallback message; queue retry | P1 |
| FM-010 | Response gen | Stream interruption | WebSocket error | Partial flush; reconnect | P2 |
| FM-011 | Pricing | Engine unreachable | Circuit breaker | Lead-capture fallback | P1 |
| FM-012 | Document | Confidence gate fails | Gate check | Pivot to user confirmation | P3 |
| FM-013 | Document | Verifier rejects | Verifier output | Flag for human review; do not send | P2 |
| FM-014 | Document | Anomaly threshold | Prometheus | Auto-suspend autonomous mode | P0 |
| FM-015 | Tool dispatcher | Tool not in registry | Lookup miss | Error to caller; alert | P2 |
| FM-016 | Tool dispatcher | Output validation error | Pydantic | Error to caller; alert (drift) | P1 |
| FM-017 | Tri-Match | Bad rule mass-misclassification | Calibration counters | Auto-deprecate | P1 |
| FM-018 | Auto-correction | Sonnet batch fails | Anthropic API | Retry next day; alert if 3 consecutive | P2 |
| FM-019 | Preprocessor | TEI sidecar unavailable | Health check + breaker | Cached embedding or fail fast | P1 |
| FM-020 | RAG | ES query timeout | Timeout | Empty retrieval; continue | P2 |

### Appendix H — Templates & Phrasings

Hardcoded user-facing strings. Marketing team can customize these post-launch.

#### H.1 Non-English redirects

```python
NON_ENGLISH_REDIRECTS = {
    "es": "¡Hola! Por el momento BookCraft AI atiende únicamente en inglés. ¿Podrías escribirnos en inglés para que podamos ayudarte?",
    "fr": "Bonjour ! Pour le moment, BookCraft AI ne répond qu'en anglais. Pourriez-vous nous écrire en anglais afin que nous puissions vous aider ?",
    "de": "Hallo! BookCraft AI unterstützt aktuell nur Englisch. Könnten Sie uns bitte auf Englisch schreiben?",
    "pt": "Olá! No momento, a BookCraft AI atende apenas em inglês. Você poderia nos escrever em inglês para que possamos ajudar?",
    "it": "Ciao! Al momento BookCraft AI risponde solo in inglese. Potresti scriverci in inglese così possiamo aiutarti?",
    "zh": "您好!目前 BookCraft AI 仅提供英文服务。请使用英文与我们交流,以便我们为您提供帮助。",
    "ja": "こんにちは!現在、BookCraft AI は英語でのみサービスを提供しています。英語でお問い合わせいただければ幸いです。",
    "ar": "مرحبًا! حاليًا، يقدم BookCraft AI الخدمة باللغة الإنجليزية فقط. هل يمكنك مراسلتنا باللغة الإنجليزية حتى نتمكن من مساعدتك؟",
    "hi": "नमस्ते! फिलहाल BookCraft AI केवल अंग्रेज़ी में सेवा देता है। कृपया हमें अंग्रेज़ी में लिखें ताकि हम आपकी मदद कर सकें।",
    "ru": "Здравствуйте! В настоящее время BookCraft AI работает только на английском языке. Не могли бы вы написать нам на английском, чтобы мы могли помочь?",
}

ENGLISH_FALLBACK = (
    "Hello! BookCraft AI currently provides services in English only. "
    "Could you please write to us in English so we can assist you?"
)
```

#### H.2 Greeting templates

```python
GREETING_TEMPLATES = {
    ("hi", "hello", "hey"): "Hello! How can I help with your book project today?",
    ("thanks", "thank you", "thanks!"): "You're welcome — let me know if you need anything else.",
    ("bye", "goodbye", "see you"): "Take care! We're here whenever you'd like to continue.",
}
```

#### H.3 Failure-mode user messages

```python
FAILURE_MESSAGES = {
    "pricing_unavailable": (
        "I want to give you accurate pricing, but our quoting system is briefly "
        "unavailable. A team member will follow up within the hour with a precise "
        "figure — could you share your email so we can reach you?"
    ),
    "all_llms_down": (
        "I'm experiencing a brief technical issue. A team member will follow up "
        "with you shortly — could you share your email so we can be in touch?"
    ),
    "consultation_unavailable": (
        "I can't book the consultation directly right now, but a team member will "
        "reach out within the next business day to schedule something that works for you."
    ),
    "document_gate_failed": (
        "Before I send the agreement, can you confirm a few details? I want to make "
        "sure everything is exactly right..."
    ),
    "stop_signal_received": (
        "Got it — we've voided the document. A team member will follow up with you "
        "directly to make sure everything is correct."
    ),
}
```

---

*End of architecture reference. Source code, prompts, and ADRs live in the BookCraft AI source repository. This document is the canonical specification; any deviation must be recorded as an ADR cross-referencing the section being modified.*
