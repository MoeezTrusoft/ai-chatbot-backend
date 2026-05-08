# BookCraft AI Chatbot — Ultimate Implementation & Execution Guide

**Document purpose:** This guide is designed to be pasted into a fresh project chat or handed to an engineering team as the single source for execution. It expands the approved BookCraft AI Chatbot architecture into a complete build playbook: foundations, data model, Elasticsearch domain knowledge, Tri-Match and Funnel Signal rules, AI platform adapters, Pricing & Timeline Engine, Portfolio Request Engine, Agreement/NDA Engine, monitoring, testing, deployment, and operational controls.

**Important constraint:** This document intentionally contains **no calendar execution timeline**. Phases below are dependency phases, not dates. A phase begins only when the prior phase’s exit criteria are met.

**Authority order for implementation:**

1. Architecture Reference v2.0 is the canonical architecture baseline.
2. Architecture Amendments v1.0 supersede/refine the baseline where D-070 through D-080 apply.
3. Uploaded templates and sample registries are authoritative inputs for document and portfolio engines.
4. This guide is the practical execution layer that turns the architecture into build steps.

---

## 0. Start-Here Execution Prompt for a New Chat

Use this prompt when opening a fresh implementation chat:

```text
You are implementing the BookCraft AI Chatbot from the approved architecture. Treat the following as locked:

- Python 3.12+, FastAPI, SQLModel/SQLAlchemy async, Pydantic v2.
- PostgreSQL 16 + pgvector, Redis 7, Elasticsearch 8, TEI BGE-small-en-v1.5, S3-compatible object storage.
- Anthropic Claude Haiku for intent/extraction/TRG/batch suggestions, Claude Sonnet for response generation and document verification, OpenAI GPT-5.4 mini for one intent ensemble vote, self-hosted DeepSeek V3 for the third intent vote.
- Every meaningful state field must carry provenance using FieldMeta.
- All external actions must go through the MCP-style Tool Dispatcher with schema validation, gating, idempotency, circuit breakers, retries, and audit logs.
- Elasticsearch/RAG contains service descriptions, FAQs, policies, process documentation, and portfolio metadata, but never numeric prices or concrete service timelines.
- Pricing and timeline numbers belong only to the Pricing & Timeline Engine.
- Legal documents use strict templates and typed parameters. LLMs must never write legal clauses.
- D-081: Tri-Match classifies query intent, service intent, and funnel stage. Funnel-stage output launches shadow-only with Decision Layer weight 0.
- Funnel Signal Engine launches in shadow mode with weight 0.
- Prometheus, Grafana, logs, traces, and alerts must be built alongside each component.
- No phase can begin until the prior phase exit criteria pass.

Now help me execute the requested phase step-by-step without skipping validation, tests, failure modes, or observability.
```

---

## 1. System Goal and Mental Model

BookCraft AI is a **24/7 production sales assistant** for BookCraft Publishers. It is responsible for:

- answering service questions,
- identifying the user’s project and needs,
- extracting contact and manuscript details,
- recommending relevant BookCraft services,
- providing pricing and timeline ranges through a deterministic engine,
- serving curated portfolio samples,
- routing NDA and service agreement requests through strict document generation,
- preserving auditability for every decision and tool action,
- improving deterministic classification over time through Tri-Match calibration.

This is not an LLM-only chatbot. The model is one participant inside a controlled system. The architecture depends on these principles:

1. **The LLM is a component, not the system.** Control flow, gating, storage, state mutation, pricing, timeline calculation, and document generation are deterministic.
2. **Every important state field has provenance.** Values are stored with confidence, source, timestamp, extractor, and raw evidence.
3. **Tools are typed and gated.** The LLM does not call arbitrary HTTP endpoints. It can only request known tools, and the orchestrator decides whether to invoke them.
4. **Prices and timelines live in one engine.** No duplicate pricing/timeline numbers in prompts, RAG content, marketing docs, or generated text.
5. **Legal text is never LLM-generated.** Agreements and NDAs are rendered from approved templates using validated parameters.
6. **Observability is a first-class feature.** Every component ships with metrics, logs, traces, dashboards, and alert thresholds.
7. **Quality gates matter more than phase speed.** A phase is complete only when its tests, SLO checks, and acceptance criteria pass.

---

## 2. Target Architecture Overview

### 2.1 High-level flow

```text
User message
  ↓
WebSocket/API ingress
  ↓
Pre-flight parallel work
  ├─ Language Guard
  ├─ Thread State load
  ├─ Shared Preprocessor → ProcessedMessage
  └─ TRG hot graph load
  ↓
Classification and retrieval
  ├─ Tri-Match classification: query/service intent only
  ├─ Funnel Signal Engine: funnel-stage signal only, shadow at launch
  ├─ 3-LLM intent ensemble: Haiku + GPT-mini + DeepSeek
  ├─ Combined Extraction
  └─ Elasticsearch RAG retrieval
  ↓
Decision Layer
  ├─ weighted source voting
  ├─ funnel-stage transition validation
  ├─ needs_clarification gate
  └─ full audit capture
  ↓
Routing
  ├─ clarification response
  ├─ Pricing & Timeline tool
  ├─ Portfolio Request tool
  ├─ NDA/Agreement tool, gated
  ├─ templated greeting
  └─ Sonnet response generation
  ↓
Formatter and streaming response
  ↓
Post-response async work
  ├─ TRG relation/compliance updates
  ├─ state deltas
  ├─ event logs
  ├─ classification logs
  └─ calibration counters
```

### 2.2 Core components

| Component | Purpose | Build phase |
|---|---|---|
| Thread State & Storage | authoritative state, customer identity, events, audit | Phase 1 |
| Language Guard | English-only guard and non-English redirect | Phase 1 |
| Shared Preprocessor | normalized text, tokens, atoms, spans, embeddings | Phase 1 |
| MCP Tool Dispatcher | typed, gated, audited tool execution | Phase 1 |
| Intent Classification | single LLM first, then ensemble | Phase 2 and Phase 4 |
| Combined Extraction | contact, project, commercial, request extraction | Phase 2 |
| Response Generation | Sonnet response generation and formatting | Phase 2 and Phase 3 |
| Elasticsearch RAG | grounded service/process/portfolio retrieval | Phase 2/3 |
| TRG | relation graph, outstanding questions, compliance, repetition | Phase 3 |
| Pricing & Timeline Engine | canonical quote and delivery estimate engine | Phase 3 |
| Portfolio Request Engine | curated static samples and gallery routing | Phase 3 |
| Decision Layer | aggregate LLM + Tri-Match + Funnel Signal votes | Phase 4 |
| Tri-Match Engine | deterministic query/service classifier with calibration | Phase 4/5 |
| Funnel Signal Engine | deterministic funnel-stage signal engine, shadow first | Phase 4 |
| Tri-Match Self-Improvement | disagreement mining and rule suggestion workflow | Phase 5 |
| Agreement & NDA Engine | strict-template document generation and verification | Phase 6 |

---

## 3. Canonical Repository Structure

Create a single backend repository. Do not split into micro-repos early.

```text
bookcraft-chatbot/
├── README.md
├── pyproject.toml
├── uv.lock
├── Makefile
├── docker-compose.yml
├── .env.example
├── .pre-commit-config.yaml
├── .github/
│   └── workflows/
│       ├── ci.yml
│       └── cd.yml
├── docs/
│   ├── architecture/
│   │   ├── architecture-reference.md
│   │   ├── architecture-amendments.md
│   │   └── adr/
│   ├── implementation/
│   ├── runbooks/
│   └── legal-template-notes/
├── data/
│   ├── rag-corpus/
│   │   ├── source_markdown/
│   │   ├── processed_chunks/
│   │   └── manifest.json
│   ├── pricing/
│   │   ├── service_catalog.yaml
│   │   ├── pricing_rules.yaml
│   │   ├── timeline_rules.yaml
│   │   └── policy_rules.yaml
│   ├── trimatch/
│   │   ├── query_intent_rules.json
│   │   ├── service_intent_rules.json
│   │   ├── eval_corpus.jsonl
│   │   └── sidecars/
│   │       ├── _negation_cues.json
│   │       ├── _typography_normalization.json
│   │       └── _compound_word_variants.json
│   ├── funnel/
│   │   ├── funnel_signal_rules_userlang.json
│   │   ├── funnel_signal_rules_crm.json
│   │   └── funnel_eval_corpus.jsonl
│   ├── portfolio/
│   │   ├── samples_registry.json
│   │   ├── genre_hierarchy_links.json
│   │   ├── author_websites.json
│   │   └── portfolio_map.yaml
│   └── templates/
│       ├── agreement/
│       │   ├── service_agreement_v1.ejs
│       │   ├── service_agreement_v1.schema.json
│       │   └── golden_params.json
│       └── nda/
│           ├── nda_v1.ejs
│           ├── nda_v1.schema.json
│           └── golden_params.json
├── ops/
│   ├── prometheus/
│   │   ├── prometheus.yml
│   │   └── alerts.yml
│   ├── grafana/
│   │   ├── provisioning/
│   │   └── dashboards/
│   ├── loki/
│   ├── otel/
│   │   └── otel-collector-config.yaml
│   └── docker/
├── scripts/
│   ├── dev/
│   ├── data/
│   │   ├── extract_bookcraft_knowledge.py
│   │   ├── verify_rag_corpus.py
│   │   ├── ingest_rag.py
│   │   ├── convert_samples_registry.py
│   │   └── verify_templates.py
│   └── ops/
├── src/
│   └── bookcraft/
│       ├── __init__.py
│       ├── config.py
│       ├── api/
│       ├── ws/
│       ├── domain/
│       ├── infra/
│       ├── components/
│       │   ├── storage/
│       │   ├── language_guard/
│       │   ├── preprocessor/
│       │   ├── intent/
│       │   ├── extraction/
│       │   ├── response/
│       │   ├── rag/
│       │   ├── trg/
│       │   ├── pricing/
│       │   ├── portfolio/
│       │   ├── documents/
│       │   ├── trimatch/
│       │   └── funnel_signal/
│       ├── tools/
│       ├── prompts/
│       ├── workers/
│       └── observability/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   ├── property/
│   ├── eval/
│   └── load/
└── migrations/
```

---

## 4. Phase 0 — Project Foundation

### 4.1 Goal

Set up a reproducible engineering environment, local infrastructure, CI/CD, secrets discipline, observability shell, and baseline repository before building business logic.

### 4.2 Deliverables

- Python project with locked dependency management.
- Docker Compose stack for Postgres, Redis, Elasticsearch, TEI, Prometheus, Grafana, Loki/OpenSearch, OpenTelemetry collector.
- CI pipeline with lint, type-check, tests, security scan, and secret scan.
- Secrets management policy.
- Basic FastAPI health endpoints.
- Observability bootstrap dashboards.

### 4.3 Python project setup

Use Python 3.12+ and `uv`.

```toml
[project]
name = "bookcraft"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "pydantic>=2.7",
  "sqlmodel>=0.0.22",
  "sqlalchemy>=2.0",
  "asyncpg>=0.29",
  "alembic>=1.13",
  "redis>=5.0",
  "httpx>=0.27",
  "anthropic>=0.40",
  "openai>=1.50",
  "spacy>=3.7",
  "rapidfuzz>=3.6",
  "phonenumbers>=8.13",
  "dateparser>=1.2",
  "elasticsearch[async]>=8.13",
  "weasyprint>=62",
  "jinja2>=3.1",
  "lingua-language-detector>=2.0",
  "structlog>=24.1",
  "prometheus-client>=0.20",
  "opentelemetry-api>=1.25",
  "opentelemetry-sdk>=1.25",
  "opentelemetry-exporter-otlp>=1.25",
  "opentelemetry-instrumentation-fastapi>=0.46b0",
  "opentelemetry-instrumentation-sqlalchemy>=0.46b0",
  "sentry-sdk[fastapi]>=2.0",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.2",
  "pytest-asyncio>=0.23",
  "pytest-cov>=5.0",
  "hypothesis>=6.100",
  "testcontainers>=4.4",
  "ruff>=0.5",
  "mypy>=1.10",
  "pre-commit>=3.7",
]
```

### 4.4 Local Docker Compose

Minimum local stack:

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: bookcraft
      POSTGRES_PASSWORD: bookcraft_dev
      POSTGRES_DB: bookcraft
    ports: ["5432:5432"]
    volumes: ["postgres-data:/var/lib/postgresql/data"]

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    volumes: ["redis-data:/data"]

  elasticsearch:
    image: docker.elastic.co/elasticsearch/elasticsearch:8.13.4
    environment:
      - discovery.type=single-node
      - xpack.security.enabled=false
      - ES_JAVA_OPTS=-Xms1g -Xmx1g
    ports: ["9200:9200"]
    volumes: ["es-data:/usr/share/elasticsearch/data"]

  tei:
    image: ghcr.io/huggingface/text-embeddings-inference:cpu-1.5
    command: --model-id BAAI/bge-small-en-v1.5 --port 8080
    ports: ["8080:8080"]
    volumes: ["tei-data:/data"]

  prometheus:
    image: prom/prometheus:latest
    ports: ["9090:9090"]
    volumes:
      - ./ops/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml
      - ./ops/prometheus/alerts.yml:/etc/prometheus/alerts.yml

  grafana:
    image: grafana/grafana:latest
    ports: ["3000:3000"]
    environment:
      GF_SECURITY_ADMIN_PASSWORD: admin
    volumes:
      - grafana-data:/var/lib/grafana
      - ./ops/grafana/provisioning:/etc/grafana/provisioning

  otel-collector:
    image: otel/opentelemetry-collector-contrib:latest
    command: ["--config=/etc/otel-collector-config.yaml"]
    ports: ["4317:4317", "4318:4318"]
    volumes:
      - ./ops/otel/otel-collector-config.yaml:/etc/otel-collector-config.yaml

volumes:
  postgres-data:
  redis-data:
  es-data:
  tei-data:
  grafana-data:
```

### 4.5 Environment variables

```bash
APP_ENV=dev
APP_NAME=bookcraft-chatbot
DATABASE_URL=postgresql+asyncpg://bookcraft:bookcraft_dev@localhost:5432/bookcraft
DATABASE_REPLICA_URL=
REDIS_URL=redis://localhost:6379/0
ELASTICSEARCH_URL=http://localhost:9200
TEI_URL=http://localhost:8080
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=http://deepseek-internal:8000/v1
NDA_MODE=manual
AGREEMENT_MODE=manual
TRIMATCH_MODE=shadow
TRIMATCH_SHORTCUT_LAYERS=
FUNNEL_SIGNAL_MODE=shadow
RAG_TOP_K=8
RAG_MAX_TOKENS_PER_CHUNK=200
SONNET_MAX_TOKENS=600
INTENT_ENSEMBLE_TIMEOUT_SECONDS=2.5
DEEPSEEK_TIMEOUT_SECONDS=4.0
```

### 4.6 Validation checklist

- `make install` completes.
- `docker compose up -d` starts all infra.
- FastAPI health endpoint returns 200.
- Postgres connection and migration baseline work.
- Redis ping works.
- Elasticsearch health works.
- TEI embedding endpoint returns a 384-dimensional vector.
- Prometheus sees the app target.
- Grafana opens and has provisioned dashboards.
- CI runs lint, type, unit, integration, security, and secret scans.

---

## 5. Phase 1 — Foundation Layer

### 5.1 Goal

Build the durable foundation: domain types, database schema, state storage, Redis cache, event log, preprocessor, language guard, and MCP Tool Dispatcher. This phase must be boring and correct.

### 5.2 Domain model principles

Every meaningful field in state must use `FieldMeta[T]`.

```python
from datetime import datetime
from enum import StrEnum
from typing import Generic, TypeVar
from pydantic import BaseModel, Field

T = TypeVar("T")

class Source(StrEnum):
    USER_STATED = "user_stated"
    USER_CONFIRMED = "user_confirmed"
    AI_EXTRACTED = "ai_extracted"
    CSR_ENTERED = "csr_entered"
    SYSTEM = "system"

class FieldMeta(BaseModel, Generic[T]):
    value: T | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: Source
    extracted_at: datetime
    extracted_by: str | None = None
    raw_excerpt: str | None = None

    def is_high_confidence(self, threshold: float = 0.85) -> bool:
        return self.value is not None and self.confidence >= threshold
```

Never store `client_email = "x"` alone. Store:

```json
{
  "value": "author@example.com",
  "confidence": 0.98,
  "source": "user_stated",
  "extracted_at": "...",
  "extracted_by": "deterministic_preextractor.v1",
  "raw_excerpt": "my email is author@example.com"
}
```

### 5.3 Core enums

Create enums for:

- `ServiceCategory`
  - Ghostwriting
  - Editing & Proofreading
  - Cover Design & Illustration
  - Interior Formatting
  - Audiobook Production
  - Publishing & Distribution
  - Marketing & Promotion
  - Author Website
  - Video Trailer
- `QueryIntentType`
  - greeting
  - service_question
  - pricing_question
  - timeline_question
  - portfolio_request
  - consultation_request
  - nda_request
  - agreement_request
  - revision_question
  - payment_question
  - publishing_platform_question
  - manuscript_status_update
  - contact_info_provided
  - complaint_or_objection
  - ready_to_buy
  - unclear
  - spam_or_abuse
  - off_topic
- `SalesStage`
  - new
  - exploring
  - service_discovery
  - scoping
  - quote_requested
  - quoted
  - negotiation
  - nda_requested
  - agreement_requested
  - closed_won
  - closed_lost
- `ManuscriptStatus`
  - idea_only
  - outline
  - partial_draft
  - completed_draft
  - edited
  - published
  - unknown

### 5.4 Database schema

Implement tables:

| Table | Purpose |
|---|---|
| `customers` | normalized identity and lifetime state |
| `threads` | current materialized thread state |
| `thread_events` | append-only hash-chained audit trail |
| `intent_classifications` | all source votes and final decisions |
| `tool_invocation_logs` | every tool call and result |
| `deferred_tool_invocations` | human-gated requests |
| `graph_nodes` | TRG nodes |
| `graph_edges` | TRG relations |
| `trimatch_rules` | deterministic classification rules |
| `funnel_signal_rules` | deterministic funnel rules |
| `quotes` | pricing/timeline quote outputs |
| `documents` | generated NDA/agreement metadata |
| `portfolio_deliveries` | sample links sent to users |

#### 5.4.1 Hash-chained events

Each event row stores:

```python
class ThreadEvent(SQLModel, table=True):
    id: UUID
    thread_id: UUID
    sequence: int
    event_type: str
    payload: dict
    previous_hash: str | None
    event_hash: str
    created_at: datetime
```

Hash calculation:

```text
event_hash = SHA256(thread_id + sequence + event_type + canonical_json(payload) + previous_hash)
```

Validation:

- Recompute full chain in tests.
- Changing an old event must break verification.
- New event append must happen in the same transaction as materialized state update.

### 5.5 Redis conventions

Use namespaced keys:

```text
bc:{env}:thread:{thread_id}:state
bc:{env}:thread:{thread_id}:graph
bc:{env}:idempotency:{idempotency_key}
bc:{env}:embedding:{language}:{text_hash}
bc:{env}:trimatch:active_state
```

Rules:

- All cache keys must have TTL unless explicitly persistent.
- Idempotency cache TTL is fixed by policy.
- Hot thread state expires after inactivity but remains authoritative in Postgres.
- Redis failure must degrade gracefully except for idempotency-sensitive write tools.

### 5.6 Language Guard

Launch language support is English. Implement:

1. ASCII fast path for simple English messages.
2. `lingua-py` fallback for longer/ambiguous text.
3. Non-English redirect with polite response.
4. State update: repeated non-English turns can mark thread as unqualified.

Validation cases:

- `hi`, `hello`, `price?` treated as English.
- Non-English paragraphs redirected.
- Mixed English/other language handled generously if BookCraft service intent is clear.

Metrics:

```text
language_detection_seconds{source}
language_detection_results_total{language}
non_english_redirects_total{language}
```

### 5.7 Shared Preprocessor

The preprocessor runs once per user turn and produces `ProcessedMessage`.

```python
class TokenInfo(BaseModel):
    text: str
    lemma: str
    pos: str | None = None
    start: int
    end: int
    negated: bool = False
    hedged: bool = False
    counterfactual: bool = False

class Span(BaseModel):
    start: int
    end: int
    text: str
    cue: str

class ProcessedMessage(BaseModel):
    raw: str
    normalized: str
    tokens: list[TokenInfo]
    negation_spans: list[Span]
    hedge_spans: list[Span]
    counterfactual_spans: list[Span]
    deterministic_atoms: dict[str, object]
    embedding: list[float]
    language: str
    char_count: int
```

Pipeline:

1. Unicode normalize with NFKC.
2. Apply typography normalization sidecar.
3. Apply compound-word variants sidecar for service recognition.
4. spaCy tokenization and lemmas.
5. Negation span detection.
6. Hedge span detection.
7. Counterfactual span detection.
8. Deterministic atoms:
   - email,
   - phone,
   - URL,
   - currency mention,
   - date mention,
   - word count,
   - page count,
   - manuscript status clue,
   - service mention clue.
9. TEI embedding using BGE-small-en-v1.5.
10. Redis embedding cache by normalized text hash.

Sidecar files:

```text
data/trimatch/sidecars/_negation_cues.json
data/trimatch/sidecars/_typography_normalization.json
data/trimatch/sidecars/_compound_word_variants.json
```

Required tests:

- `I don't need ghostwriting, I need editing` must not classify ghostwriting as primary service.
- `Maybe I need marketing` must mark marketing as hedged.
- `If I had a finished manuscript, I would ask for formatting` must mark formatting as counterfactual.
- `My book is 65,000 words` extracts word count.
- `email me at x@example.com` extracts email.
- TEI down with cached text returns cached embedding.
- TEI down with uncached text fails safely and logs a degraded path.

Metrics:

```text
preprocessor_seconds
preprocessor_atoms_extracted_total{atom_type}
preprocessor_negation_spans_total
preprocessor_hedge_spans_total
preprocessor_counterfactual_spans_total
embedder_latency_seconds
embedder_cache_hit_total
```

### 5.8 MCP Tool Dispatcher

The dispatcher is the only path to external actions and deterministic tools.

Tool classes:

```python
class ToolClass(StrEnum):
    READ = "read"
    WRITE_STATE = "write_state"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"
    HIGH_STAKES_DOCUMENT = "high_stakes_document"
```

Tool context:

```python
class ToolContext(BaseModel):
    thread_id: UUID
    customer_id: UUID | None
    turn_sequence: int
    invoked_by: str
    correlation_id: str
    idempotency_key: str
    environment: str
```

Dispatcher responsibilities:

- registry lookup,
- versioned tool names such as `get_pricing_quote.v1`,
- Pydantic input validation,
- gating policy,
- idempotency cache,
- circuit breaker,
- timeout,
- retry policy,
- Pydantic output validation,
- audit log,
- metrics.

Never let the LLM directly mutate state. The LLM may request a tool; the orchestrator validates whether the tool should run.

### 5.9 Gating policy

Initial modes:

```bash
NDA_MODE=manual
AGREEMENT_MODE=manual
```

Allowed values:

```text
manual | verifier_gated | autonomous
```

Rules:

- `manual`: create `deferred_tool_invocation`, do not generate/send document automatically.
- `verifier_gated`: render document, verify via deterministic and LLM verifiers, queue for human review if any uncertainty.
- `autonomous`: only allowed after golden tests, verifier tests, hash-chain audit, and rollback workflow pass.

Validation:

- Invalid mode fails startup.
- Manual mode always defers.
- No per-request override can bypass environment gating.

### 5.10 Phase 1 exit criteria

- Domain models implemented with strict typing.
- FieldMeta serialization and validation passes.
- Database migrations run cleanly.
- Redis wrapper works and namespaces keys.
- Event log hash chain verified.
- Language Guard passes corpus tests.
- Preprocessor produces normalized message, spans, atoms, and embeddings.
- Tool Dispatcher can invoke a no-op tool with audit log and idempotency.
- Gating policy tested for NDA and Agreement modes.
- Prometheus metrics exist for every built path.
- Grafana has starter dashboards.

---

## 6. Phase 2 — Intelligence Baseline

### 6.1 Goal

Create an end-to-end chatbot loop using one intent classifier, extraction, Elasticsearch RAG, and Sonnet response generation. This phase proves the system can converse before adding the full ensemble and self-improvement machinery.

### 6.2 Basic Intent Classifier

Start with Claude Haiku only.

Input:

- normalized user message,
- current ThreadState summary,
- recent turns,
- detected atoms,
- service catalog,
- funnel stage list.

Output schema:

```python
class IntentVote(BaseModel):
    query_primary: QueryIntentType
    query_secondary: list[QueryIntentType] = []
    service_primary: ServiceCategory | None
    service_secondary: list[ServiceCategory] = []
    funnel_stage: SalesStage
    needs_clarification: bool
    confidence: float
    rationale: str
    evidence: list[str]
```

Rules:

- Use structured output only.
- Retry once if schema validation fails.
- Persist every vote even before ensemble exists.
- Never use this classifier to set price/timeline numbers.

### 6.3 Combined Extraction Engine

Purpose: extract state deltas, not generate a response.

Extraction categories:

```python
class CombinedExtraction(BaseModel):
    contact: ContactExtraction
    project: ProjectExtraction
    commercial: CommercialExtraction
    service_interest: ServiceInterestExtraction
    sample_request: SampleRequestExtraction
    document_request: DocumentRequestExtraction
    consultation_request: ConsultationRequestExtraction
    user_questions: list[str]
    state_deltas: list[StateDelta]
```

Contact fields:

- full name,
- email,
- phone,
- location,
- preferred contact method,
- preferred contact time.

Project fields:

- book title,
- genre,
- manuscript status,
- word count,
- page count,
- target format,
- target publishing platforms,
- target launch window as user-stated free text,
- author goal.

Commercial fields:

- budget stated,
- urgency stated,
- selected services,
- add-ons,
- objections,
- quote accepted.

No-overwrite rule:

- Higher-confidence user-confirmed data cannot be overwritten by lower-confidence AI extraction.
- New extraction becomes a candidate delta if conflict exists.
- Conflicts can trigger clarification.

Metrics:

```text
extraction_seconds
extraction_fields_per_turn{category}
extraction_no_overwrite_skips_total{field}
extraction_conflicts_total{field}
```

### 6.4 Elasticsearch RAG Setup

#### 6.4.1 RAG rule

Elasticsearch stores BookCraft knowledge for explanation and grounding. It must **not** store numeric pricing or concrete timeline values. Those belong only to the Pricing & Timeline Engine.

#### 6.4.2 Source documents

Use the uploaded `bookcraft_knowledge.zip` as the seed corpus. Its enhanced bundle contains markdown docs for:

- About BookCraft,
- Audiobook Production,
- Author Website,
- Cover Design & Illustration,
- Editing & Proofreading,
- Formatting,
- Ghostwriting,
- Marketing & Promotion,
- Publishing & Distribution,
- Video Trailers.

The bundle already follows the decoupling principle: service descriptions, tiers, add-ons, process explanations, and technical specs remain in content; numeric prices and concrete timeline values are removed and routed to the Pricing & Timeline Engine.

#### 6.4.3 Corpus extraction script

```python
# scripts/data/extract_bookcraft_knowledge.py
from pathlib import Path
import zipfile

SRC = Path("/mnt/data/bookcraft_knowledge.zip")
OUT = Path("data/rag-corpus/source_markdown")

OUT.mkdir(parents=True, exist_ok=True)

with zipfile.ZipFile(SRC) as z:
    z.extractall("/tmp/bookcraft_knowledge")

# Prefer enhanced_content_v2/markdown/*.md if present.
# Copy markdown files into OUT and write manifest.json.
```

#### 6.4.4 Pricing/timeline verifier

Before indexing, scan every chunk. Reject chunks containing pricing/timeline-shaped patterns.

Examples of rejected patterns:

```regex
\$\s?\d+
\b\d+\s?%\b
\b\d+\s?[-–]\s?\d+\s?(days|weeks|months)\b
\b\d+\s?(business days|weeks|months)\b
\b(per word|per page|per hour|PFH|monthly fee)\b
```

Allowed exceptions:

- Company address numbers.
- Phone numbers.
- Legal retention terms inside templates are not RAG content unless explicitly placed in policy docs.
- External standards that are not BookCraft pricing may be allowed by whitelist.

Script behavior:

```text
read source markdown
  → split into sections
  → chunk each section
  → verify no forbidden numeric patterns
  → write accepted chunks
  → write rejected_chunks_report.json
  → block CI if rejected chunk count > 0
```

#### 6.4.5 Chunking strategy

- Chunk size: maximum 200 tokens.
- Overlap: 50 tokens.
- Preserve section heading in each chunk.
- Include metadata:
  - `doc_id`,
  - `source_filename`,
  - `title`,
  - `service`,
  - `section`,
  - `doc_type`,
  - `chunk_index`,
  - `content_version`,
  - `source_hash`.

#### 6.4.6 Elasticsearch mapping

```json
PUT bookcraft_rag_v1
{
  "mappings": {
    "properties": {
      "chunk_id": { "type": "keyword" },
      "text": { "type": "text" },
      "embedding": {
        "type": "dense_vector",
        "dims": 384,
        "index": true,
        "similarity": "cosine"
      },
      "metadata": {
        "properties": {
          "service": { "type": "keyword" },
          "section": { "type": "keyword" },
          "doc_type": { "type": "keyword" },
          "source_filename": { "type": "keyword" },
          "content_version": { "type": "keyword" }
        }
      },
      "created_at": { "type": "date" },
      "updated_at": { "type": "date" }
    }
  }
}
```

Use an alias:

```text
bookcraft_rag_current → bookcraft_rag_v1
```

New corpus versions create a new physical index and atomically move the alias after verification.

#### 6.4.7 Hybrid retrieval

Use BM25 + vector retrieval and combine with RRF.

Retrieval input:

- `ProcessedMessage.normalized`,
- `ProcessedMessage.embedding`,
- detected service intent,
- detected query intent.

Retrieval output:

```python
class RetrievedChunk(BaseModel):
    chunk_id: str
    text: str
    score: float
    service: ServiceCategory | None
    section: str
    source_filename: str
```

Rules:

- Top-k = 8.
- Max 200 tokens per chunk.
- Total RAG context cap = 1,600 tokens.
- Reuse `ProcessedMessage.embedding`; do not re-embed query text.
- Never allow RAG chunks to answer price/timeline with numbers.

Metrics:

```text
rag_retrieval_seconds
rag_chunks_returned
rag_empty_result_total
rag_rejected_chunk_total{reason}
rag_alias_version_info{version}
```

### 6.5 Sonnet Response Generation

Prompt structure:

```text
SYSTEM:
- BookCraft voice and safety rules
- Never invent prices/timelines/legal clauses
- Use tools for price/timeline/sample/document requests
- Answer in concise helpful paragraphs
- Ask only necessary clarifying questions

CONTEXT:
- Thread summary
- Current state fields with provenance
- Intent classification
- Extraction result
- Outstanding questions
- RAG chunks
- Recent turns

USER MESSAGE:
- normalized message
```

Response rules:

- If price/timeline intent and inputs are sufficient, call tool instead of generating a number.
- If inputs are missing, ask for only the missing fields.
- If service explanation intent, answer from RAG.
- If portfolio intent, call Portfolio Request Engine.
- If NDA/agreement intent, route through document tool and gating.
- Never claim guaranteed book sales or rankings.
- Never offer ghostwriting samples; explain confidentiality and offer adjacent samples if appropriate.

### 6.6 Formatter

The formatter sanitizes model output:

- remove unsupported markdown,
- split into chat bubbles,
- enforce paragraph length,
- prevent accidental raw JSON exposure,
- preserve links only from approved tool outputs,
- add typing/streaming pacing.

### 6.7 Phase 2 exit criteria

- Basic end-to-end conversation works.
- Single Haiku intent classification persists to DB.
- Extraction updates ThreadState through approved state mutation methods.
- RAG answers service/process questions from Elasticsearch.
- Price/timeline questions do not produce invented numbers.
- Responses use Sonnet and include RAG context where appropriate.
- RAG ingestion verifier blocks numeric price/timeline content.
- Eval corpus baseline exists.
- Metrics and traces exist for each path.

---

## 7. Phase 3 — Production-Ready Sales Engines

### 7.1 Goal

Add the production capabilities that turn the assistant into a sales tool: streaming, TRG, Pricing & Timeline Engine, Portfolio Request Engine, quote persistence, and post-response state intelligence.

---

## 8. Temporal Relational Graph Engine

### 8.1 Purpose

TRG tracks conversation relationships that a single LLM call cannot reliably maintain:

- whether the bot answered the user’s question,
- whether the user confirmed a quote,
- outstanding questions,
- repetition,
- compliance score,
- relation between turns,
- stage-aware conversation health.

TRG does **not** classify funnel stage after D-070. Funnel-stage signal comes from the LLM ensemble and Funnel Signal Engine.

### 8.2 Graph model

```python
class GraphNode(SQLModel, table=True):
    id: UUID
    thread_id: UUID
    turn_sequence: int
    role: str  # user | assistant | tool
    text: str
    embedding: list[float] | None
    created_at: datetime

class GraphEdge(SQLModel, table=True):
    id: UUID
    thread_id: UUID
    source_node_id: UUID
    target_node_id: UUID
    relation_type: str
    confidence: float
    created_at: datetime
```

Relation types:

- answers,
- asks_clarification,
- confirms,
- rejects,
- changes_scope,
- repeats_question,
- provides_missing_info,
- requests_human,
- accepts_quote,
- disputes_quote,
- requests_document.

### 8.3 Hot graph and compaction

- Keep recent nodes in Redis.
- Persist all nodes/edges to Postgres.
- When hot graph exceeds limit, compact older nodes into a summary with references.
- Never lose audit events; compaction only affects runtime context.

Metrics:

```text
trg_relation_classification_seconds
trg_relation_classifier_distribution_total{classifier}
trg_compliance_score{sales_stage}
trg_unaddressed_questions_total{sales_stage}
trg_compaction_total
trg_graph_size_nodes
```

---

## 9. Pricing & Timeline Engine — From Scratch

### 9.1 Purpose

The Pricing & Timeline Engine is the **single source of truth** for all BookCraft numbers related to:

- price ranges,
- timeline ranges,
- discounts,
- rush fees,
- add-on pricing,
- payment schedule options,
- quote expiration policy,
- commercial assumptions.

No RAG document, prompt, or LLM response should contain pricing/timeline values unless the engine returned them.

### 9.2 Engine boundaries

The engine owns:

- numeric quote ranges,
- timeline estimates,
- bundle calculations,
- rush modifiers,
- complexity modifiers,
- add-on calculations,
- quote IDs,
- payment schedule proposals,
- assumptions and exclusions.

RAG owns:

- what each service includes,
- tier names,
- process descriptions,
- add-on names and descriptions,
- complexity driver names,
- educational explanations.

### 9.3 Required user inputs by service

| Service | Required inputs | Optional inputs |
|---|---|---|
| Ghostwriting | genre/category, expected word count or book length goal, source material status | interview count, research depth, author involvement |
| Editing & Proofreading | word count, editing level, genre | sample edit needed, urgency, style guide |
| Cover Design & Illustration | format, design tier, illustration count/style | trim size, series branding, source files |
| Interior Formatting | format types, word/page count, image/table complexity | trim size, platform targets, accessibility needs |
| Audiobook Production | word count or finished runtime estimate, narrator type, production tier | accent, multi-voice, music/SFX |
| Publishing & Distribution | platforms, format types, metadata status | ISBN needs, imprint, global distribution |
| Marketing & Promotion | campaign tier, genre, goals, assets available | ad budget, launch status, audience size |
| Author Website | website tier, page count, integrations | domain/hosting status, copywriting, SEO depth |
| Video Trailer | trailer tier, duration band, asset availability | voiceover, animation style, music/SFX |

### 9.4 Pricing config files

Use config-backed rules first; migrate to admin UI later.

```yaml
# data/pricing/service_catalog.yaml
services:
  editing_proofreading:
    display_name: "Editing & Proofreading"
    units:
      primary: word_count
    required_inputs:
      - word_count
      - editing_level
      - genre_category
    tiers:
      manuscript_assessment:
        display_name: "Manuscript Assessment"
      developmental_editing:
        display_name: "Developmental Editing"
      copyediting:
        display_name: "Copyediting"
      proofreading:
        display_name: "Proofreading"

  cover_design_illustration:
    display_name: "Cover Design & Illustration"
    units:
      primary: project
    required_inputs:
      - format_type
      - design_tier
```

```yaml
# data/pricing/pricing_rules.yaml
version: pricing_v1
currency: USD
rules:
  editing_proofreading:
    base_formula: "unit_rate * word_count"
    rates:
      manuscript_assessment:
        unit_rate: REPLACE_WITH_APPROVED_VALUE
      developmental_editing:
        unit_rate: REPLACE_WITH_APPROVED_VALUE
    modifiers:
      genre_complexity:
        low: 1.00
        medium: REPLACE_WITH_APPROVED_VALUE
        high: REPLACE_WITH_APPROVED_VALUE
      rush:
        standard: 1.00
        rush: REPLACE_WITH_APPROVED_VALUE
```

Use `REPLACE_WITH_APPROVED_VALUE` during development. Do not invent business numbers.

### 9.5 Timeline config files

```yaml
# data/pricing/timeline_rules.yaml
version: timeline_v1
rules:
  editing_proofreading:
    unit: business_days
    base_formula: "base_days + word_count_factor + complexity_factor"
    base_days:
      manuscript_assessment: REPLACE_WITH_APPROVED_VALUE
      developmental_editing: REPLACE_WITH_APPROVED_VALUE
    modifiers:
      rush:
        standard: 1.00
        rush: REPLACE_WITH_APPROVED_VALUE
      client_delay_policy: "timeline excludes delays caused by missing materials or late approvals"
```

### 9.6 Engine input schema

```python
class PricingContext(BaseModel):
    thread_id: UUID
    customer_id: UUID | None
    requested_services: list[ServiceCategory]
    manuscript: ManuscriptDetails
    selected_tiers: dict[ServiceCategory, str | None] = {}
    add_ons: dict[ServiceCategory, list[str]] = {}
    urgency: str | None = None
    location: str | None = None
    discount_code: str | None = None
    source_quote_id: str | None = None

class ManuscriptDetails(BaseModel):
    genre: str | None = None
    word_count: int | None = None
    page_count: int | None = None
    manuscript_status: ManuscriptStatus | None = None
    formats: list[str] = []
```

### 9.7 Engine output schema

```python
class MoneyRange(BaseModel):
    currency: str = "USD"
    low: Decimal
    high: Decimal

class TimelineRange(BaseModel):
    unit: str = "business_days"
    low: int
    high: int

class QuoteLineItem(BaseModel):
    service: ServiceCategory
    tier: str | None
    add_on: str | None = None
    price_range: MoneyRange
    timeline_range: TimelineRange | None = None
    assumptions: list[str]

class PricingTimelineQuote(BaseModel):
    quote_id: str
    thread_id: UUID
    line_items: list[QuoteLineItem]
    total_price_range: MoneyRange
    total_timeline_range: TimelineRange
    payment_schedule_options: list[PaymentScheduleOption]
    assumptions: list[str]
    missing_inputs: list[str] = []
    confidence: float
    created_at: datetime
```

### 9.8 Pre-pricing validation

Before quote calculation, validate required inputs.

Example:

```python
def missing_inputs_for(service: ServiceCategory, state: ThreadState) -> list[str]:
    required = CATALOG[service].required_inputs
    return [field for field in required if not state.has_high_confidence(field)]
```

If missing inputs exist:

- do not quote,
- return `needs_clarification=true`,
- ask for the smallest necessary set of missing inputs.

Example response:

```text
I can estimate that, but I need one detail first: approximately how many words is your manuscript?
```

### 9.9 Quote calculation flow

```text
get_pricing_quote.v1
  ↓
validate input schema
  ↓
load canonical pricing rules
  ↓
check missing inputs
  ↓
calculate line item ranges
  ↓
apply modifiers
  ↓
apply bundle/rush/discount policies
  ↓
calculate total range
  ↓
calculate payment schedule options
  ↓
persist quote
  ↓
append thread event
  ↓
return quote object
```

### 9.10 Payment schedule handling

The uploaded service agreement template supports multiple schedule styles:

- 100% upon signing,
- percentage + monthly installments,
- fixed amount + monthly installments,
- advance + final payment linked to service,
- milestone-based schedule.

Represent them as:

```python
class PaymentScheduleType(StrEnum):
    FULL_UPON_SIGNING = "100% upon signing"
    PERCENTAGE_MONTHLY = "Percentage + Monthly Installments"
    FIXED_MONTHLY = "Fixed Amount + Monthly Installments"
    ADVANCE_FINAL = "Advance + Final Payment (linked to service)"
    MILESTONE_BASED = "Milestone-Based Schedule"

class PaymentScheduleOption(BaseModel):
    schedule_type: PaymentScheduleType
    initial_amount: Decimal | None = None
    initial_percentage: Decimal | None = None
    remaining_amount: Decimal | None = None
    remaining_percentage: Decimal | None = None
    number_of_months: int | None = None
    installment_amount: Decimal | None = None
    milestones: list[PaymentMilestone] = []
```

### 9.11 Quote acceptance

If the previous bot turn included a quote and the user says yes/confirm/proceed, TRG marks relation as `confirms`. Then:

- set quote accepted,
- store accepted quote ID,
- allow agreement generation to reference only accepted quote data,
- never generate an agreement from an unaccepted or stale quote.

### 9.12 Pricing & Timeline MCP tools

Implement:

```text
get_pricing_quote.v1
get_timeline_estimate.v1
explain_quote_assumptions.v1
list_required_quote_inputs.v1
```

Tool classes: READ or WRITE_STATE depending on persistence.

### 9.13 Price/Timeline tests

Unit tests:

- missing word count blocks quote,
- invalid tier rejected,
- negative word count rejected,
- discount cannot make total negative,
- line items add correctly,
- timeline range low <= high,
- all outputs are ranges, not single unqualified numbers.

Contract tests:

- tool input schema rejects unknown fields,
- tool output schema requires quote_id,
- quote persisted to ThreadState,
- accepted quote can be loaded by Agreement Engine.

Property tests:

- increasing word count never decreases price for word-based services,
- adding add-ons never decreases total,
- rush modifier never reduces delivery effort unless explicitly modeled as a premium rush policy,
- all returned quotes contain assumptions.

Metrics:

```text
pricing_quote_seconds
pricing_quote_total{service,status}
pricing_missing_inputs_total{service,field}
pricing_quote_range_width{service}
timeline_estimate_seconds
timeline_estimate_total{service,status}
quote_accepted_total{service}
```

---

## 10. Portfolio Request Engine

### 10.1 Purpose

The Portfolio Request Engine returns curated, static portfolio samples. It must not dynamically generate a gallery, scrape live pages during user requests, or invent samples.

### 10.2 Uploaded sources to normalize

Use:

- `portfolio_samples.docx` for service-level portfolio rules.
- `samples.registry.js` for service/genre sample assets.
- `genre_hierarchy_links.json` for Amazon/book links by genre hierarchy.

Observed source rules:

- Author Website samples are explicit website links.
- Publishing & Distribution, Editing & Proofreading, Marketing & Promotion, and Formatting should use Amazon links from attached JSON sources.
- Video Trailer samples use the four provided trailer URLs.
- Cover Design & Illustration uses cover designs from JSON/registry.
- Ghostwriting samples are not provided because of confidentiality.
- Audiobook samples are pending and should use a safe unavailable response until supplied.

### 10.3 Normalize samples registry

Convert `samples.registry.js` to JSON at build time.

Target schema:

```python
class PortfolioSample(BaseModel):
    title: str
    service: ServiceCategory
    genre: str | None = None
    url: str | None = None
    cover_url: str | None = None
    sample_type: str  # website | amazon | cover | trailer | unavailable_notice
    source: str
    confidentiality_note: str | None = None

class PortfolioRegistry(BaseModel):
    version: str
    samples: list[PortfolioSample]
```

### 10.4 Portfolio map

```yaml
# data/portfolio/portfolio_map.yaml
services:
  author_website:
    default:
      - title: "Neil Gaiman"
        url: "https://neilgaiman.com/"
      - title: "Rupi Kaur"
        url: "https://rupikaur.com/pages/all-books"
  video_trailer:
    default:
      - title: "Till We Have Faces — Book Trailer"
        url: "..."
  ghostwriting:
    default:
      unavailable_reason: "Ghostwriting samples are confidential. Offer NDA-safe process examples instead."
```

### 10.5 Matching logic

Input:

```python
class PortfolioRequestInput(BaseModel):
    service: ServiceCategory | None
    genre: str | None = None
    category: str | None = None
    limit: int = 3
```

Matching cascade:

```text
exact service + exact normalized genre
  ↓
service + genre alias
  ↓
service + parent genre from hierarchy
  ↓
service default
  ↓
general default
  ↓
safe unavailable response
```

Rules:

- Cap at 3 sample groups per user turn.
- For multi-service requests, run tools in parallel.
- Track delivered samples in ThreadState.
- If URL fails validation, do not send it.
- Alert on any 404.

### 10.6 Ghostwriting response rule

Never provide ghostwriting samples. Use:

```text
Because ghostwriting work is confidential, we do not share ghostwritten manuscripts as samples. I can show you our process, confidentiality approach, and relevant adjacent samples such as editing, covers, or published-book examples in your genre.
```

### 10.7 Audiobook pending rule

Until samples are supplied:

```text
Audiobook samples are not yet available in the approved portfolio registry. Offer to share process details or route to a human.
```

### 10.8 Portfolio tool

```text
get_portfolio_samples.v1
```

Output:

```python
class PortfolioSamplesOutput(BaseModel):
    matched_specificity: str
    samples: list[PortfolioSample]
    unavailable_reason: str | None = None
    follow_up_suggestion: str | None = None
```

Metrics:

```text
portfolio_request_total{service,matched_specificity}
portfolio_samples_returned_total{service}
portfolio_unavailable_total{service,reason}
portfolio_url_404_total{service}
```

### 10.9 Portfolio tests

- `fantasy covers` returns cover-design samples with fantasy-related genre fallback.
- `show me author websites` returns author website URLs.
- `show me book trailers` returns trailer URLs.
- `show me ghostwriting samples` returns confidentiality-safe response.
- `show me audiobook samples` returns pending-safe response.
- Registry reload works without app restart.
- URL validation catches broken links.

---

## 11. Phase 4 — Ensemble, Decision Layer, Tri-Match, and Funnel Signal

### 11.1 Goal

Upgrade from single-classifier intelligence to robust multi-source classification:

- Haiku vote,
- GPT-mini vote,
- DeepSeek vote,
- Tri-Match query/service vote,
- Funnel Signal Engine funnel-stage signal.

### 11.2 AI platform adapters

Create a unified interface:

```python
class LLMClassifier(Protocol):
    name: str
    async def classify(self, input: IntentInput) -> IntentVote: ...
```

#### 11.2.1 Anthropic adapter

Use native Anthropic API for:

- Haiku intent vote,
- Haiku extraction,
- Haiku TRG relation fallback,
- Sonnet response generation,
- Sonnet document verification,
- Sonnet rule suggestion batches.

Features to use:

- tool use where appropriate,
- prompt caching for large stable system prompts,
- strict JSON validation in application code,
- retry on schema errors.

#### 11.2.2 OpenAI adapter

Use GPT-mini for one intent vote.

Rules:

- Use function calling or structured outputs.
- Validate with Pydantic after response.
- Treat model output as untrusted until schema validation passes.
- Apply timeout and circuit breaker.

#### 11.2.3 DeepSeek adapter

Self-host DeepSeek V3 behind an internal OpenAI-compatible endpoint.

Rules:

- Hosted DeepSeek API is not approved for production.
- Internal-only network.
- JSON-mode response.
- Longer timeout allowed than hosted providers.
- Circuit breaker required.

### 11.3 Race-with-quorum

Run all three LLM classifiers concurrently.

Quorum condition:

```text
2 of 3 agree on:
- query.primary
- service.primary_service
- funnel.stage
```

If quorum reached:

- cancel remaining task if safe,
- preserve partial logs,
- continue to Decision Layer.

If no quorum:

- wait for all successful votes within timeout,
- Decision Layer resolves by weighted vote,
- flag for eval if disagreement is high.

Metrics:

```text
intent_classification_seconds{source}
intent_consensus_total{quorum_size}
intent_fallback_total{reason}
llm_vendor_timeout_total{vendor}
llm_vendor_circuit_open_total{vendor}
```

---

## 12. Tri-Match Classification Engine

### 12.1 Purpose

Tri-Match is a deterministic classifier for:

- query intent,
- service intent,
- funnel stage.

Per D-081, Tri-Match funnel-stage output is shadow-only at launch with Decision Layer weight 0. It must not mutate `ThreadState.sales_stage` directly.

### 12.2 Rule layers

Supported layers:

| Layer | Description | Shortcut eligible |
|---|---|---|
| exact | exact normalized phrase match | yes after gates |
| keyword | keyword sets and phrase clues | no by default |
| regex | regex patterns | yes after gates |
| pattern | structured pattern templates | yes after gates |
| fuzzy | approximate matching | no |
| semantic | embedding similarity | no |

Semantic and fuzzy can provide evidence but cannot shortcut.

### 12.3 Rule schema

```python
class TriMatchRule(BaseModel):
    rule_id: str
    version: str
    enabled: bool = True
    deprecated: bool = False
    layer: Literal["exact", "keyword", "regex", "pattern", "fuzzy", "semantic"]
    dimension: Literal["query_intent", "service_intent"]
    target: str
    pattern: str | None = None
    phrases: list[str] = []
    keywords_any: list[str] = []
    keywords_all: list[str] = []
    negative_keywords: list[str] = []
    requires_not_negated: bool = True
    suppress_if_hedged: bool = False
    suppress_if_counterfactual: bool = True
    context_conditions: dict[str, object] = {}
    confidence_base: float
    priority: int = 100
    rationale: str
    examples_positive: list[str]
    examples_negative: list[str]
```

### 12.4 Rule storage

Use Postgres `trimatch_rules`.

Additional calibration columns:

```text
times_matched
times_correct
times_overruled
empirical_precision
last_matched_at
approval_status
approved_by
is_shadow
created_by
```

### 12.5 Matching pipeline

```text
ProcessedMessage
  ↓
exact matcher
  ↓
keyword matcher
  ↓
regex matcher
  ↓
pattern matcher
  ↓
fuzzy matcher
  ↓
semantic matcher
  ↓
evidence aggregation
  ↓
TRG context adjustment
  ↓
negation/hedge/counterfactual suppression
  ↓
TriMatchResult
```

### 12.6 Evidence aggregation

```python
class TriMatchEvidence(BaseModel):
    rule_id: str
    layer: str
    target: str
    confidence: float
    span: str | None
    suppressed: bool = False
    suppress_reason: str | None = None

class TriMatchResult(BaseModel):
    query_intent: QueryIntentType | None
    service_intent: ServiceCategory | None
    confidence: float
    evidence: list[TriMatchEvidence]
    best_layer: str | None
    shortcut_candidate: bool
```

### 12.7 Negation examples

User says:

```text
I don't need ghostwriting, I need editing.
```

Expected:

- ghostwriting evidence suppressed by negation,
- editing recognized as service intent.

User says:

```text
I was thinking about marketing, but not right now.
```

Expected:

- marketing may be hedged or deprioritized,
- do not treat as ready-to-buy.

User says:

```text
If I had a finished book, I would ask for formatting.
```

Expected:

- formatting counterfactual suppressed as current service intent.

### 12.8 Shortcut promotion gates

Do not enable shortcuts at launch.

Shortcut layers can be promoted only when:

- precision floor passes,
- recall floor passes,
- eval subsets pass,
- rule calibration is stable,
- rollback exists,
- observability dashboard is green.

Floors:

```text
exact: precision ≥ 0.97 and recall ≥ 0.20
regex: precision ≥ 0.97 and recall ≥ 0.35
pattern: precision ≥ 0.97 and recall ≥ 0.45
```

Never shortcut on semantic or fuzzy layers.

### 12.9 Hot reload

Tri-Match active state must reload without app restart.

Algorithm:

```text
fetch approved enabled rules
  ↓
build new matcher indexes
  ↓
embed semantic phrases
  ↓
validate state
  ↓
atomic pointer swap
  ↓
record reload metric
```

Never mutate live matcher state in place.

### 12.10 Calibration counters

After Decision Layer finalizes:

- increment `times_matched` for matched rules,
- increment `times_correct` when final agrees,
- increment `times_overruled` when final disagrees,
- auto-deprecate rules with enough evidence and low empirical precision.

Metrics:

```text
trimatch_classification_seconds{layer}
trimatch_layer_distribution_total{layer}
trimatch_shortcut_total{enabled}
trimatch_overruled_total{rule_id}
trimatch_rule_precision{rule_id}
trimatch_reload_total
trimatch_reload_seconds
trimatch_active_rules{layer}
```

---

## 13. Funnel Signal Engine

### 13.1 Purpose

The Funnel Signal Engine is a sibling deterministic engine. It contributes funnel-stage evidence only.

It reuses Tri-Match infrastructure but has separate:

- rule corpus,
- database table or rule namespace,
- metrics,
- dashboard,
- mode flag,
- calibration counters.

### 13.2 Launch mode

```bash
FUNNEL_SIGNAL_MODE=shadow
```

Initial Decision Layer weight: `0`.

Meaning:

- run it,
- log it,
- evaluate it,
- do not let it change final decisions yet.

### 13.3 Rule partition

The original funnel rule corpus must be partitioned at ingest:

```text
funnel_stage_intents.json
  ↓
partition by section field
  ├─ funnel_signal_rules_userlang.json → Funnel Signal Engine
  └─ funnel_signal_rules_crm.json → future CRM Event Consumer
```

Drop metadata-only regex rules that belong in ThreadState, not text matching.

### 13.4 Funnel signal output

```python
class FunnelSignalResult(BaseModel):
    proposed_stage: SalesStage | None
    confidence: float
    evidence: list[TriMatchEvidence]
    mode: Literal["shadow", "active"]
```

### 13.5 Validation

- `send me the contract` should signal `agreement_requested`.
- `I'm ready to sign` should signal late-stage buying intent.
- `how much does editing cost?` should not jump to closed-won.
- CRM field rules must never fire against user chat text.

Metrics:

```text
funnel_signal_seconds{layer}
funnel_signal_stage_total{stage}
funnel_signal_overruled_total{rule_id}
funnel_signal_precision{rule_id}
funnel_signal_shadow_total
```

---

## 14. Decision Layer

### 14.1 Purpose

The Decision Layer combines all classification sources into a final decision.

Sources:

- Haiku,
- GPT-mini,
- DeepSeek,
- Tri-Match,
- Funnel Signal Engine.

Dimensions:

- query intent,
- service intent,
- funnel stage.

Tri-Match does not vote on funnel stage. Funnel Signal Engine does not vote on query/service intent.

### 14.2 Output schema

```python
class FinalIntentClassification(BaseModel):
    query_primary: QueryIntentType
    service_primary: ServiceCategory | None
    funnel_stage: SalesStage
    needs_clarification: bool
    confidence: float
    decision_method: str
    source_votes: dict[str, object]
    warnings: list[str]
```

### 14.3 Stage transition validator

Prevent invalid jumps.

Examples:

- `new → closed_won` blocked unless explicit accepted payment/agreement evidence exists.
- `quoted → exploring` allowed only if user rejects quote or changes scope.
- `nda_requested → agreement_requested` allowed when user asks for contract/agreement.

### 14.4 Failure behavior

| Failure | Response |
|---|---|
| one LLM down | continue with other sources |
| all LLMs down | Tri-Match fallback if confidence is safe; otherwise fallback response |
| Tri-Match unavailable | LLM ensemble continues |
| Funnel Signal unavailable | continue; log degradation |
| all sources disagree | pick highest weighted confidence, flag for review |

---

## 15. Phase 5 — Self-Improvement

### 15.1 Goal

Use operational data to improve deterministic classification safely.

### 15.2 Disagreement mining

Mine cases where:

- LLMs disagree,
- Tri-Match was overruled,
- user corrected the bot,
- confidence was low,
- invalid stage transitions were attempted,
- fallback response occurred.

### 15.3 Rule suggestion workflow

```text
disagreement examples
  ↓
cluster similar cases
  ↓
Sonnet suggests candidate rules
  ↓
Pydantic validation
  ↓
pending rule queue
  ↓
human approval
  ↓
shadow-on-shadow evaluation
  ↓
promotion or rejection
```

### 15.4 Auto-approval safety

Auto-approval is allowed only after manual workflow is proven and only for high-confidence suggestions.

Auto-approved rules still enter shadow mode first.

### 15.5 Eval subsets

Maintain labeled subsets:

- baseline intent,
- service intent,
- negation,
- hedge,
- counterfactual,
- multi-service,
- portfolio requests,
- pricing requests,
- document requests,
- ambiguous short messages.

---

## 16. Phase 6 — Agreement & NDA Engine

### 16.1 Goal

Generate legally sensitive documents with strict templates, typed parameters, deterministic verification, LLM verification, audit trails, and gated rollout.

### 16.2 Absolute rule

The LLM must never write legal clauses. It can help verify that rendered output matches expected parameters, but it must not create, rewrite, or improvise legal text.

### 16.3 Uploaded template inventory

#### Service Agreement template

Uploaded file: `BCP_Service_Agreement_Full.ejs`.

Major parameter expressions detected:

```text
logoPath
effectiveDate
abbreviation
clientFullName
clientPhone
clientEmail
clientLocation
filteredServices[].title
filteredServices[].items[].title
filteredServices[].items[].description
finalFee
totalFee
discountPercent
scheduleType
initialPercentage
remainingPercentage
numberOfMonths
installmentAmount
initialAmount
remainingAmount
advancePercentage
finalPercentage
beforeOrAfter
finalMilestoneService
milestones[].percentage
milestones[].beforeOrAfter
milestones[].description
signature
agreementDate
```

The template includes dynamic service sections through `filteredServices` and dynamic payment schedules through `scheduleType`.

Main legal sections:

1. Scope of Agreement
2. Term and Termination
3. Fees, Payments, and Refunds
4. Intellectual Property Rights
5. Confidentiality and Data Protection
6. Warranties and Representations
7. Liability and Indemnification
8. Governing Law and Dispute Resolution
9. Entire Agreement, Amendments, and Enforceability
10. Signatures and Execution

#### NDA template

Uploaded file: `BCP-NDA.ejs`.

Parameters:

```text
date
authorTitle
authorFullName
authorPhone
authorEmail
signature
```

Main legal sections:

1. Purpose of the Agreement
2. Definition of Confidential Information
3. Obligations of Confidentiality
4. Exceptions to Confidentiality
5. No Rights or License
6. No Obligation to Publish
7. Term and Termination
8. Governing Law and Dispute Resolution
9. Miscellaneous
10. Signature and Acknowledgment

### 16.4 Template technology decision

The architecture baseline prefers Python/Jinja2 with `StrictUndefined`, but the uploaded templates are EJS.

Choose one of these implementation paths:

#### Preferred path: convert EJS to Jinja2

Advantages:

- stays fully inside Python stack,
- uses `StrictUndefined`,
- avoids Node rendering dependency,
- simpler deployment.

Required controls:

- preserve legal text exactly,
- convert syntax only,
- run golden PDF regression before and after conversion,
- legal sign-off required for any content change.

#### Acceptable path: encapsulated EJS renderer

Advantages:

- preserves existing EJS templates exactly,
- faster initial parity with uploaded samples.

Required controls:

- EJS rendering service is internal-only,
- input schema validated in Python before call,
- rendered HTML returned to Python for PDF generation/storage,
- strict template versioning,
- golden output tests.

Recommended practical approach:

1. Start with encapsulated EJS rendering to match existing templates.
2. Build golden tests.
3. Convert to Jinja2 only after parity tests pass.

### 16.5 Document parameter schemas

#### NDA input

```python
class GenerateNDAInput(BaseModel):
    thread_id: UUID
    author_title: str
    author_full_name: str
    author_phone: str
    author_email: EmailStr
    signature: Literal["Jerry Miller", "Robert Williams"]
    date: date
```

#### Service Agreement input

```python
class AgreementServiceItem(BaseModel):
    title: str
    description: str

class AgreementService(BaseModel):
    title: str
    items: list[AgreementServiceItem]

class AgreementMilestone(BaseModel):
    percentage: Decimal
    before_or_after: Literal["before", "after", "upon"]
    description: str

class GenerateAgreementInput(BaseModel):
    thread_id: UUID
    accepted_quote_id: str
    logo_path: str
    effective_date: date
    abbreviation: str
    client_full_name: str
    client_phone: str
    client_email: EmailStr
    client_location: str
    filtered_services: list[AgreementService]
    final_fee: Decimal
    total_fee: Decimal
    discount_percent: Decimal = Decimal("0")
    schedule_type: PaymentScheduleType
    initial_percentage: Decimal | None = None
    remaining_percentage: Decimal | None = None
    number_of_months: int | None = None
    installment_amount: Decimal | None = None
    initial_amount: Decimal | None = None
    remaining_amount: Decimal | None = None
    advance_percentage: Decimal | None = None
    final_percentage: Decimal | None = None
    before_or_after: bool | None = None
    final_milestone_service: str | None = None
    milestones: list[AgreementMilestone] = []
    signature: Literal["Jerry Miller", "Robert Williams"]
    agreement_date: date
```

### 16.6 Agreement generation preconditions

Do not generate a service agreement unless:

- customer full name exists,
- customer email exists,
- customer phone exists,
- location exists or is explicitly waived,
- selected services exist,
- accepted quote exists,
- payment schedule exists,
- signature representative is approved,
- agreement mode allows generation.

### 16.7 NDA generation preconditions

Do not generate NDA unless:

- author full name exists,
- email exists,
- phone exists,
- title is selected or safely defaulted,
- signature representative is approved,
- NDA mode allows generation.

### 16.8 Rendering flow

```text
document tool invoked
  ↓
gating policy check
  ↓
load ThreadState and quote/document inputs
  ↓
validate Pydantic input
  ↓
render HTML from strict template
  ↓
render PDF
  ↓
extract text from PDF
  ↓
deterministic verification
  ↓
LLM verification, if mode requires
  ↓
hash document
  ↓
store in S3/object storage
  ↓
persist document metadata
  ↓
append thread event
  ↓
return secure URL or deferred status
```

### 16.9 Deterministic verifier

Verify:

- required names appear exactly,
- email appears exactly,
- phone appears exactly,
- selected services appear,
- fee values match accepted quote,
- payment schedule values match accepted quote,
- no unreplaced template tags remain,
- no `undefined`, `null`, `[object Object]`, or template syntax remains,
- page count is within expected range,
- signatures display correctly,
- PDF text extraction succeeds,
- document hash stored.

### 16.10 LLM verifier

Use Sonnet/Haiku verifier as a second check, not as author.

Verifier prompt asks:

- Does this rendered document match the supplied parameters?
- Are there missing client fields?
- Are there unreplaced placeholders?
- Did any legal clause appear malformed?
- Are all selected services listed?
- Are the fee/payment schedule values consistent with inputs?

Output schema:

```python
class DocumentVerificationResult(BaseModel):
    passed: bool
    confidence: float
    issues: list[str]
    requires_human_review: bool
```

### 16.11 Golden regression tests

For each template version:

- `golden_params.json`,
- rendered HTML snapshot,
- rendered PDF text snapshot,
- content hash,
- visual reference if needed.

Any template change requires:

- deterministic diff,
- golden test update,
- legal approval,
- version bump.

### 16.12 Document tools

```text
generate_nda.v1
generate_service_agreement.v1
verify_document.v1
get_document_status.v1
```

### 16.13 Document metrics

```text
document_generation_seconds{document_type}
document_generated_total{document_type,status}
document_verifier_rejected_total{document_type,reason}
document_deferred_total{document_type,mode}
document_unreplaced_placeholder_total{document_type}
document_pdf_render_failure_total{document_type}
```

---

## 17. Monitoring Platform — Prometheus and Grafana

### 17.1 Goal

Every phase and component must be observable before it is considered complete.

### 17.2 Prometheus setup

`ops/prometheus/prometheus.yml`:

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - /etc/prometheus/alerts.yml

scrape_configs:
  - job_name: bookcraft-api
    static_configs:
      - targets: ["host.docker.internal:8000"]

  - job_name: prometheus
    static_configs:
      - targets: ["localhost:9090"]
```

FastAPI metrics endpoint:

```python
from prometheus_client import make_asgi_app
from fastapi import FastAPI

app = FastAPI()
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)
```

### 17.3 Required dashboards

#### System Health Dashboard

Panels:

- request rate,
- error rate,
- p50/p95/p99 latency,
- WebSocket active connections,
- worker queue depth,
- DB pool usage,
- Redis latency,
- Elasticsearch latency,
- TEI latency.

#### Cost Dashboard

Panels:

- tokens by model,
- cost by model,
- cost by component,
- per-turn average cost,
- per-conversation average cost,
- cache hit ratio,
- cost anomaly alerts.

#### Quality Dashboard

Panels:

- intent confidence distribution,
- needs clarification rate,
- fallback rate,
- TRG compliance score,
- unanswered question count,
- user correction signals.

#### Tri-Match Dashboard

Panels:

- match layer distribution,
- rule precision,
- overruled rules,
- active rules by layer,
- shadow/shortcut activity,
- recall floors progress.

#### Funnel Signal Dashboard

Panels:

- proposed stages,
- shadow-stage signals,
- overruled signals,
- precision by stage,
- invalid transition attempts.

#### RAG Dashboard

Panels:

- retrieval latency,
- empty retrieval count,
- chunks returned,
- rejected chunks at ingestion,
- index version,
- ES health.

#### Pricing & Timeline Dashboard

Panels:

- quote requests by service,
- missing input rates,
- quote generation latency,
- accepted quote rate,
- quote range width distribution,
- timeline estimate latency.

#### Portfolio Dashboard

Panels:

- requests by service,
- matched specificity,
- unavailable responses,
- broken URL alerts,
- sample delivery count.

#### Document Dashboard

Panels:

- NDA requests,
- agreement requests,
- deferred requests,
- verifier failures,
- PDF render failures,
- unreplaced placeholders,
- document generation latency.

### 17.4 Alert examples

```yaml
groups:
  - name: bookcraft-alerts
    rules:
      - alert: HighAPILatency
        expr: histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m])) > 5
        for: 5m
        labels:
          severity: p1
        annotations:
          summary: "BookCraft API p95 latency above target"

      - alert: DocumentVerifierFailure
        expr: increase(document_verifier_rejected_total[5m]) > 0
        labels:
          severity: p0
        annotations:
          summary: "Document verifier rejected generated document"

      - alert: PortfolioBrokenURL
        expr: increase(portfolio_url_404_total[5m]) > 0
        labels:
          severity: p1
        annotations:
          summary: "Portfolio engine detected broken sample URL"

      - alert: RAGPricingLeak
        expr: increase(rag_rejected_chunk_total{reason="pricing_or_timeline_pattern"}[5m]) > 0
        labels:
          severity: p1
        annotations:
          summary: "RAG corpus ingestion found pricing/timeline leakage"
```

### 17.5 Metric naming standards

- Use snake_case.
- Use `_seconds` for duration.
- Use `_total` for counters.
- Keep labels bounded.
- Never put user IDs, emails, names, or thread IDs in labels.
- Use exemplars/traces for per-request debugging instead.

---

## 18. Security, Privacy, and Compliance

### 18.1 Secrets

Never commit secrets. Use a secret manager in deployed environments and `.env.local` only for local development.

Secrets:

```text
ANTHROPIC_API_KEY
OPENAI_API_KEY
DEEPSEEK_API_KEY
DATABASE_URL
REDIS_URL
ELASTICSEARCH_PASSWORD
SENDGRID_API_KEY or SES credentials
SENTRY_DSN
JWT_SIGNING_KEY
DOCUMENT_SIGNING_KEY
```

### 18.2 PII handling

PII includes:

- name,
- email,
- phone,
- location,
- manuscript content,
- payment details,
- generated documents.

Rules:

- Do not log raw PII.
- Redact structured logs.
- Store documents encrypted.
- Use signed short-lived URLs.
- Keep document access audit logs.
- Do not send manuscript content to unapproved vendors.
- Hosted DeepSeek API is not approved for production.

### 18.3 Rate limiting

Implement:

- per-IP limit,
- per-thread message limit,
- abuse detection,
- payload size limit,
- WebSocket connection limit.

### 18.4 Document safety

High-stakes document tools require:

- gating,
- deterministic verification,
- LLM verification where enabled,
- audit event,
- hash,
- template version,
- parameter snapshot,
- rollback/retraction runbook.

---

## 19. API and WebSocket Surface

### 19.1 Public endpoints

```text
GET /health
GET /ready
GET /metrics
POST /api/chat/thread
GET /api/chat/thread/{thread_id}
WS /ws/chat/{thread_id}
```

### 19.2 Admin endpoints

Protect behind auth.

```text
GET /admin/threads/{thread_id}
GET /admin/intent/logs
GET /admin/trimatch/rules
POST /admin/trimatch/rules/{rule_id}/approve
POST /admin/trimatch/rules/{rule_id}/reject
POST /admin/trimatch/reload
GET /admin/funnel/rules
POST /admin/funnel/reload
GET /admin/documents/{document_id}
GET /admin/deferred-tools
POST /admin/deferred-tools/{id}/approve
POST /admin/deferred-tools/{id}/reject
```

### 19.3 WebSocket message contracts

Inbound:

```json
{
  "type": "user_message",
  "thread_id": "...",
  "message": "I need editing for a 60k word romance novel",
  "client_event_id": "..."
}
```

Outbound:

```json
{
  "type": "assistant_bubble",
  "thread_id": "...",
  "sequence": 12,
  "content": "Great — for editing, I’ll need...",
  "segments": []
}
```

Tool result outbound:

```json
{
  "type": "tool_result",
  "tool": "get_pricing_quote.v1",
  "status": "success",
  "display": {
    "quote_id": "Q-...",
    "summary": "..."
  }
}
```

---

## 20. Testing Strategy

### 20.1 Test layers

| Layer | What to test |
|---|---|
| Unit | pure functions, schemas, calculators, matchers |
| Integration | DB, Redis, ES, TEI, tool dispatcher |
| Contract | tool input/output schemas |
| Property | state transitions, pricing invariants |
| Eval | intent/extraction quality |
| Golden | legal document rendering |
| Load | concurrent chat, RAG, pricing, portfolio |
| Chaos | LLM provider outage, ES down, TEI down |

### 20.2 Critical golden paths

1. User asks service question → RAG answer.
2. User asks price with missing inputs → clarification.
3. User provides missing inputs → quote tool returns range.
4. User confirms quote → quote accepted.
5. User asks for agreement → document request deferred in manual mode.
6. User asks for portfolio → curated samples returned.
7. User asks for ghostwriting samples → confidentiality-safe response.
8. User uses non-English message → polite redirect.
9. LLM provider down → degraded classification still works.
10. RAG pricing leak → CI blocks ingestion.

### 20.3 Acceptance tests by phase

#### Phase 1

- state CRUD,
- event hash chain,
- preprocessor spans/atoms/embedding,
- dispatcher idempotency,
- gating policy.

#### Phase 2

- single LLM conversation,
- RAG ingestion/retrieval,
- no price hallucination,
- extraction state deltas.

#### Phase 3

- streaming,
- TRG relations,
- pricing quote,
- portfolio samples,
- quote acceptance.

#### Phase 4

- ensemble quorum,
- Decision Layer,
- Tri-Match shadow,
- Funnel Signal shadow,
- calibration counters.

#### Phase 5

- disagreement mining,
- rule suggestions,
- approval queue,
- hot reload,
- auto-deprecation.

#### Phase 6

- NDA render,
- agreement render,
- verifier rejection on bad params,
- unreplaced placeholder detection,
- hash and storage,
- gated modes.

---

## 21. CI/CD and Pipeline Verification

### 21.1 CI stages

```text
lint
  ↓
type-check
  ↓
unit tests
  ↓
integration tests
  ↓
contract tests
  ↓
eval smoke
  ↓
security scan
  ↓
secret scan
  ↓
pipeline artifact verification
```

### 21.2 Data-shipping pipeline verifier

Every artifact pipeline must end with invariant checks.

Artifacts:

- RAG corpus,
- prompts,
- Tri-Match rules,
- Funnel rules,
- eval corpora,
- templates,
- pricing configs,
- portfolio registry.

Verifier examples:

| Artifact | Verifier |
|---|---|
| RAG corpus | no pricing/timeline patterns, valid chunks, embeddings present |
| Tri-Match rules | schema valid, regex compiles, examples pass, no duplicate IDs |
| Funnel rules | user/CRM partition valid, metadata-only rules removed |
| Templates | golden render passes, no placeholders, hash recorded |
| Pricing config | all rates approved, formulas parse, no negative outputs |
| Portfolio registry | URLs valid, no ghostwriting sample leakage |
| Prompts | schema references valid, forbidden claims absent |

CD promotion blocks if verifier fails.

---

## 22. Operational Runbooks

### 22.1 LLM vendor outage

1. Check vendor-specific circuit breaker metrics.
2. Confirm whether quorum still works with remaining vendors.
3. If two vendors fail, enable safe Tri-Match fallback only for high-confidence non-sensitive intents.
4. Disable document autonomy if verifier model unavailable.
5. Add incident event.

### 22.2 RAG quality drop

1. Check `rag_empty_result_total`.
2. Check ES health.
3. Check current alias version.
4. Roll alias back if new corpus caused issue.
5. Re-run ingestion verifier.

### 22.3 Pricing issue

1. Disable quote tool if incorrect numbers detected.
2. Identify pricing config version.
3. Roll back to prior config.
4. Recompute affected quotes.
5. Mark affected quotes as requiring human review.

### 22.4 Document generation issue

1. Immediately set `NDA_MODE=manual` and `AGREEMENT_MODE=manual`.
2. Stop autonomous sends.
3. Identify template version and document IDs.
4. Re-run verifier.
5. Notify human reviewer.
6. Patch template/config only after golden tests pass.

### 22.5 Portfolio broken URL

1. Remove or disable broken sample.
2. Reload portfolio map.
3. Verify URL health.
4. Add replacement sample if available.

---

## 23. Common Loopholes This Guide Closes

### 23.1 Price leakage loophole

Problem: RAG content and engine both mention prices.

Control: RAG ingestion rejects pricing/timeline patterns. Only Pricing & Timeline Engine returns numbers.

### 23.2 Legal hallucination loophole

Problem: LLM generates agreement text.

Control: LLM never writes legal text. Templates are rendered with typed parameters and verified.

### 23.3 Tool bypass loophole

Problem: LLM calls external APIs or mutates state directly.

Control: all tools go through dispatcher, validation, gating, idempotency, audit.

### 23.4 Funnel/Tri-Match confusion loophole

Problem: Tri-Match funnel-stage output directly changes conversation stage before calibration.

Control: D-081 allows funnel-stage voting but keeps it shadow-only with Decision Layer weight 0 until calibration.

### 23.5 Ghostwriting sample confidentiality loophole

Problem: bot shows ghostwriting samples.

Control: Portfolio Engine returns confidentiality-safe response for ghostwriting.

### 23.6 Shortcut-before-calibration loophole

Problem: Tri-Match shortcuts before enough evidence.

Control: shadow launch, recall+precision floors, eval subsets, no semantic/fuzzy shortcuts.

### 23.7 Template placeholder loophole

Problem: generated PDF contains `<%= clientEmail %>` or `undefined`.

Control: deterministic verifier rejects unreplaced placeholders and invalid values.

### 23.8 Broken portfolio link loophole

Problem: bot sends dead sample URL.

Control: portfolio URL validation and immediate alert on 404.

---

## 24. Final Build Order Summary

### Phase 0 — Project Foundation

- repo,
- dependencies,
- local infra,
- CI/CD,
- secrets,
- basic FastAPI,
- observability shell.

### Phase 1 — Foundation Layer

- domain types,
- FieldMeta,
- database schema,
- event log,
- Redis wrapper,
- Language Guard,
- Preprocessor,
- Tool Dispatcher,
- gating policy.

### Phase 2 — Intelligence Baseline

- single Haiku classifier,
- Combined Extraction,
- Elasticsearch RAG ingestion and retrieval,
- Sonnet response generation,
- formatter,
- baseline evals.

### Phase 3 — Sales Engines

- streaming,
- TRG,
- Pricing & Timeline Engine,
- Portfolio Request Engine,
- quote persistence,
- quote acceptance,
- production conversation tests.

### Phase 4 — Ensemble and Decision Intelligence

- Anthropic/OpenAI/DeepSeek adapters,
- race-with-quorum,
- Decision Layer,
- Tri-Match shadow,
- Funnel Signal Engine shadow,
- calibration counters.

### Phase 5 — Self-Improvement

- disagreement mining,
- Sonnet rule suggestions,
- approval queue,
- hot reload,
- auto-deprecation,
- shortcut promotion gates.

### Phase 6 — High-Stakes Documents

- NDA renderer,
- Service Agreement renderer,
- deterministic verifier,
- LLM verifier,
- PDF generation,
- S3 storage,
- gated rollout modes,
- golden tests.

---

## 25. Final Acceptance Definition

The implementation is complete only when:

- Every phase exit criterion passes.
- All tools have contract tests.
- RAG ingestion rejects pricing/timeline leakage.
- Pricing & Timeline Engine is the only source of numeric commercial values.
- Portfolio Engine returns only approved static samples.
- Ghostwriting sample requests are confidentiality-safe.
- NDA and Agreement generation are template-only and verified.
- Tri-Match and Funnel Signal run in shadow before influencing decisions.
- Decision Layer logs every source vote.
- Prometheus and Grafana show health, cost, quality, RAG, pricing, portfolio, document, Tri-Match, and funnel dashboards.
- Alerts exist for document failures, pricing leakage, broken portfolio URLs, LLM outages, latency breaches, and classifier regressions.
- All generated documents, quotes, tool calls, and state changes are audit-trailed.
- A new engineer can clone the repo, run local infra, run tests, ingest corpus, and execute the baseline conversation without undocumented steps.

---

## 26. Appendix A — Minimum Service Catalog Seed

```yaml
services:
  ghostwriting:
    display_name: Ghostwriting
    sample_policy: confidential
  editing_proofreading:
    display_name: Editing & Proofreading
    sample_policy: amazon_links
  cover_design_illustration:
    display_name: Cover Design & Illustration
    sample_policy: cover_registry
  interior_formatting:
    display_name: Interior Formatting
    sample_policy: amazon_links
  audiobook_production:
    display_name: Audiobook Production
    sample_policy: pending
  publishing_distribution:
    display_name: Publishing & Distribution
    sample_policy: amazon_links
  marketing_promotion:
    display_name: Marketing & Promotion
    sample_policy: amazon_links
  author_website:
    display_name: Author Website
    sample_policy: website_links
  video_trailer:
    display_name: Video Trailer
    sample_policy: trailer_links
```

---

## 27. Appendix B — Engine Ownership Matrix

| Fact type | Owner | Not allowed owner |
|---|---|---|
| price amounts | Pricing Engine | RAG, LLM prompt, Sonnet free text |
| timeline estimates | Timeline Engine | RAG, LLM prompt, Sonnet free text |
| service descriptions | RAG/domain knowledge | Pricing Engine |
| service tier names | RAG/domain knowledge + service catalog | ad hoc prompt text |
| quote acceptance | TRG + ThreadState | LLM memory only |
| customer identity | customers table + ThreadState | RAG |
| legal clauses | approved templates | LLM |
| portfolio samples | Portfolio Registry | LLM generated links |
| query/service intent | LLM ensemble + Tri-Match | Pricing Engine |
| funnel stage | LLM ensemble + Funnel Signal Engine | Tri-Match |

---

## 28. Appendix C — Minimum First Eval Corpus Categories

Create JSONL cases for:

```text
greeting
service explanation
pricing missing input
pricing complete input
timeline missing input
timeline complete input
portfolio cover request
portfolio website request
ghostwriting sample request
NDA request
agreement request
editing vs ghostwriting negation
hedged marketing interest
counterfactual formatting mention
multi-service request
ready-to-buy
complaint/objection
non-English redirect
```

Each case:

```json
{
  "id": "eval-001",
  "message": "I don't need ghostwriting, I need editing for my finished manuscript.",
  "thread_context": {},
  "expected": {
    "query_primary": "service_question",
    "service_primary": "Editing & Proofreading",
    "funnel_stage": "service_discovery",
    "suppressed_services": ["Ghostwriting"]
  }
}
```

---

## 29. Appendix D — Template Verification Checklist

For every generated NDA or Agreement:

- [ ] required customer/author fields present,
- [ ] selected services present,
- [ ] quote ID present in metadata,
- [ ] fee matches accepted quote,
- [ ] payment schedule matches accepted quote,
- [ ] no unreplaced EJS/Jinja tags,
- [ ] no `undefined`, `null`, or `[object Object]`,
- [ ] no empty signature representative,
- [ ] PDF render succeeded,
- [ ] extracted text non-empty,
- [ ] document hash stored,
- [ ] thread event appended,
- [ ] tool invocation logged,
- [ ] mode/gating recorded,
- [ ] verifier result stored.

---

## 30. Appendix E — Local Developer Success Path

A developer should be able to run:

```bash
make install
make up
make migrate
make ingest-rag
make ingest-portfolio
make verify-data
make test
make dev
```

Then open:

```text
http://localhost:8000/health
http://localhost:8000/metrics
http://localhost:3000
```

And run a smoke conversation:

```text
User: Hi, I need editing for my 60,000-word romance novel. How much would it cost?
Expected: Bot recognizes editing + pricing intent, calls pricing tool if rules are configured, or asks for missing pricing inputs. It must not invent prices.
```
