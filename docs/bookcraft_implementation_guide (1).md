# BookCraft AI Chatbot — Implementation Guide

> **Document Version:** 1.0 &nbsp;•&nbsp; **Status:** Approved for Execution &nbsp;•&nbsp; **Companion to:** *BookCraft AI Chatbot — Architecture Reference v2.0*
>
> This is the canonical implementation guide. It defines the **order, dependencies, and verification** of every build task across the system. The architecture reference defines **what** to build; this document defines **how** to build it without losing your way.

---

## Document Information

| Attribute | Value |
|---|---|
| Document type | Step-by-step implementation guide |
| Audience | Engineering teams (primary), tech leads, project managers |
| Companion document | Architecture Reference v2.0 (referenced as `AR §N.M`) |
| Cross-reference convention | Architecture: `AR §6.4`, decisions: `D-NNN`, risks: `R-NNN`, failure modes: `FM-NNN` |
| Format | Markdown, versioned in Git |

### How to use this guide

1. **Read once, linearly,** to understand the dependency structure
2. **Work through phases in order** — phase-N+1 has hard dependencies on phase-N exit criteria
3. **Each step has explicit tasks, validation, and common pitfalls** — do not skip validation
4. **Within a phase, parallelizable steps are marked `║`** — these can be assigned to different engineers
5. **The architecture reference is authoritative for schemas and decisions** — this guide does not duplicate them, it sequences their implementation

---

## Implementation Philosophy

Five operating principles guide every decision in this document:

1. **Build foundations once, well.** Phase 1 is unglamorous (schema, storage, dispatcher skeleton) but it determines whether everything else is fast or painful. Spending an extra step on FieldMeta provenance correctness is worth more than rushing to a chat demo.

2. **Working end-to-end before working perfectly.** Phase 2 deliberately ships a single-Haiku conversation loop. Quality improvements come in Phase 4. Don't optimize ahead of validation.

3. **Verify after every step, not after every phase.** Each step has a validation method. If validation fails, do not proceed. Compounding small errors is the #1 cause of late-stage rework.

4. **Observability is built alongside, not after.** Every step that adds a code path also adds the metric that observes it. Dashboards are stood up in Phase 1, not Phase 6.

5. **The risk register is real.** R-001 through R-020 in the architecture reference are not theoretical. Each step that touches a high-severity risk has explicit mitigation tasks.

---

## Glossary (essentials)

Full glossary in the Architecture Reference. Implementation-specific terms:

| Term | Definition |
|---|---|
| **Step** | A discrete unit of implementation with explicit goal, tasks, validation, and exit criteria |
| **Parallel marker** `║` | Steps that can be done concurrently within a phase |
| **Dependency** | A prior step that must complete before this step starts |
| **Validation** | The verification method that proves a step is done correctly |
| **Exit criteria** | The set of validations that allow advancing to the next phase |
| **Smoke test** | Minimal end-to-end check that core functionality works after deployment |
| **Acceptance test** | Comprehensive verification that a phase's goals are met |

---

## Table of Contents

- [Master Phase Map](#master-phase-map)
- [Prerequisites: Project Foundation](#prerequisites-project-foundation)
- [Phase 1: Foundation](#phase-1-foundation)
- [Phase 2: Intelligence](#phase-2-intelligence)
- [Phase 3: Production-Ready](#phase-3-production-ready)
- [Phase 4: Ensemble + Tri-Match](#phase-4-ensemble--tri-match)
- [Phase 5: Self-Improvement](#phase-5-self-improvement)
- [Phase 6: High-Stakes Documents](#phase-6-high-stakes-documents)
- [Cross-Phase Activities](#cross-phase-activities)
- [Appendices](#appendices)

---

## Master Phase Map

```
┌──────────────────────────────────────────────────────────────────────┐
│ Prerequisites: Project Foundation                                    │
│  - Repo, dev env, infra, secrets, observability, CI/CD               │
└──────────────────────────────┬───────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│ PHASE 1: Foundation                                                  │
│  Components 1, 3, 10 (skeleton), 13                                  │
│  - Storage, identity, dispatcher skeleton, preprocessing             │
│  Exit: Thread CRUD works, events log correctly, preprocessor flows   │
└──────────────────────────────┬───────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│ PHASE 2: Intelligence                                                │
│  Components 4 (basic), 5, 6 (basic)                                  │
│  - Single-Haiku intent + extraction; Sonnet response                 │
│  Exit: End-to-end conversation works on single LLM stack             │
└──────────────────────────────┬───────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│ PHASE 3: Production-Ready                                            │
│  Components 6 (full), 2, 7, 9                                        │
│  - Streaming, TRG, pricing/portfolio integration                     │
│  Exit: Soft-launch candidate; full state management; passes SLOs     │
└──────────────────────────────┬───────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│ PHASE 4: Ensemble + Tri-Match                                        │
│  Components 4 (revised), 11, 12 (basic)                              │
│  - 3-LLM ensemble + Decision Layer + Tri-Match shadow                │
│  Exit: Tri-Match votes alongside ensemble; calibration accumulating  │
└──────────────────────────────┬───────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│ PHASE 5: Self-Improvement                                            │
│  Component 12 (full)                                                 │
│  - Sonnet batch suggestions; manual approval; auto-approval gates    │
│  Exit: Tri-Match rule corpus growing organically with quality gates  │
└──────────────────────────────┬───────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│ PHASE 6: High-Stakes Documents                                       │
│  Component 8                                                         │
│  - NDA/agreement: manual → verifier-gated → autonomous (per D-051)   │
│  Exit: Autonomous document generation with bounded blast radius      │
└──────────────────────────────────────────────────────────────────────┘

Cross-Phase activities run continuously throughout all phases:
  Testing • Observability • Security hardening • Performance tuning •
  Documentation • DR drills • Capacity reviews
```

### Phase gates

Each phase transition is a quality gate. **No phase begins until the prior phase's exit criteria are 100% met.**

| Transition | Gate criteria |
|---|---|
| Prereq → 1 | Local dev environment runs; infra provisioned; CI/CD green |
| 1 → 2 | All Phase 1 acceptance tests pass; dashboards green |
| 2 → 3 | End-to-end conversation works; eval harness baselined |
| 3 → 4 | All SLOs met under load; soft-launch demo successful |
| 4 → 5 | Ensemble in production for 14+ days; calibration data accumulating |
| 5 → 6 | Tri-Match auto-correction running; manual approval validated |

---

## Prerequisites: Project Foundation

These steps must complete before Phase 1 begins. They are not part of the architectural component build but are required for everything that follows.

### P.1 Repository scaffolding

**Goal:** Single Git repository with mono-package layout supporting fast iteration.

**Tasks:**

1. Create Git repository (private, with branch protection on `main`)
2. Configure branch protection: required reviews, required CI, no force-push
3. Set up project layout:

```
bookcraft-chatbot/
├── README.md
├── pyproject.toml              # Single Python project
├── uv.lock                     # Dependency lock (uv preferred over pip-tools)
├── Makefile                    # Common commands
├── docker-compose.yml          # Local dev stack
├── .pre-commit-config.yaml
├── .github/
│   └── workflows/              # CI/CD
├── docs/                       # Architecture, this guide, ADRs
├── ops/
│   ├── dashboards/             # Grafana JSON
│   ├── alerts/                 # Alertmanager rules
│   └── runbooks/               # Incident playbooks
├── src/
│   ├── bookcraft/              # Application package
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── domain/             # Pure domain types (FieldMeta, ThreadState, etc.)
│   │   ├── components/         # Each architectural component as submodule
│   │   ├── tools/              # MCP tool implementations
│   │   ├── infra/              # DB, Redis, ES, TEI clients
│   │   ├── prompts/            # System prompts (cached)
│   │   ├── api/                # FastAPI routes
│   │   ├── ws/                 # WebSocket handlers
│   │   └── workers/            # Background jobs
│   └── alembic/                # DB migrations
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   ├── property/
│   └── load/
└── scripts/
    ├── dev/                    # Local dev helpers
    ├── ops/                    # Production scripts
    └── data/                   # Data migration utilities
```

4. Initialize `pyproject.toml` with Python 3.12+ requirement
5. Add `.gitignore` covering Python artifacts, IDE files, secrets, logs

**Validation:**
- `git clone` produces a working tree
- `tree -L 2` matches the layout above
- Branch protection rules verified in GitHub/GitLab UI

**Common pitfalls:**
- Splitting into multiple repositories early — resist this. A single repo simplifies CI, dependencies, and refactors.

---

### P.2 Development environment

**Goal:** Reproducible local environment that matches production semantics.

**Tasks:**

1. Install `uv` as the Python package manager: `curl -LsSf https://astral.sh/uv/install.sh | sh`
2. Pin Python: `uv python install 3.12`
3. Create virtual environment: `uv venv`
4. Install initial dependencies in `pyproject.toml`:

```toml
[project]
name = "bookcraft"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "pydantic>=2.7",
    "sqlmodel>=0.0.22",
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
    "opentelemetry-api>=1.25",
    "opentelemetry-sdk>=1.25",
    "opentelemetry-exporter-otlp>=1.25",
    "opentelemetry-instrumentation-fastapi>=0.46b0",
    "opentelemetry-instrumentation-sqlalchemy>=0.46b0",
    "sentry-sdk[fastapi]>=2.0",
    "prometheus-client>=0.20",
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

5. Install dependencies: `uv pip install -e ".[dev]"`
6. Download spaCy English model: `python -m spacy download en_core_web_sm`
7. Set up `pre-commit` with ruff, mypy, end-of-file-fixer, trailing-whitespace
8. Create Makefile with common commands:

```makefile
.PHONY: install dev test test-unit test-integration lint type fmt up down migrate

install:
	uv pip install -e ".[dev]"
	python -m spacy download en_core_web_sm
	pre-commit install

up:
	docker-compose up -d
	@sleep 3
	@$(MAKE) migrate

down:
	docker-compose down

dev:
	uvicorn bookcraft.api.main:app --reload --host 0.0.0.0 --port 8000

test:
	pytest -xvs tests/

test-unit:
	pytest -xvs tests/unit/

test-integration:
	pytest -xvs tests/integration/

lint:
	ruff check src/ tests/

type:
	mypy --strict src/

fmt:
	ruff format src/ tests/

migrate:
	alembic upgrade head
```

**Validation:**
- `make install` completes without errors
- `python -c "import bookcraft"` works
- `python -m spacy load en_core_web_sm` works
- `pre-commit run --all-files` produces clean output

**Common pitfalls:**
- Using `pip` instead of `uv` — `uv` is 10-100× faster and produces more reproducible installs
- Forgetting to download spaCy model — fails opaquely at first preprocessing call

---

### P.3 Local infrastructure (`docker-compose.yml`)

**Goal:** All infrastructure dependencies runnable locally with one command.

**Tasks:**

Create `docker-compose.yml`:

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: bookcraft
      POSTGRES_PASSWORD: bookcraft_dev
      POSTGRES_DB: bookcraft
    ports: ["5432:5432"]
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U bookcraft"]
      interval: 5s
      timeout: 3s
      retries: 5

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    volumes:
      - redis-data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]

  elasticsearch:
    image: docker.elastic.co/elasticsearch/elasticsearch:8.13.4
    environment:
      - discovery.type=single-node
      - xpack.security.enabled=false
      - "ES_JAVA_OPTS=-Xms1g -Xmx1g"
    ports: ["9200:9200"]
    volumes:
      - es-data:/usr/share/elasticsearch/data

  tei:
    image: ghcr.io/huggingface/text-embeddings-inference:cpu-1.5
    command: --model-id BAAI/bge-small-en-v1.5 --port 8080
    ports: ["8080:8080"]
    volumes:
      - tei-data:/data

  otel-collector:
    image: otel/opentelemetry-collector-contrib:latest
    command: ["--config=/etc/otel-collector-config.yaml"]
    volumes:
      - ./ops/otel-collector-config.yaml:/etc/otel-collector-config.yaml
    ports:
      - "4317:4317"   # OTLP gRPC
      - "4318:4318"   # OTLP HTTP

  prometheus:
    image: prom/prometheus:latest
    volumes:
      - ./ops/prometheus.yml:/etc/prometheus/prometheus.yml
    ports: ["9090:9090"]

  grafana:
    image: grafana/grafana:latest
    environment:
      GF_SECURITY_ADMIN_PASSWORD: admin
    volumes:
      - grafana-data:/var/lib/grafana
      - ./ops/dashboards:/etc/grafana/provisioning/dashboards
    ports: ["3000:3000"]

volumes:
  postgres-data:
  redis-data:
  es-data:
  tei-data:
  grafana-data:
```

**Validation:**
- `docker-compose up -d` brings everything up
- `docker-compose ps` shows all services healthy
- `psql postgresql://bookcraft:bookcraft_dev@localhost/bookcraft` connects
- `redis-cli ping` returns PONG
- `curl http://localhost:9200/_cluster/health` returns green
- `curl http://localhost:8080/embed -X POST -H "Content-Type: application/json" -d '{"inputs":"hello"}'` returns 384-dim vector

**Common pitfalls:**
- ES heap size insufficient for development — bump `ES_JAVA_OPTS` if you see OOM
- TEI model download takes a few minutes on first start — check container logs with `docker-compose logs tei`

---

### P.4 Production infrastructure provisioning

**Goal:** Production infrastructure provisioned and configured per `AR §3.5` topology.

**Tasks:**

1. Provision Postgres 16 instance with pgvector extension:
   - Primary + 1 read replica
   - Encryption at rest (AES-256)
   - Continuous WAL archiving to S3 for PITR (RPO 5 min per `AR §8.5`)
   - Connection limit 100, with pgbouncer in front

2. Provision Redis 7:
   - Cluster-ready single instance at launch (2 GB)
   - Persistence: RDB snapshot every 6 hours (cache only, rebuildable)
   - Encryption at rest and in transit

3. Provision Elasticsearch 8:
   - Single-node initially (10 GB storage, scalable to 50 GB)
   - Plan migration to 3-node cluster post-Phase 3

4. Provision S3 (or compatible) buckets:
   - `bookcraft-documents` (for PDF documents)
   - `bookcraft-archives` (for cold thread archives)
   - Versioning enabled
   - Cross-region replication for `bookcraft-documents`
   - SSE-KMS encryption

5. Deploy TEI sidecar:
   - CPU instance is sufficient at launch
   - Model: `BAAI/bge-small-en-v1.5` (384-dim)
   - Health check endpoint configured

6. Deploy DeepSeek V3 (per D-026, mandatory self-host):
   - Single A100 GPU instance or equivalent
   - Use the open-weight checkpoint
   - HTTP wrapper exposing OpenAI-compatible API
   - Internal-only network endpoint

7. Configure CDN / WAF (Cloudflare or equivalent):
   - TLS 1.3 mandatory
   - DDoS protection
   - Rate limiting per IP per minute (default 30, per AR Appendix D)

**Validation:**
- All services pass health checks from staging environment
- TLS connections verified (`openssl s_client`)
- Postgres replica lag verified < 1 second
- Backup restore tested in staging

**Common pitfalls:**
- Forgetting cross-region replication on documents bucket — DR will fail when needed
- Using the hosted DeepSeek API "just for now" — D-026 forbids this; do not start the slippery slope

---

### P.5 Secrets management

**Goal:** All secrets centrally managed; zero secrets in code or `.env` files in version control.

**Tasks:**

1. Choose secrets backend (HashiCorp Vault, AWS Secrets Manager, Doppler, etc.)
2. Configure secrets store with the following entries (per AR Appendix D):

```
ANTHROPIC_API_KEY
OPENAI_API_KEY
DEEPSEEK_API_KEY
DATABASE_URL (with credentials)
REDIS_URL (with credentials)
ELASTICSEARCH_PASSWORD
SENDGRID_API_KEY (or SES credentials)
SENTRY_DSN
JWT_SIGNING_KEY
DOCUMENT_SIGNING_KEY (for PDF signing in Phase 6)
```

3. Configure application to load secrets at startup via SDK (not environment variables)
4. Configure rotation policy: 90 days for API keys, 180 days for signing keys
5. Set up audit logging on the secrets backend
6. Implement local-dev override via `.env.local` (gitignored)

**Validation:**
- `git grep -i "api_key" src/` returns zero hits
- Application fails to start without secrets backend access (proves no fallback to defaults)
- Secret rotation tested in staging

**Common pitfalls:**
- Storing keys in `.env` files even temporarily — these end up in commit history
- Hardcoded fallback values in code — even for "dev defaults", this is forbidden

---

### P.6 Observability bootstrap

**Goal:** Telemetry pipeline running before any application code is deployed.

**Tasks:**

1. Configure OpenTelemetry Collector:
   - Receive OTLP gRPC and HTTP
   - Export traces to Tempo/Jaeger
   - Export metrics to Prometheus
   - Sample rate: 100% in dev, 10% in production

2. Configure Prometheus:
   - Scrape interval: 15s
   - Retention: 30 days
   - Service discovery for application instances

3. Configure Grafana:
   - Provision the 6 dashboards listed in `AR §7.3`
   - Import initial JSON definitions (start empty; populate as components ship)

4. Configure log aggregation (Loki or OpenSearch):
   - Structured JSON logs from all services
   - 30-day retention for application logs

5. Configure Sentry:
   - Project per environment (dev/staging/prod)
   - PII scrubbing rules for the structured fields

6. Configure Alertmanager:
   - Routes for P0 (page), P1 (urgent Slack), P2 (Slack), P3 (email digest)
   - Test alert firing end-to-end

**Validation:**
- `curl http://localhost:9090/api/v1/targets` shows targets up
- Test trace span visible in Tempo/Jaeger
- Test alert reaches PagerDuty/Slack
- Grafana login works; dashboards exist (even if empty)

**Common pitfalls:**
- Postponing observability until after launch — this guarantees no visibility during incidents
- Sampling traces too aggressively in dev — full sampling locally helps debugging

---

### P.7 CI/CD skeleton

**Goal:** CI runs on every commit; CD pipeline is configured but not yet deploying (no application yet).

**Tasks:**

1. GitHub Actions / GitLab CI workflow with these stages:
   - **Lint:** `ruff check`
   - **Type:** `mypy --strict`
   - **Unit tests:** `pytest tests/unit/`
   - **Integration tests:** `pytest tests/integration/` (with testcontainers)
   - **Security scan:** Snyk or Trivy
   - **Secret scan:** gitleaks
   - **Coverage report:** uploaded to Codecov

2. CD pipeline (configured, not yet active):
   - Trigger: push to `main`
   - Build: Docker image
   - Push: container registry
   - Deploy: blue/green to staging
   - Smoke test: health check endpoint
   - Promote: manual gate to production

3. Configure environment variables and secrets in CI:
   - Use environment-scoped secrets
   - Never expose production secrets to PR builds

4. Add status badges to README

**Validation:**
- A no-op PR triggers all CI steps
- All steps complete in < 10 minutes
- Failed lint/type/test blocks merge

**Common pitfalls:**
- Building Docker images on every commit — cache aggressively
- Running production CD on every PR — separate staging from production

---

### Prerequisites Exit Criteria

Do not proceed to Phase 1 until all of the following are true:

- [ ] Repository scaffolded with `pyproject.toml`, Makefile, `.pre-commit-config.yaml`
- [ ] Local development environment installs and runs (`make install && make up`)
- [ ] All infrastructure (Postgres+pgvector, Redis, ES, TEI) reachable from local dev
- [ ] Production infrastructure provisioned in staging environment
- [ ] DeepSeek V3 deployed and responding to health checks (D-026)
- [ ] Secrets management configured; zero secrets in repo
- [ ] OpenTelemetry, Prometheus, Grafana, Loki, Sentry all reachable
- [ ] CI runs on every commit; lint/type/test all green on empty repo
- [ ] CD pipeline configured (not yet active)
- [ ] Tracing pipeline tested end-to-end with a hello-world span

---

## Phase 1: Foundation

**Goal:** Storage, identity, dispatcher skeleton, and shared preprocessing — the contract layer everything else builds on.

**Components addressed:** 1, 3, 10 (skeleton), 13

**Critical constraint:** Every meaningful state field uses `FieldMeta`. Every state mutation goes through the event log. Every external call goes through the dispatcher. Shortcuts here cause weeks of pain in later phases.

---

### 1.1 Domain types

**Goal:** Establish the core type system before any code that uses it.

**Dependencies:** Prerequisites complete

**Reference:** `AR Appendix A`, `D-067`

**Tasks:**

1. Implement `Source` enum in `src/bookcraft/domain/source.py`:

```python
from enum import StrEnum

class Source(StrEnum):
    USER_STATED = "user_stated"
    USER_CONFIRMED = "user_confirmed"
    AI_EXTRACTED = "ai_extracted"
    CSR_ENTERED = "csr_entered"
    SYSTEM = "system"
```

2. Implement `FieldMeta` generic in `src/bookcraft/domain/field_meta.py`:
   - Pydantic v2 generic with `TypeVar`
   - `value`, `confidence`, `source`, `extracted_at`, `extracted_by`, `raw_excerpt`
   - `is_high_confidence()` method
   - Custom JSON serializer for backward-compatibility on enum and datetime

3. Implement domain enums:
   - `ServiceCategory` (9 services from BookCraft catalog)
   - `SubServices` (per-service sub-service lists)
   - `QueryIntentType` (18 query intents)
   - `SalesStage` (11 funnel stages)
   - `ProjectCategory`, `ManuscriptStatus`, `ContactMethod`, `ContactTime`

4. Write unit tests for serialization round-trip of `FieldMeta[str]`, `FieldMeta[int]`, `FieldMeta[datetime]`

**Validation:**
- `pytest tests/unit/domain/` passes
- `mypy --strict src/bookcraft/domain/` passes
- A `FieldMeta[EmailStr]` with invalid email fails validation at construction

**Common pitfalls:**
- Forgetting `Generic[T]` parameterization — types collapse to `Any`
- Custom validators that drop `raw_excerpt` — never lose this field; downstream legal audit needs it
- Default `confidence=1.0` is dangerous — use `0.0` default to force callers to set it explicitly

---

### 1.2 Database connection layer

**Goal:** Async Postgres connection pool with replica routing and observability.

**Dependencies:** 1.1

**Reference:** `AR §3.1`, `AR §3.2`

**Tasks:**

1. Implement `src/bookcraft/infra/database.py`:
   - Async engine via SQLAlchemy 2.0 + asyncpg
   - Primary pool (writes) and replica pool (reads)
   - `get_session()` async context manager
   - `get_replica_session()` for read-only queries
   - Connection pool metrics exported to Prometheus

2. Configure connection pooling:
   - Pool size: 20
   - Max overflow: 10
   - Pool timeout: 30 seconds
   - Pool pre-ping: enabled

3. Add `DATABASE_REPLICA_URL` env var support (falls back to primary if unset for local dev)

4. Configure Alembic for migrations:
   - `alembic init src/alembic`
   - Configure async migrations
   - Set `compare_type=True` for column type changes

**Validation:**
- Connection pool emits metrics: `db_pool_size`, `db_pool_checked_out`
- `make migrate` produces "no changes" on empty schema (Alembic baseline established)
- A read query routed to replica is verified in trace span

**Common pitfalls:**
- Using sync SQLAlchemy patterns in async code — silently blocks the event loop
- Forgetting `pool_pre_ping=True` — stale connections cause production errors
- Letting application code construct sessions directly — always use the context manager for proper cleanup

---

### 1.3 Redis client wrapper

**Goal:** Async Redis client with key namespacing and TTL conventions.

**Dependencies:** 1.2

**Tasks:**

1. Implement `src/bookcraft/infra/redis.py`:
   - Async client via `redis.asyncio`
   - Key namespacing: `bc:{env}:{component}:{key}`
   - Hash-tagged keys for cluster-readiness: `bc:{env}:thread:{thread_id}:graph` uses `{thread_id}` as hash tag

2. Define TTL constants in `src/bookcraft/config.py`:

```python
class RedisTTL:
    HOT_THREAD_HOURS = 24       # D-009 adjacent
    IDEMPOTENCY_HOURS = 24       # D-062
    RELATION_CACHE_HOURS = 24   # D-009
    LANG_DETECTION_HOURS = 168  # 1 week
```

3. Implement health check endpoint that pings Redis
4. Add Prometheus metrics: `redis_commands_total`, `redis_command_seconds`

**Validation:**
- `redis-cli keys 'bc:dev:*'` shows namespaced keys when used
- Health check returns 200 when Redis is up, 503 when down
- Connection failure causes graceful fallback (logged, not crashed) at higher layers

**Common pitfalls:**
- Forgetting hash tags — when you migrate to Redis Cluster later, multi-key operations break
- Using `client.set()` without TTL on cache keys — leads to unbounded growth

---

### 1.4 Customers table

**Goal:** Identity layer that supports cross-thread customer identification.

**Dependencies:** 1.2

**Reference:** `AR §6.1`, `AR Appendix B`, `D-003`

**Tasks:**

1. Implement `src/bookcraft/components/storage/customer.py`:
   - `Customer` SQLModel with all fields from `AR Appendix B`
   - Indexes on `email`, `phone`, `has_signed_agreement`
   - Soft-delete via `deleted_at`

2. Generate Alembic migration: `alembic revision --autogenerate -m "create customers table"`
3. Apply migration: `alembic upgrade head`

4. Implement `CustomerRepository` with methods:
   - `find_by_email(email)`
   - `find_by_phone(phone)`
   - `find_or_create_anonymous()` (for threads before identity captured)
   - `merge(source_id, target_id)` (for de-dup)

5. Write integration tests using testcontainers with a real Postgres instance

**Validation:**
- Migration up/down works cleanly
- `find_by_email` returns None for unknown emails (not exception)
- Soft-deleted customers are excluded from default queries

**Common pitfalls:**
- Forgetting partial indexes on `WHERE deleted_at IS NULL` — soft-deleted rows degrade query performance
- Using string "deleted" status — soft-delete via timestamp is more flexible (you can query "deleted before X")

---

### 1.5 Threads table and ThreadState

**Goal:** Authoritative store for conversation state with optimistic locking.

**Dependencies:** 1.4

**Reference:** `AR §6.1`, `AR Appendix A.2`, `AR Appendix B`, `D-001`, `D-002`

**Tasks:**

1. Implement `ThreadState` Pydantic model in `src/bookcraft/domain/thread_state.py`:
   - `schema_version: int = 1`
   - `personal: PersonalInfo`, `project: ProjectInfo`, `origin`, `samples`, `consultation`, `commercial`, `documents`, `project_status`, `rolling_summary`
   - All inner classes with `FieldMeta`-wrapped fields per `AR Appendix A.2`

2. Implement `Thread` SQLModel with JSONB `state` column

3. Implement `ThreadRepository`:
   - `get(thread_id)` — returns `(Thread, ThreadState)` tuple
   - `update_state(thread_id, mutator, expected_version)` — optimistic lock retry up to 3x
   - `create(customer_id, origin)`

4. Implement schema migration framework:
   - `MIGRATORS: dict[int, Callable[[dict], dict]]`
   - On read, if `state["schema_version"] < current_version`, apply migrators in sequence
   - Persist upgraded state on next write

5. Generate and apply Alembic migration for the `threads` table

**Validation:**
- `update_state` with stale version raises `OptimisticLockError`
- Retry-on-conflict succeeds within 3 attempts under realistic contention
- Schema migration test: write v1 state, read with v2 code, verify upgrade

**Common pitfalls:**
- Mutating state directly without going through `update_state` — bypasses event log
- Optimistic lock retries that don't re-read state — must fetch fresh state, re-apply mutator, retry
- Migrators that aren't pure functions — testing becomes impossible

---

### 1.6 Hash-chained event log

**Goal:** Tamper-evident audit log of every state-changing action.

**Dependencies:** 1.5

**Reference:** `AR §6.1`, `D-001`

**Tasks:**

1. Implement `ThreadEvent` SQLModel:
   - Fields per `AR Appendix B`
   - `prev_hash` (the previous event's `content_hash`)
   - `content_hash` = SHA-256 of canonical JSON of (sequence, event_type, actor, payload, prev_hash)
   - Partition by `created_at` monthly

2. Implement `EventLogRepository`:
   - `append(thread_id, event_type, actor, payload, confidence)` — atomic with state update
   - `get_chain(thread_id, since_sequence)` — for verification

3. Implement chain verification:
   - `verify_chain(thread_id) -> bool` — recompute hashes, compare
   - Add admin endpoint `/admin/threads/{id}/verify-chain` (auth-gated)

4. Critically: ensure event append happens in the same transaction as state update via SQLAlchemy session

5. Add Prometheus metric: `thread_events_append_seconds`

**Validation:**
- Append 100 events, verify chain — passes
- Manually edit one event in DB, verify chain — fails (proves tamper detection)
- State update + event append are atomic: kill the process mid-call, verify no orphan state without event

**Common pitfalls:**
- Computing hash from non-canonical JSON — different orderings produce different hashes
- Appending events outside the state transaction — race conditions on sequence numbers
- Storing hashes as `bytea` — use `CHAR(64)` hex strings for human-debuggability

---

### 1.7 Update thread state pattern

**Goal:** A single, consistent pattern for state mutations that covers locking, event log, and Redis cache invalidation.

**Dependencies:** 1.5, 1.6

**Tasks:**

1. Implement `update_thread_state` async function in `src/bookcraft/components/storage/state.py`:

```python
async def update_thread_state(
    session: AsyncSession,
    thread_id: UUID,
    mutator: Callable[[ThreadState], ThreadState],
    event_type: str,
    actor: Literal["user", "ai", "csr", "system"],
    payload: dict,
    confidence: float | None = None,
    max_retries: int = 3,
) -> StateUpdateResult:
    """The ONE pattern for state mutations.
    Atomic with event log; invalidates Redis cache; respects optimistic lock."""
    for attempt in range(max_retries):
        thread, state = await get_thread_with_state(session, thread_id)
        try:
            new_state = mutator(state)
        except StateMutationError as e:
            return StateUpdateResult.error(reason=str(e))
        
        try:
            await _atomic_update(session, thread, new_state, event_type, actor, payload, confidence)
            await invalidate_thread_cache(thread_id)
            return StateUpdateResult.success(new_state, thread.version + 1)
        except OptimisticLockError:
            if attempt == max_retries - 1:
                raise
            continue
```

2. Implement `invalidate_thread_cache(thread_id)` — Redis del + pub/sub broadcast for multi-worker scenarios
3. Add Prometheus metrics: `state_update_seconds`, `state_update_retries_total`, `state_update_outcomes_total{outcome=...}`
4. Write property-based tests with hypothesis: random sequences of mutations preserve consistency

**Validation:**
- Concurrent updates to same thread serialize correctly (no lost updates)
- Cache invalidation observable via Redis monitor
- Retry metric increments under contention

**Common pitfalls:**
- Mutators that have side effects beyond state — they must be pure; side effects belong elsewhere
- Forgetting cache invalidation on the failure path — partial updates leave stale cache

---

### 1.8 Language guard

**Goal:** Detect non-English messages and politely redirect.

**Dependencies:** 1.3, 1.5

**Reference:** `AR §6.3`, `D-010`, `D-011`, `D-012`, `D-013`

**Tasks:**

1. Implement `src/bookcraft/components/language_guard.py`:
   - Detection cascade: cache check → length guard → ASCII heuristic → lingua-py → low-conf default
   - 11 candidate languages per `D-010`
   - Re-detection cadence: first 3 turns + every 10th + opportunistic on long messages

2. Implement non-English redirect templates in `src/bookcraft/prompts/non_english_redirects.py` (per `AR Appendix H.1`)

3. Implement attempt counter:
   - Increment on each non-English turn
   - Reset on successful English turn
   - At 5 consecutive attempts, flag thread as `unqualified` (D-012)

4. Add Prometheus metrics:
   - `language_detection_seconds{source}`
   - `language_detection_results_total{language}`
   - `non_english_redirects_total{language}`

**Validation:**
- Test corpus of 100 messages in each of the 11 languages: ≥ 95% accuracy
- ASCII fast path correctly identifies "hi" as English in < 1ms
- After 5 non-English turns, thread state shows `sales_stage=unqualified`

**Common pitfalls:**
- Trusting lingua-py confidence scores too aggressively — short messages have low signal; default to "en" generously
- Hardcoding the 11-language list — define in a single source of truth (config or constants module)

---

### 1.9 Shared preprocessing layer (Component 13)

**Goal:** Single preprocessing pass per turn produces a `ProcessedMessage` consumed by all downstream components.

**Dependencies:** 1.3 (Redis for embedding cache), 1.8 (language guard)

**Reference:** `AR §6.13`, `AR Appendix A.5`, `D-066`

**Tasks:**

1. Implement `src/bookcraft/components/preprocessor/processed_message.py` — Pydantic model per `AR Appendix A.5`

2. Implement TEI client `src/bookcraft/infra/tei_client.py`:
   - Async HTTP client with timeout and retry
   - `embed(text: str) -> list[float]` and `embed_batch(texts: list[str]) -> list[list[float]]`
   - Cache embeddings on text-hash key in Redis (TTL 1 hour)
   - Health check endpoint
   - Prometheus metric `embedder_latency_seconds`

3. Implement `DeterministicPreExtractor` in `src/bookcraft/components/preprocessor/atoms.py`:
   - Email: regex + Pydantic `EmailStr` validation
   - Phone: `phonenumbers.PhoneNumberMatcher`
   - URLs: regex
   - Currency: locale-aware regex
   - Dates: dateparser
   - Word/page counts: pattern match (e.g., `r'\b\d{1,3}(?:,\d{3})*\s*(?:words|pages)\b'`)

4. Implement `Preprocessor` in `src/bookcraft/components/preprocessor/preprocessor.py`:
   - Lazy-load spaCy model at module import
   - Pipeline: normalize → spaCy parse → negation spans → atoms → embedding
   - Returns `ProcessedMessage`

5. Implement negation span detector:
   - Trigger words: "not", "never", "no", "without", "instead of", "rather than"
   - Span ends at sentence boundary or at conjunction "but"/"however"
   - Mark negated tokens with `negated=True`

6. Add Prometheus metrics:
   - `preprocessor_seconds` (histogram)
   - `preprocessor_atoms_extracted_total{atom_type}`
   - `preprocessor_negation_spans_total`

**Validation:**
- Processing a 100-token message completes in < 50ms (p95)
- spaCy model loaded successfully on application startup
- Test message "I'm not interested in pricing" produces negation span covering "interested in pricing"
- Email "user@example.com" reliably extracted to atoms

**Common pitfalls:**
- Loading spaCy model in request path — must be loaded at startup
- TEI cache key collisions — include language in the cache key (or commit to English-only)
- Negation detection that doesn't respect sentence boundaries — "I'm not interested. But ghostwriting sounds great" should not negate the second sentence

---

### 1.10 MCP dispatcher skeleton

**Goal:** Centralized tool dispatcher framework. No actual tools yet — just the dispatcher contract.

**Dependencies:** 1.7 (state updates for write tools)

**Reference:** `AR §6.10`, `D-061`, `D-062`, `D-063`, `D-064`

**Tasks:**

1. Implement `Tool` abstract base class in `src/bookcraft/tools/base.py`:
   - Generic `Tool[TInput, TOutput]`
   - Abstract `execute(params, context) -> TOutput`
   - `to_anthropic_schema()` method
   - `tool_class: ToolClass` enum field

2. Implement `ToolContext` Pydantic model with `thread_id`, `turn_sequence`, `invoked_by`, `correlation_id`, `idempotency_key`

3. Implement `ToolDispatcher` in `src/bookcraft/tools/dispatcher.py`:
   - Tool registry (code-based per `D-061`)
   - `invoke(tool_name, params, context) -> ToolResult`
   - Input validation via Pydantic
   - Output validation via Pydantic
   - Gating policy check (see 1.11)
   - Idempotency cache (Redis, 24h TTL per `D-062`)
   - Circuit breaker (5 failures, 60s recovery)
   - Timeout + exponential backoff retry
   - Audit log every invocation

4. Implement `CircuitBreaker` class with closed/open/half-open states

5. Implement `tool_invocation_logs` SQLModel and `ToolInvocationLogRepository`

6. Add Prometheus metrics:
   - `tool_invocations_total{tool_name, status}`
   - `tool_duration_seconds{tool_name}`
   - `tool_circuit_breaker_state{tool_name}`
   - `tool_validation_failures_total{tool_name, validation_phase}`

**Validation:**
- Register a no-op test tool, invoke it, verify audit log row written
- Force a circuit breaker open after 5 failures, verify subsequent calls fail fast
- Idempotency: invoke same tool with same idempotency_key twice, verify cached result returned

**Common pitfalls:**
- Tool versioning forgotten now — adding versioning later requires breaking changes; bake `.v1` into tool name from day 1 (`D-064`)
- Error handling that swallows tool failures silently — every failure path must log and emit metric
- Audit logging that's optional / configurable — it must always run

---

### 1.11 Gating policy

**Goal:** Enforces autonomous vs. human-gated rules per environment.

**Dependencies:** 1.10

**Reference:** `AR §6.10`, `D-051`

**Tasks:**

1. Implement `GatingPolicy` in `src/bookcraft/tools/gating.py`:
   - Read configuration from environment variables
   - For each tool: returns `allowed`, `deferred_to_human`, or `denied`

2. Configuration env vars (per AR Appendix D):
   - `NDA_MODE`: manual | verifier_gated | autonomous (default: manual)
   - `AGREEMENT_MODE`: manual | verifier_gated | autonomous (default: manual)

3. Implement `DeferredToolInvocation` SQLModel and queue
4. Implement deferred SLA calculation: 4h business / 24h overnight per `D-063`
5. Add expiration job that fires every hour to mark expired invocations

**Validation:**
- With `NDA_MODE=manual`, invoking `generate_nda.v1` returns deferred result
- With `NDA_MODE=autonomous`, invoking proceeds normally
- Invalid mode value causes startup failure (fail loudly, never assume defaults)

**Common pitfalls:**
- Treating gating as optional middleware — it must be in the dispatcher's main path
- Allowing per-call override of mode — gating is per-environment, not per-call

---

### 1.12 Phase 1 acceptance tests

**Goal:** Comprehensive verification that Phase 1 is complete and correct.

**Dependencies:** All of 1.1-1.11

**Tasks:**

1. Write integration test suite in `tests/integration/phase1/`:
   - `test_thread_lifecycle.py` — create thread, update state 10 times, verify event chain integrity
   - `test_optimistic_locking.py` — concurrent updates serialize correctly
   - `test_schema_migration.py` — v1 state read by v2 code upgrades correctly
   - `test_language_guard.py` — corpus of 100 multi-language messages classified correctly
   - `test_preprocessor.py` — atom extraction, negation, embedding caching
   - `test_dispatcher.py` — register test tool, invoke, verify audit + metrics
   - `test_gating.py` — manual mode defers, autonomous mode proceeds

2. Write smoke test script `scripts/dev/smoke_test_phase1.py`:
   - End-to-end: create thread → preprocess message → update state → verify event log → verify cache invalidation

3. Load test:
   - 50 concurrent threads, 10 turns each
   - Verify no state corruption
   - Verify p95 state update < 30ms

4. Chaos test:
   - Kill Postgres mid-update → verify graceful failure
   - Kill Redis → verify state still functions (degraded)

**Validation:**
- All tests in suite pass
- Smoke test runs in < 10 seconds
- Load test completes without errors
- Chaos test demonstrates expected degradation

---

### Phase 1 Exit Criteria

Do not proceed to Phase 2 until all of the following are true:

- [ ] All Phase 1 acceptance tests pass in CI
- [ ] Code coverage for Phase 1 components ≥ 80%
- [ ] All Phase 1 components emit Prometheus metrics
- [ ] Grafana shows live metrics from a running instance
- [ ] OpenTelemetry traces show end-to-end request paths
- [ ] Schema migration tested with v1→v2 dummy upgrade
- [ ] Hash chain verification passes on a 100-event sequence
- [ ] Optimistic locking validated under contention
- [ ] Language guard accuracy ≥ 95% on labeled corpus
- [ ] Preprocessor p95 < 50ms
- [ ] Dispatcher audit log captures every invocation (verify by SQL count)

---

## Phase 2: Intelligence

**Goal:** End-to-end conversation works on a single LLM stack — single Haiku for intent, single Haiku for extraction, single Sonnet for response.

**Components addressed:** 4 (basic), 5, 6 (basic)

**Critical constraint:** This phase ships a *working* chatbot. Quality improvements (ensemble, Tri-Match, TRG context, streaming, formatting) come in Phases 3-4. Resist scope creep here.

---

### 2.1 Anthropic client wrapper

**Goal:** Reusable Anthropic client with prompt caching, retry, and metrics.

**Dependencies:** P.5 (secrets)

**Reference:** `AR §3.3`

**Tasks:**

1. Implement `src/bookcraft/infra/anthropic_client.py`:
   - Wrap official Anthropic Python SDK
   - Default timeouts: 30s for sync, 60s for streaming
   - Retry: 3 attempts with exponential backoff for transient errors
   - Prompt caching support (5-minute ephemeral cache per `D-016`)

2. Add Prometheus metrics:
   - `anthropic_input_tokens_total{model, component, kind}` (kind: cache_read, cache_write, fresh)
   - `anthropic_output_tokens_total{model, component}`
   - `anthropic_cost_dollars_total{model, component}`
   - `anthropic_call_seconds{model, component}`
   - `anthropic_cache_hit_ratio{component}` (gauge)

3. Implement cost calculation:
   - Use rate table per AR §3.3
   - Tag every call with `component` label for attribution

4. Add structured logging on every call: `model`, `component`, `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `latency_ms`

**Validation:**
- Test call to Haiku returns structured response
- Cache hit observed on 2nd identical call within 5 minutes
- Metrics visible in Prometheus
- Cost tracking matches expected calculation

**Common pitfalls:**
- Forgetting to set `cache_control` on cacheable system prompts — pay full price every call
- Retrying non-idempotent calls — Anthropic API is generally idempotent for typical use, but verify per-call
- Logging full prompt content — PII risk; log token counts and IDs instead

---

### 2.2 Single-LLM intent classification (basic)

**Goal:** Working intent classification on Haiku alone. No ensemble, no Tri-Match, no TRG context yet.

**Dependencies:** 2.1, 1.5 (thread state)

**Reference:** `AR §6.4`, `AR §6.4.3`, `AR Appendix A.3`, `AR Appendix F.1`

**Tasks:**

1. Implement `IntentClassification` and sub-models per `AR Appendix A.3`

2. Define `INTENT_TOOL` schema in `src/bookcraft/components/intent/schema.py`:
   - Tool name: `classify_intent`
   - Input schema: `IntentClassification.model_json_schema()`

3. Author intent classification system prompt in `src/bookcraft/prompts/intent_classification.txt`:
   - BookCraft service catalog
   - 18 query intent definitions
   - 11 sales funnel stages
   - Stickiness rules
   - Multi-intent extraction rules
   - `needs_clarification` flag semantics

4. Implement `IntentClassifier` in `src/bookcraft/components/intent/classifier.py`:
   - Single Haiku call with `tool_choice={"type": "tool", "name": "classify_intent"}`
   - System prompt cached via `cache_control: ephemeral`
   - User context: state summary, recent turns (last 6), current message
   - Returns `IntentClassification`

5. Add Prometheus metrics:
   - `intent_classification_seconds`
   - `intent_invalid_stage_transition_total{from, to}`

**Validation:**
- Test corpus of 50 labeled messages: ≥ 80% accuracy on primary query intent
- Stage transition validator catches invalid transitions (test: discovery → SALE)
- needs_clarification correctly fires on ambiguous test messages

**Common pitfalls:**
- Including too much state in the user prompt — cache miss every call; include only what changes
- Validating output too leniently — Pydantic validation must be strict; bad outputs are bugs in the prompt or model

---

### 2.3 Combined extraction (basic)

**Goal:** Single Haiku call extracts questions, personal, project, services, commercial signals, sample/consultation requests.

**Dependencies:** 2.1, 1.9 (preprocessor for atoms)

**Reference:** `AR §6.5`, `AR Appendix A.4`, `D-030` through `D-037`

**Tasks:**

1. Implement `ExtractionResult` and all sub-models per `AR Appendix A.4`
2. Define `EXTRACTION_TOOL` schema in `src/bookcraft/components/extraction/schema.py`
3. Author extraction system prompt in `src/bookcraft/prompts/extraction.txt`:
   - All 7 categories of extractable data
   - Strict literalism rule (don't extract unstated values)
   - Already-known context format
   - Per-field confidence requirements

4. Implement `Extractor` in `src/bookcraft/components/extraction/extractor.py`:
   - Pre-extraction stage uses `ProcessedMessage.deterministic_atoms`
   - Atoms passed into Haiku prompt as "facts already extracted"
   - Single Haiku call with cached system prompt
   - Returns `ExtractionResult`
   - max_tokens: 2048 per `D-030`

5. Implement post-extraction validation:
   - Drop fields with confidence < 0.5 per `D-031`
   - Validate service catalog membership

6. Add Prometheus metrics:
   - `extraction_seconds`
   - `extraction_fields_per_turn{category}`
   - `extraction_validation_failures_total{field}`

**Validation:**
- Test message "Hi, I'm Sarah, my email is sarah@example.com, working on a 80,000-word fantasy novel, need a quote" extracts:
  - 1 question (related_to: pricing)
  - personal.name = "Sarah", personal.email = "sarah@example.com"
  - project.word_count = 80000, project.genre = "fantasy"
  - 1 service mention (likely ghostwriting)
- Empty/trivial messages produce empty `ExtractionResult`
- Confidence < 0.5 fields are dropped before return

**Common pitfalls:**
- Re-extracting fields already in state — wastes tokens; the prompt MUST include "already known" context
- LLM inventing email addresses — the strict literalism rule must be in the prompt and validated post-hoc

---

### 2.4 State delta application

**Goal:** Apply extraction results to thread state via narrow MCP tools.

**Dependencies:** 2.3, 1.10 (dispatcher)

**Reference:** `AR §6.5` state application, `AR §7.4`, narrow tools list

**Tasks:**

1. Implement narrow state-update tools in `src/bookcraft/tools/state_updates/`:
   - `update_personal.v1`
   - `update_project.v1`
   - `add_service_interest.v1`
   - `record_consultation_request.v1`
   - `record_sample_request.v1`
   - `record_quote.v1`
   - `mark_lead_created.v1`
   - `flag_for_escalation.v1`

2. Each tool:
   - Inherits from `Tool[TInput, TOutput]`
   - `tool_class = ToolClass.WRITE_AUTONOMOUS`
   - Uses `update_thread_state` pattern from 1.7
   - Wraps each new value in `FieldMeta` with appropriate `Source`

3. Implement `StateUpdater` orchestrator in `src/bookcraft/components/extraction/updater.py`:
   - Takes `ExtractionResult` and dispatches to appropriate tools
   - Implements no-overwrite-of-higher-confidence rule
   - Handles negation flag suppression for service mentions per `D-036`
   - Auto-escalates `mentioned → considering` per `D-032`
   - Refuses `considering → committed` without commercial signal per `D-033`

4. Register all state-update tools in dispatcher
5. **Critically:** these tools are *not* exposed to Sonnet; they're orchestrator-only

**Validation:**
- Apply test extraction with name "Sarah" — verify FieldMeta source is `ai_extracted`, raw_excerpt populated
- Apply two extractions in sequence with same email at confidence 0.8 then 0.95 — verify second overwrites first (higher confidence wins)
- Apply extraction with negated service mention — verify mention recorded but interest_level NOT escalated

**Common pitfalls:**
- Forgetting to invalidate Redis cache after state update — handled by 1.7's `update_thread_state` pattern, but verify integration
- Letting the LLM pick which tool to invoke — these are orchestrator-only; LLM never sees them

---

### 2.5 Elasticsearch RAG setup

**Goal:** ES index for RAG retrieval with hybrid BM25 + dense vector search.

**Dependencies:** P.3 (ES infra)

**Reference:** `AR §7.1`

**Tasks:**

1. Define ES index mapping in `src/bookcraft/infra/elasticsearch_setup.py`:
   - `text` field (BM25)
   - `embedding` field (dense_vector, 384 dims, cosine similarity)
   - `metadata` object (service, category, doc_type, source_url)
   - `created_at`, `updated_at` timestamps

2. Implement `RAGIngester` in `src/bookcraft/components/rag/ingester.py`:
   - Read source documents (Markdown files in `data/rag-corpus/`)
   - Chunk into 200-token segments with 50-token overlap
   - Embed each chunk via TEI (batch)
   - Index to ES

3. Initial corpus contents per `AR §7.1`:
   - Service descriptions and process docs
   - FAQs and policy docs
   - Past resolved conversations (anonymized)
   - Portfolio metadata
   - Pricing engine context (capabilities, NOT actual prices)

4. Implement `RAGRetriever` in `src/bookcraft/components/rag/retriever.py`:
   - Hybrid query: BM25 + dense vector
   - Reciprocal Rank Fusion for ranking
   - Top-k = 8, max 200 tokens per chunk
   - Reuse query embedding from `ProcessedMessage` (no re-embedding)

5. Add Prometheus metrics: `rag_retrieval_seconds`, `rag_chunks_returned`

**Validation:**
- Ingest sample corpus, verify ES `_count` matches expected
- Test query "what's your editing process?" returns relevant chunks (manual review)
- p95 retrieval latency < 250ms per `AR §4.2`

**Common pitfalls:**
- Token budget bloat — hard cap at 1,600 tokens (8 chunks × 200)
- Ingesting actual prices — RAG context for pricing is engine-driven; only capabilities go in RAG
- Re-embedding query text — must reuse `ProcessedMessage.embedding`

---

### 2.6 Sonnet response generation (basic)

**Goal:** Sonnet generates the user-facing reply. No streaming yet, no formatting yet.

**Dependencies:** 2.1, 2.2, 2.3, 2.5

**Reference:** `AR §6.6`, `AR Appendix F.3`

**Tasks:**

1. Author response generation system prompt template in `src/bookcraft/prompts/response_generation.txt`:
   - Brand voice (placeholder; marketing team customizes per `D-042`)
   - Hard rules: never invent prices/timelines/availability/CSR names
   - Outstanding-question priority directive
   - Repetition acknowledgment directive
   - Response structure (acknowledgment, answer, context, forward-step)
   - Word count guidance (60-180 words typical)
   - Forbidden formatting (no emoji, bold, headers — formatter rejects these)

2. Implement `ResponseGenerator` in `src/bookcraft/components/response/generator.py`:
   - Build prompt blocks: state summary, intent, extraction, RAG, recent turns, current message
   - Single Sonnet call (non-streaming for now)
   - Cached system prompt
   - max_tokens: 600

3. Add Prometheus metrics:
   - `sonnet_response_seconds`
   - `sonnet_input_tokens` (histogram)
   - `sonnet_output_tokens` (histogram)
   - `sonnet_response_word_count` (histogram)

**Validation:**
- Test conversation: "Hi, I need editing for my novel" → reasonable response within 4 seconds
- Output respects word count guidance (60-180 words for normal turns)
- No emoji, bold, or headers in output

**Common pitfalls:**
- System prompt that's too long — pay caching cost forever; trim ruthlessly
- Including the entire thread state in user prompt — only include what matters; the rolling summary covers older context

---

### 2.7 End-to-end conversation loop

**Goal:** A complete request-response cycle: HTTP message → orchestrator → response.

**Dependencies:** 2.2, 2.3, 2.4, 2.6

**Tasks:**

1. Implement `ConversationOrchestrator` in `src/bookcraft/components/orchestrator.py`:
   - Phase 1: Pre-flight (language, state load, preprocess)
   - Phase 3: Run intent and extraction in parallel via `asyncio.gather`
   - Phase 5: Routing decision (skip Sonnet on `needs_clarification`)
   - Phase 6: Sonnet response (non-streaming for now)
   - Phase 8: Async post-response (state updates, event log)

2. Implement HTTP API endpoint in `src/bookcraft/api/conversation.py`:
   - POST `/conversation/{thread_id}/message`
   - Request body: `{"text": "..."}`
   - Response body: `{"reply": "...", "thread_id": "...", "turn_sequence": N}`
   - Returns 200 with reply, 503 on degraded, 400 on validation

3. Add OpenTelemetry tracing:
   - Root span per request
   - Child spans per phase
   - Attributes: thread_id, turn_sequence, language, intent.primary, etc.

4. Add Prometheus metrics:
   - `conversation_turn_seconds` (histogram)
   - `conversation_turn_outcomes_total{outcome}`

**Validation:**
- Send 10 turns through endpoint, verify each returns valid response
- Trace shows all phases as child spans
- Concurrent requests for different threads don't interfere
- Concurrent requests for same thread serialize correctly

**Common pitfalls:**
- Forgetting to await the post-response work properly — fire-and-forget is fine but errors must be caught and logged
- HTTP timeout shorter than Sonnet timeout — proxy returns 504 while Sonnet is still working

---

### 2.8 Eval harness baseline

**Goal:** Continuous quality measurement on a labeled corpus.

**Dependencies:** 2.7

**Reference:** `AR §8.1`

**Tasks:**

1. Build initial labeled corpus:
   - 100-200 example messages
   - For each: expected `query.primary`, expected `service.primary_service`, expected `funnel.stage`, expected extraction fields
   - Span the 18 query intents and 9 services
   - Include negation cases, multi-intent cases, repeat cases

2. Implement `tests/eval/run_eval.py`:
   - Iterate corpus
   - Call orchestrator
   - Compare against labels
   - Compute precision/recall per intent type
   - Output JSON report

3. Schedule weekly eval run via CI:
   - Run against current production prompts
   - Alert on accuracy regression > 5pp

4. Establish baseline:
   - Run eval, record current accuracy
   - This is the floor; quality must not drop below it

**Validation:**
- Eval runs to completion in < 10 minutes
- Accuracy on primary query intent ≥ 80% (acceptable for Phase 2; Phase 4 ensemble will improve)
- Per-intent precision/recall reported in JSON

**Common pitfalls:**
- Corpus contamination — never use eval examples in prompt few-shots
- Optimizing the corpus to fit the model — the corpus represents reality; the model fits the corpus

---

### Phase 2 Exit Criteria

- [ ] All Phase 2 acceptance tests pass in CI
- [ ] End-to-end conversation works via HTTP endpoint
- [ ] Eval baseline established at ≥ 80% accuracy on primary intent
- [ ] Cost per turn measured at ~$0.025-0.030 (within Phase 4 ensemble cost ceiling)
- [ ] Cache hit rate ≥ 80% on cached system prompts (intent, extraction, response)
- [ ] No PII in application logs (verified by sampling)
- [ ] All state updates via narrow tools (verified by SQL: every state diff has matching `tool_invocation_log` entry)
- [ ] Sonnet response p95 < 4s
- [ ] Stage transition validation rejects invalid transitions

---

## Phase 3: Production-Ready

**Goal:** Soft-launch candidate. Streaming, full formatting, TRG, pricing/portfolio integration. The system feels like a real product.

**Components addressed:** 6 (full), 2, 7, 9

---

### 3.1 WebSocket gateway

**Goal:** WebSocket-based bidirectional communication with the user's browser.

**Dependencies:** 2.7

**Reference:** `AR §3.5`, `AR §6.6` streaming

**Tasks:**

1. Implement `src/bookcraft/ws/gateway.py`:
   - FastAPI WebSocket route at `/ws/conversation/{thread_id}`
   - Origin validation (allowlist from `WS_ALLOWED_ORIGINS`)
   - Authentication via thread-scoped JWT (24h TTL per `AR §7.5`)
   - Connection lifecycle: connect → authenticate → loop → disconnect

2. Define WebSocket message types:
   - **Inbound (user):** `{"type": "message", "text": "..."}`
   - **Outbound:** `{"type": "typing_start"}`, `{"type": "typing_stop"}`, `{"type": "message_bubble", "bubble_index": N, "text": "...", "rich_segments": [...]}`, `{"type": "error", "code": "...", "message": "..."}`

3. Configure deployment with sticky sessions (each thread pinned to one worker for the WebSocket lifetime)

4. Implement reconnect protocol: client may reconnect with same thread_id; server resumes from last sent bubble

5. Add Prometheus metrics:
   - `ws_active_connections` (gauge)
   - `ws_connection_seconds` (histogram)
   - `ws_messages_received_total{direction}`

**Validation:**
- Connect via test client (e.g., `websocat`), authenticate, send message, receive response
- Origin denial works (try connecting from disallowed origin)
- Token expiry causes reconnect prompt

**Common pitfalls:**
- Forgetting sticky sessions — bubbles arrive on different workers, ordering breaks
- Holding open connections without heartbeat — load balancer drops idle connections silently

---

### 3.2 Response formatter

**Goal:** Deterministic post-processor that transforms Sonnet's markdown into chat-ready bubbles.

**Dependencies:** 2.6

**Reference:** `AR §6.6`, `D-040`

**Tasks:**

1. Implement `ResponseFormatter` in `src/bookcraft/components/response/formatter.py`:
   - Sanitize: emoji strip, special char normalize, bold strip, header strip
   - Paragraph split: blank-line split with min-length merging
   - Bubble chunk: 500-character max per `D-040`
   - Rich segments: emails, URLs, phones detected and tagged

2. Implement `RichSegment` Pydantic model with `start`, `end`, `kind`, `href`

3. Add deduplication for overlapping segments (phone match inside email match, etc.)

4. Add Prometheus metrics:
   - `formatter_bubble_count` (histogram)
   - `formatter_rich_segments_total{kind}`

**Validation:**
- Test markdown with embedded emoji → emoji stripped
- 1,200-char response → 3 bubbles of ~400 chars each
- Email "user@example.com" tagged with `kind=email`, `href=mailto:...`
- Phone matched inside email is not double-tagged

**Common pitfalls:**
- Regex for emoji that misses extended ranges — use the comprehensive Unicode ranges in `AR §6.6`
- Splitting paragraphs too aggressively — short paragraphs (< 30 chars) should merge into the prior

---

### 3.3 Humanized inter-bubble pacing

**Goal:** Bubbles arrive with natural typing-rhythm pauses.

**Dependencies:** 3.1, 3.2

**Reference:** `AR §6.6`, `D-041`

**Tasks:**

1. Implement `HumanizedPacing` class in `src/bookcraft/components/response/pacing.py`:
   - `delay_for(previous_bubble_text) -> int` (ms)
   - Formula: `transition_ms (600) + word_count × 180 + question_bonus (400) + long_paragraph_bonus (500)`
   - Clamp: 800ms min, 7000ms max

2. Wire into WebSocket emission flow:
   - Emit `typing_start` immediately when delay begins
   - Sleep `delay_ms / 1000`
   - Emit `typing_stop`
   - Emit `message_bubble`

3. Add A/B test infrastructure (config-driven):
   - Allow per-environment override of `base_ms_per_word`, `transition_ms`
   - Log selected variant per turn

**Validation:**
- 9-word greeting bubble: ~2.2s delay
- 19-word answer bubble (ending in "?"): ~4.4s delay
- 24-word long paragraph: ~5.4s delay
- All within clamp bounds

**Common pitfalls:**
- Forgetting to send `typing_stop` before message — frontend shows persistent "typing..." indicator
- Calculating delay from current bubble instead of previous — feels off-rhythm

---

### 3.4 Greeting templates

**Goal:** High-confidence greeting messages skip Sonnet and respond with templates.

**Dependencies:** 3.2

**Reference:** `AR §6.6`, `D-043`, `AR Appendix H.2`

**Tasks:**

1. Implement template module in `src/bookcraft/prompts/greeting_templates.py` with the 3 starter templates per `AR Appendix H.2`

2. Implement `maybe_template_response()` in orchestrator:
   - Check `intent.query.primary == GREETINGS`
   - Confidence ≥ 0.9
   - Funnel stage NOT in {proposal, high_intent}
   - First matching trigger keyword

3. If template applies, skip Sonnet call entirely; emit template bubble through formatter

4. Add Prometheus metric: `template_response_total{template}`

**Validation:**
- "hi" message in lead stage triggers greeting template, skips Sonnet
- "hi" in proposal stage falls through to Sonnet (custom response expected)
- "hi there how much for editing?" does not trigger template (multi-intent; not pure greeting)

**Common pitfalls:**
- Templates feel robotic across long conversations — limit to truly trivial messages
- Triggering on substring match without word boundaries — "thinks" matches "think"

---

### 3.5 Tool use in Sonnet response

**Goal:** Sonnet invokes pricing/timeline/portfolio/consultation tools as part of generation.

**Dependencies:** 1.10 (dispatcher), 3.2 (formatter)

**Reference:** `AR §6.6` tool use, `AR §6.7`, `AR §6.9`

**Tasks:**

1. Implement context-filtered tool list builder per `AR §6.6`:
   - Always available: pricing, timeline, portfolio, consultation
   - Funnel-stage gated: nda (LEAD/PROPOSAL/HIGH_INTENT), service_agreement (HIGH_INTENT/SALE)

2. Wire tool list into Sonnet call's `tools` parameter

3. Implement tool-use loop:
   - First Sonnet call with tools
   - If response has `tool_use` blocks, dispatch each through `ToolDispatcher`
   - Feed tool results back as `tool_result` blocks
   - Continue until no more tool_use (typically 1-2 iterations)

4. Hard rules for Sonnet (in system prompt):
   - Never retry failed tools
   - Never fabricate tool results
   - On error response, pivot conversation to lead capture

5. Add Prometheus metrics:
   - `sonnet_tool_use_iterations` (histogram)
   - `sonnet_tool_invocations_total{tool_name}`

**Validation:**
- "How much for editing 80,000-word fantasy?" triggers `get_pricing_quote.v1` invocation
- Tool result fed back; final Sonnet response uses the result
- Tool failure produces lead capture pivot, not fabricated response

**Common pitfalls:**
- Allowing infinite tool-use loops — cap iterations at 5 to prevent runaway
- Sonnet fabricating tool results when temperature > 0 — keep temperature at 0 for tool-use

---

### 3.6 TRG: embedding storage

**Goal:** TRG nodes (turns) and edges (relations) stored with vector search support.

**Dependencies:** 1.5 (state), 1.9 (embeddings)

**Reference:** `AR §6.2`, `AR Appendix B`

**Tasks:**

1. Implement `GraphNode` and `GraphEdge` SQLModels with monthly partitioning per `AR Appendix B`
2. Generate Alembic migration with HNSW index on embedding column (m=16, ef_construction=64 per `D-007`)
3. Implement `TRGRepository`:
   - `add_node(thread_id, node_type, text, embedding, questions, metadata)`
   - `add_edge(thread_id, source_id, target_id, relation, confidence, classifier)`
   - `get_hot_graph(thread_id, limit=24)` — most recent N nodes
   - `find_similar_nodes(thread_id, embedding, threshold=0.85)`

4. Implement Redis hot graph cache:
   - Key: `bc:{env}:trg:{thread_id}:hot`
   - Value: list of last 24 node summaries
   - TTL: 24 hours

**Validation:**
- HNSW index used in `EXPLAIN ANALYZE` of similarity query
- Hot graph load p95 < 5ms (Redis hit)
- Hot graph fallback to Postgres p95 < 50ms

**Common pitfalls:**
- HNSW index parameters too aggressive — m=64 is excessive; m=16 is the right balance
- Storing full embeddings in Redis — too much memory; store node summaries only

---

### 3.7 TRG: 3-tier relation classifier

**Goal:** Relations between turns classified efficiently via cache → fast features → Haiku LLM.

**Dependencies:** 3.6

**Reference:** `AR §6.2`

**Tasks:**

1. Implement Tier 1: Redis cache lookup
   - Key: `bc:{env}:trg:relation:{hash(prev_text, current_text)}`
   - TTL: 24 hours per `D-009`
   - Hit rate target: ~70% in steady state

2. Implement Tier 2: Fast features
   - Cosine similarity between embeddings
   - Sequence distance (turns between)
   - Speaker pattern (user→bot, bot→user, user→user)
   - Logistic regression or simple rule-based decision
   - Confidence threshold for fast-path acceptance

3. Implement Tier 3: Haiku LLM micro-call
   - Cached on text-pair hash
   - Returns relation label + confidence
   - ~5% of turns in steady state

4. Implement `RelationClassifier` orchestrator that routes through tiers

5. Add Prometheus metrics:
   - `trg_relation_classification_seconds{classifier}` (histogram)
   - `trg_relation_classifier_distribution_total{classifier}` (counter)

**Validation:**
- Test corpus of 50 turn pairs with labeled relations: tier 2 accuracy ≥ 75%, tier 3 accuracy ≥ 90%
- Cache hit rate ≥ 70% in load test
- p95 latency: tier 1 < 5ms, tier 2 < 30ms, tier 3 < 200ms

**Common pitfalls:**
- Cache key collisions across threads — include thread context in cache key
- Treating tier 2 confidence absolutely — use threshold; below threshold, escalate to tier 3

---

### 3.8 TRG: compliance scoring

**Goal:** Score whether AI response addressed the user's outstanding questions.

**Dependencies:** 3.7

**Reference:** `AR §6.2`

**Tasks:**

1. Implement `ComplianceScorer` in `src/bookcraft/components/trg/compliance.py`:
   - Inputs: outstanding questions list, AI response text
   - Compute embedding similarity per question vs. response
   - Score = max similarity for each question, averaged

2. Stage-aware threshold per `AR §6.2`:
   - Initial inquiry: 0.55, Discovery: 0.60, Lead: 0.62, Proposal: 0.70, High intent: 0.72

3. If score below threshold, flag turn for response repair signal in next turn's context

4. Add Prometheus metric: `trg_compliance_score{sales_stage}` (histogram)

**Validation:**
- AI response that ignores question scores low (≤ 0.4)
- AI response that addresses question scores high (≥ 0.8)
- Stage-aware threshold differentiation works (test in proposal vs. inquiry)

**Common pitfalls:**
- Computing compliance on every turn even when no outstanding questions — wasteful; check first
- Treating 0.0 as "no questions" vs "completely failed" — distinguish via question_count

---

### 3.9 TRG: compaction

**Goal:** Hot graph stays bounded; older turns fold into rolling summary.

**Dependencies:** 3.6

**Reference:** `AR §6.2`, `D-008`

**Tasks:**

1. Implement compaction trigger:
   - Check after each turn whether hot graph > 24 nodes
   - If yes, fold oldest 12 into a `system_summary` node

2. Implement summary generation:
   - Haiku call with the 12 nodes' text
   - Output: 200-token summary
   - Update `ThreadState.rolling_summary` (keep last 3 summaries)
   - Persist `system_summary` node in Postgres for replay/audit

3. Trim Redis hot graph to remaining 12 nodes + 1 summary node
4. Run compaction async (post-response phase)

5. Add Prometheus metrics:
   - `trg_compaction_total`
   - `trg_compaction_seconds`
   - `trg_graph_size_nodes` (histogram)

**Validation:**
- Run 30-turn conversation, verify compaction triggers at turn 25 (24+1)
- Verify rolling summary contains gist of compacted turns
- Hot graph size stays ≤ 24 over long conversations

**Common pitfalls:**
- Compacting on the request path — must be async (post-response); blocks user otherwise
- Forgetting to update Redis — Postgres compacted but Redis still has old nodes

---

### 3.10 TRG: stage-aware behavior

**Goal:** Per-stage configuration drives compliance thresholds and escalation triggers.

**Dependencies:** 3.8

**Reference:** `AR §6.2` stage-aware behavior

**Tasks:**

1. Implement `StageConfig` per `AR §6.2`:
   ```python
   STAGE_CONFIGS = {
       INITIAL_INQUIRY: StageConfig(0.80, 0.25, 0.55, 5, 4),
       DISCOVERY: StageConfig(0.78, 0.28, 0.60, 4, 3),
       LEAD: StageConfig(0.75, 0.30, 0.62, 3, 3),
       PROPOSAL: StageConfig(0.72, 0.32, 0.70, 2, 2),
       HIGH_INTENT: StageConfig(0.70, 0.35, 0.72, 2, 2),
       SALE: StageConfig(0.70, 0.35, 0.72, 1, 1),
   }
   ```

2. Implement escalation triggers:
   - Outstanding questions exceed `unaddressed_alert` threshold
   - Repetition count exceeds `repetition_alert` threshold
   - Trigger emits `flag_for_escalation.v1` tool invocation

3. Add Prometheus metrics:
   - `trg_unaddressed_questions_total{sales_stage}`
   - `trg_escalation_triggers_total{reason}`

**Validation:**
- Force 3 unaddressed questions in proposal stage → escalation triggered
- Force 2 unaddressed in lead stage → no escalation (threshold not met)

**Common pitfalls:**
- Hardcoded thresholds — keep them in `STAGE_CONFIGS` dict for easy tuning
- Triggering escalation multiple times per thread — track flag in state

---

### 3.11 Pricing/timeline engine integration

**Goal:** MCP tool integration with the existing pricing/timeline engine via HTTP wrapper.

**Dependencies:** 1.10 (dispatcher), 3.5 (Sonnet tool use)

**Reference:** `AR §6.7`, `AR Appendix C.1`, `D-049`

**Tasks:**

1. Implement `PricingQuoteRequest` and `PricingQuoteResponse` per `AR Appendix C.1`
2. Implement `TimelineEstimateRequest` and `TimelineEstimateResponse` per `AR Appendix C.2`

3. Implement `GetPricingQuoteTool` and `GetTimelineEstimateTool` in `src/bookcraft/tools/pricing/`:
   - Subclass `Tool[TInput, TOutput]`
   - `tool_class = ToolClass.READ`
   - HTTP client to existing pricing engine
   - Timeout 5s, max retries 2

4. Implement pre-pricing validation per `D-046`:
   - Function `should_invoke_pricing(state, extraction) -> tuple[bool, str | None]`
   - If sizing/category/genre missing, return clarifying question

5. Implement quote persistence per `AR §6.7`:
   - On successful quote response, append to `ThreadState.commercial.quotes`
   - Generate event log entry

6. Implement quote auto-acceptance per `D-048`:
   - When TRG classifies next turn as `confirms` after a quote, mark `Quote.accepted = True`

7. Register tools in dispatcher
8. Add to Sonnet's available tools list

**Validation:**
- Mock pricing engine returns range; tool output validated by Pydantic
- Pre-pricing validation produces clarifying question on missing word_count
- Quote in state visible after successful invocation
- TRG `confirms` after quote auto-accepts the quote

**Common pitfalls:**
- Allowing tool to return single price without range — schema validation must enforce range
- Quote storage that doesn't preserve quote_id — downstream agreement generation can't reference

---

### 3.12 Portfolio request engine

**Goal:** MCP tool returning curated portfolio gallery URLs.

**Dependencies:** 1.10 (dispatcher), 3.5 (Sonnet tool use)

**Reference:** `AR §6.9`, `AR Appendix C.3`, `D-056` through `D-060`

**Tasks:**

1. Implement portfolio map YAML in `data/portfolio_map.yaml` per `AR §6.9`:
   - 30-50 entries per `D-057`
   - One default per service plus category and genre specializations

2. Implement `PortfolioMap` Pydantic model with `find()` method (cascading specificity)

3. Implement `GetPortfolioSamplesTool`:
   - Tool class: READ
   - Loads map at startup; reloads on file change (file watcher)
   - Returns gallery URL with `matched_specificity` flag

4. Implement multi-sample handling per `D-058`:
   - Cap 3 sample types per turn
   - Parallel tool invocations for multi-sample requests

5. Implement sample tracking:
   - Record delivered URL in `ThreadState.samples.requests`
   - TRG sees subsequent turns reference what was delivered

6. Add Prometheus metrics:
   - `portfolio_request_total{sample_type, matched_specificity}`
   - `portfolio_url_404_total` (alert immediately on > 0)

**Validation:**
- Request "fantasy covers" with category Fiction → returns specific gallery
- Request "covers" without genre → falls back to category-level
- Request "samples" with no context → falls back to general
- Map file edit → reload triggers, no restart needed

**Common pitfalls:**
- Hardcoding URLs in code — must be in YAML for marketing team to edit
- Allowing dynamic gallery generation — D-056 forbids it; static curated only

---

### 3.13 Phase 3 acceptance tests

**Goal:** Comprehensive verification that Phase 3 is complete.

**Dependencies:** All of 3.1-3.12

**Tasks:**

1. End-to-end test suite in `tests/integration/phase3/`:
   - WebSocket conversation with streaming bubbles
   - Pricing tool invocation with mocked engine
   - Portfolio tool invocation
   - TRG relation classification across multi-turn conversation
   - Compaction triggered after 25 turns

2. Load test with 50 concurrent threads
3. Measure p50/p95/p99 latency for each component vs. SLO targets in `AR §4.2`
4. Demo conversation script for soft-launch stakeholders

**Validation:**
- All tests pass
- Latency SLOs met
- Demo runs cleanly

---

### Phase 3 Exit Criteria

- [ ] All Phase 3 acceptance tests pass
- [ ] WebSocket streaming with humanized pacing working
- [ ] TRG operational (relations, compliance, compaction)
- [ ] Pricing/timeline engine integration tested
- [ ] Portfolio map deployed with 30+ entries
- [ ] Soft-launch demo successful
- [ ] Per-component p95 latencies meet `AR §4.2` SLOs
- [ ] Cost per turn measured and within budget
- [ ] Eval harness accuracy improved by ≥ 5pp from Phase 2 baseline

---

## Phase 4: Ensemble + Tri-Match

**Goal:** Three-LLM ensemble with Decision Layer, plus Tri-Match in shadow mode accumulating calibration data.

**Components addressed:** 4 (revised), 11, 12 (basic)

**Critical constraint:** Tri-Match runs in shadow mode (`TRIMATCH_MODE=shadow`). It votes alongside the ensemble; the Decision Layer uses its vote but never shortcuts. This entire phase exists to gather data for safe Phase 5+ shortcut promotion.

---

### 4.1 LLM adapter pattern

**Goal:** Unified interface for the three LLM vendors.

**Dependencies:** 2.2

**Reference:** `AR §6.4.2`

**Tasks:**

1. Implement `LLMClassifier` abstract base class in `src/bookcraft/components/intent/adapters/base.py`:
   - `name: str`
   - `timeout_ms: int`
   - `async classify(message, thread, recent_turns) -> IntentClassification`

2. Refactor existing Haiku classifier into `ClaudeHaikuClassifier(LLMClassifier)`

3. Define `ClassifierVote` dataclass with source, classification, latency_ms, error fields

**Validation:**
- Existing Haiku-only path still works after refactor
- All Phase 2 tests pass

---

### 4.2 OpenAI GPT-5.4 mini integration

**Goal:** GPT-5.4 mini as the second ensemble member.

**Dependencies:** 4.1, P.5 (OpenAI key)

**Reference:** `AR §3.3`, `D-025`

**Tasks:**

1. Implement `GPT5MiniClassifier(LLMClassifier)` in `src/bookcraft/components/intent/adapters/openai.py`:
   - Use OpenAI Python SDK
   - Model: `gpt-5.4-mini`
   - Function calling with `INTENT_FUNCTION_SCHEMA` (translated from Anthropic tool schema)
   - `tool_choice={"type": "function", "function": {"name": "classify_intent"}}`
   - Temperature 0.0
   - Automatic prefix caching (no `cache_control` needed)
   - Timeout: 2.5s

2. Translate intent classification system prompt to OpenAI's `messages` format
3. Add Prometheus metrics with `source=gpt_5_mini` label

**Validation:**
- Test classification produces same Pydantic schema as Haiku
- Prefix caching observed in OpenAI dashboard after second call
- Latency p95 < 1s

**Common pitfalls:**
- OpenAI's tool format differs from Anthropic's — keep the system prompt vendor-agnostic
- Forgetting to set seed for reproducibility — set `seed=42` for consistency in eval

---

### 4.3 DeepSeek V3 self-hosted integration

**Goal:** DeepSeek V3 as the third ensemble member, mandatorily self-hosted per `D-026`.

**Dependencies:** 4.1, P.4 (DeepSeek deployment)

**Reference:** `AR §3.3`, `D-026`, `R-002`

**Tasks:**

1. Verify self-hosted DeepSeek V3 endpoint is reachable from application tier
2. Implement `DeepSeekClassifier(LLMClassifier)` in `src/bookcraft/components/intent/adapters/deepseek.py`:
   - Use OpenAI-compatible HTTP client pointed at internal endpoint
   - JSON mode response
   - Temperature 0.0
   - Timeout: 4.0s (DeepSeek is historically slowest)

3. Translate prompt to DeepSeek's expected format
4. Add Prometheus metrics with `source=deepseek_v3` label

**Validation:**
- Test classification produces same Pydantic schema
- No external network calls to non-internal endpoints (verify via egress monitoring)
- DeepSeek instance survives 100 sequential calls without restart

**Common pitfalls:**
- Accidentally pointing at hosted DeepSeek API — `R-002` is critical, monitor egress
- DeepSeek timeout too aggressive — 4s is the right balance; slower than 5s breaks quorum SLO

---

### 4.4 Race-with-quorum pattern

**Goal:** Three LLMs run in parallel; cancel slow tasks once quorum is reached.

**Dependencies:** 4.1, 4.2, 4.3

**Reference:** `AR §6.4.2`, `D-027`

**Tasks:**

1. Implement `RaceWithQuorum` orchestrator in `src/bookcraft/components/intent/race.py`:
   - Fire all 3 LLMs concurrently via `asyncio.create_task`
   - Use `asyncio.as_completed` to collect results as they arrive
   - Check for strong consensus after each result
   - Cancel remaining tasks on consensus
   - Hard timeout: max of per-vendor timeouts

2. Implement consensus check:
   - 2+ valid votes agree on `query.primary` AND `service.primary_service` AND `funnel.stage`

3. Add Prometheus metrics:
   - `intent_consensus_total{quorum_size}`
   - `intent_classifier_cancellation_total{source}`

**Validation:**
- Mock 2 fast classifiers agreeing + 1 slow disagreeing → slow one cancelled
- All 3 disagree → all complete, weighted voting decides
- All 3 agree quickly → 2nd-fastest result triggers cancellation

**Common pitfalls:**
- Not actually cancelling tasks (just ignoring) — wastes downstream LLM cost
- Race conditions on result accumulation — use proper async primitives, not shared lists

---

### 4.5 Tri-Match: rule storage

**Goal:** Postgres-backed rule store with hot reload.

**Dependencies:** 1.5 (DB)

**Reference:** `AR §6.4.1`, `AR Appendix B`

**Tasks:**

1. Implement `TriMatchRule` SQLModel per `AR Appendix B`
2. Generate Alembic migration

3. Implement `TriMatchRuleRepository`:
   - `get_active_rules() -> list[TriMatchRule]`
   - `propose_rule(rule_data, source, suggested_by_run_id)`
   - `approve_rule(rule_id, approver)`
   - `deprecate_rule(rule_id, reason)`
   - `update_calibration(rule_id, matched, correct, overruled)`

4. Implement seed rules:
   - 20-30 manual rules covering obvious cases (price keywords, sample requests, contact info inquiries, greetings)
   - Each with `source="manual"`, `approval_status="approved"`, manually authored

**Validation:**
- Rule CRUD via API works
- Seed rules loaded successfully
- Calibration counters update correctly

---

### 4.6 Tri-Match: state-of-the-art preprocessing

**Goal:** State-of-the-art preprocessing pipeline within Tri-Match (lemmatization, negation, evidence pool).

**Dependencies:** 1.9 (shared preprocessor), 4.5

**Reference:** `AR §6.4.1`, `D-024`

**Tasks:**

1. Implement Tri-Match preprocessing in `src/bookcraft/components/intent/trimatch/preprocessor.py`:
   - Reuses `ProcessedMessage` from Component 13
   - No additional NLP work; just consumes the shared artifact

2. Document the layered architecture in code comments:
   - Layer 1: Shared preprocessing (already done)
   - Layer 2: Lexical + pattern matchers
   - Layer 3: Semantic matcher
   - Layer 4: Evidence aggregation
   - Layer 5: Empirical calibration

**Validation:**
- ProcessedMessage from preprocessor flows directly into Tri-Match
- No redundant tokenization or negation detection

---

### 4.7 Tri-Match: lexical and pattern matchers

**Goal:** Token-level evidence on lemmatized tokens; spaCy Matcher for sequence patterns.

**Dependencies:** 4.5, 4.6

**Reference:** `AR §6.4.1`

**Tasks:**

1. Implement `LexicalMatcher` in `src/bookcraft/components/intent/trimatch/lexical.py`:
   - Build dict from approved keyword/exact rules: `{lemma: (intent_dim, target_value, confidence)}`
   - Check each lemma in `ProcessedMessage.tokens`
   - Skip tokens within negation spans
   - Returns list of matches with weights

2. Implement `PatternMatcher` using spaCy Matcher:
   - Build spaCy patterns from approved regex/pattern rules
   - Run on processed message
   - Skip matches inside negation spans

3. Implement evidence emission: each matcher emits weighted evidence per intent

4. Add Prometheus metrics:
   - `trimatch_lexical_matches_total{intent}`
   - `trimatch_pattern_matches_total{intent}`

**Validation:**
- Test message "I need pricing for editing" → lexical hits on "price", "edit"
- Negated message "I'm not asking about price" → lexical hit on "price" but suppressed by negation
- Pattern "how much {ADV}? {VERB}" matches "how much would it cost"

**Common pitfalls:**
- Forgetting negation suppression — counts as Tri-Match's biggest single quality bug
- Building spaCy patterns at every call — pre-compile at startup or rule reload

---

### 4.8 Tri-Match: semantic matcher

**Goal:** Sentence-level semantic similarity vs. canonical phrase library per intent.

**Dependencies:** 4.5, 4.6

**Reference:** `AR §6.4.1`

**Tasks:**

1. Implement `SemanticMatcher` in `src/bookcraft/components/intent/trimatch/semantic.py`:
   - Load all approved `semantic_phrase` rules at startup
   - Embed each phrase via TEI (batch)
   - Cache embeddings keyed on `(intent_dim, target_value)`

2. Per query:
   - Reuse `ProcessedMessage.embedding`
   - Compute cosine similarity vs. each phrase
   - Return best match with similarity ≥ 0.78
   - Apply 0.05 gap requirement (best - second-best) for confidence

3. Add Prometheus metrics:
   - `trimatch_semantic_match_seconds`
   - `trimatch_semantic_score` (histogram)

**Validation:**
- "What would I pay for cover design?" matches canonical phrase library for `service_price`
- Ambiguous message scores below threshold; punts to LLMs

**Common pitfalls:**
- Re-embedding the query — must reuse `ProcessedMessage.embedding`
- Loading rules synchronously on hot path — load at startup, reload on signal

---

### 4.9 Tri-Match: evidence aggregation

**Goal:** Combine lexical, pattern, and semantic evidence into a per-intent score with confidence.

**Dependencies:** 4.7, 4.8

**Reference:** `AR §6.4.1`

**Tasks:**

1. Implement `EvidenceAggregator` in `src/bookcraft/components/intent/trimatch/aggregator.py`:
   - Per intent: `score = w₁·lexical + w₂·pattern + w₃·semantic - w₄·negation_penalty`
   - Weights initialize from rule's `base_confidence`; updated by calibration
   - Output: sorted distribution of (intent, score) pairs

2. Implement `TriMatchEngine` orchestrator:
   - Runs lexical + pattern + semantic
   - Aggregates evidence
   - Applies TRG context modifiers (see 4.10)
   - Returns `TriMatchResult` per `AR Appendix A.6`

3. Add Prometheus metric: `trimatch_classification_seconds{layer}`

**Validation:**
- Multi-intent message scores both intents above threshold
- Single clear intent dominates (score gap ≥ 0.2)
- Empty/ambiguous message returns layer=NONE

**Common pitfalls:**
- Hardcoded weights — use rule.base_confidence and let calibration drive
- Not handling tied top-2 — return needs_clarification signal

---

### 4.10 Tri-Match: TRG context integration

**Goal:** Conditional rules fire based on TRG context.

**Dependencies:** 4.9, 3.7 (TRG relations)

**Reference:** `AR §6.4.1`, TRG integration discussion

**Tasks:**

1. Extend rule schema with optional `context_conditions` field:
   - `previous_bot_relation`: required relation type for rule to fire
   - `min_repetition_count`: rule fires only if repetition ≥ N
   - `outstanding_question_about`: rule context only if pending question matches topic

2. Implement context-aware filtering in matchers:
   - Before considering a rule, check `context_conditions` against `TRGContext`
   - Skip rule if conditions don't match

3. Implement repetition-damped confidence:
   - When repetition count exceeds threshold for matched intent, dampen Tri-Match's confidence (not raw score, but final returned confidence)
   - Forces Decision Layer to consider ensemble more heavily

**Validation:**
- "Yes" after a price-quote bot turn → rule fires with `accept_quote` interpretation
- Same "Yes" after a scope-question bot turn → different rule fires with `confirm_scope`
- Repeated "Yes" with rising count → confidence dampened

**Common pitfalls:**
- Context conditions in code instead of data — must be on rule rows so auto-correction loop can suggest them later

---

### 4.11 Decision Layer (Component 11)

**Goal:** Aggregate Tri-Match + 3 LLM votes into a final classification.

**Dependencies:** 4.4, 4.10

**Reference:** `AR §6.11`

**Tasks:**

1. Implement `IntentDecisionLayer` in `src/bookcraft/components/intent/decision_layer.py`:
   - Source weights initial values per `AR §6.11`
   - Per-dimension weighted voting (query, service, funnel separately)
   - Consensus boost (+0.05 confidence on agreement)
   - Stage transition validation (pin invalid transitions)
   - `needs_clarification` gate

2. Implement weight calibration job (runs monthly):
   - Read last 30 days of `intent_classifications`
   - Compute per-source accuracy vs. final decision
   - Update weights table

3. Implement `EnsembleDecision` schema with full audit trail:
   - All votes
   - Tri-Match result
   - Decision method
   - Decision confidence

4. Persist every decision to `intent_classifications` table with full vote capture

5. Add Prometheus metrics:
   - `intent_decision_method_total{method}`
   - `intent_consensus_size_total{size}`
   - `intent_decision_seconds`

**Validation:**
- 4 sources agree → consensus boost applied; final confidence high
- 2 disagree, 2 agree → weighted majority chosen
- All disagree → highest-confidence choice; flagged for review
- Invalid stage transition → pinned to current stage

**Common pitfalls:**
- Hardcoded weights at runtime — read from config; reload on signal
- Treating Tri-Match's vote as primary even in shadow mode — initial weight 0.4 is correct; let calibration update over time

---

### 4.12 Tri-Match shadow mode

**Goal:** Tri-Match runs and votes; never shortcuts at this phase.

**Dependencies:** 4.11

**Reference:** `D-019`, `D-020`

**Tasks:**

1. Implement environment configuration:
   - `TRIMATCH_MODE=shadow` (default)
   - `TRIMATCH_SHORTCUT_LAYERS=` (empty)

2. Wire Tri-Match into orchestrator:
   - Phase 2: run Tri-Match
   - If `TRIMATCH_MODE=shortcut_enabled` AND confidence ≥ 0.95 AND layer ∈ shortcut layers → use Tri-Match alone (NOT triggered in this phase)
   - Else: continue to Phase 3 with all 4 sources

3. Verify configuration enforcement:
   - Application startup logs the mode
   - Mismatch between intended mode and actual behavior fails loudly

**Validation:**
- With `TRIMATCH_MODE=shadow`, every turn invokes the LLM ensemble
- Tri-Match output captured in `intent_classifications.trimatch_result`
- No turn in production logs shows "shortcut taken"

**Common pitfalls:**
- Allowing shortcut in this phase "for testing" — leads to production shortcuts before calibration data exists
- Forgetting to verify cost accounting — shadow mode runs full ensemble; cost is at peak

---

### 4.13 Tri-Match calibration counters

**Goal:** Per-rule calibration data accumulating from real classifications.

**Dependencies:** 4.5, 4.11

**Tasks:**

1. Implement counter update on every classification:
   - For each rule that matched in Tri-Match: increment `times_matched`
   - If Decision Layer's final agrees with Tri-Match's vote on the same intent: increment `times_correct`
   - If Decision Layer disagreed with Tri-Match: increment `times_overruled`

2. Implement empirical precision view:
   - SQL view: `times_correct / NULLIF(times_matched, 0)`
   - Available via admin API endpoint

3. Implement auto-deprecation:
   - Background job runs daily
   - Rules with `times_matched ≥ 100 AND empirical_precision < 0.85` → deprecated automatically

4. Add Prometheus metrics:
   - `trimatch_overruled_total{rule_id}`
   - `trimatch_rule_precision{rule_id}` (gauge, periodically computed)

**Validation:**
- After 200 classifications, calibration counters non-zero
- Force a bad rule to fire 100 times with 80% overrule → auto-deprecation triggers

**Common pitfalls:**
- Updating counters synchronously in the request path — must be async (post-response)
- Storing per-rule counters as session-scoped — must be persistent in `trimatch_rules` table

---

### 4.14 Phase 4 acceptance tests

**Goal:** Verification that ensemble + Tri-Match work end-to-end.

**Tasks:**

1. Test suite in `tests/integration/phase4/`:
   - Full ensemble vote on labeled corpus → eval accuracy improves by ≥ 5pp from Phase 2
   - Race-with-quorum cancellation observed in trace
   - Tri-Match shadow vote captured per turn
   - Calibration counters updating
   - Auto-deprecation of intentionally bad rule
   - Stage transition validator catches invalid transitions

2. Cost analysis:
   - Verify per-turn cost ~$0.028 (peak, due to shadow mode)
   - Verify monthly extrapolation ~$2,000

**Validation:**
- All tests pass
- Cost matches projections in `AR §10.1` launch numbers

---

### Phase 4 Exit Criteria

- [ ] All Phase 4 acceptance tests pass
- [ ] Three LLM vendors integrated and voting
- [ ] Decision Layer aggregating 4 sources
- [ ] Tri-Match running in shadow mode
- [ ] Eval accuracy ≥ 90% on primary intent (consensus boost helps)
- [ ] Cost per turn ~$0.028 (matching launch projections)
- [ ] Calibration counters accumulating data
- [ ] DeepSeek self-hosted endpoint stable for 14+ days
- [ ] No PII flowing to external LLM providers (verified)

---

## Phase 5: Self-Improvement

**Goal:** Tri-Match grows its rule corpus through automated mining of LLM disagreements.

**Components addressed:** Component 12 (full)

**Critical constraint:** This phase phases in safety. Day 0 is manual review only. Day 30 is Sonnet suggestions with manual approval. Day 60 is auto-approval with strict gates.

---

### 5.1 Disagreement mining job

**Goal:** Daily batch job identifies turns where Tri-Match diverged from LLM consensus.

**Dependencies:** Phase 4 complete with 14+ days of operational data

**Reference:** `AR §6.12`

**Tasks:**

1. Implement `DisagreementMiner` in `src/bookcraft/workers/trimatch/miner.py`:
   - Query `intent_classifications` for last 24 hours
   - Filter: `trimatch_diverged = true` OR `trimatch_result IS NULL` (Tri-Match missed)
   - Group by `(intent_dimension, llm_consensus_value, trg_context_pattern)`
   - Filter groups with ≥ 5 high-confidence ensemble agreements

2. Generate disagreement summary:
   - Per group: list of example messages, what Tri-Match said vs. LLM consensus
   - Output to `disagreement_reports` table or S3 file

3. Schedule via Arq or Temporal:
   - Daily at 03:00 UTC

4. Initial mode: report-only (no Sonnet suggestions yet)

**Validation:**
- After Day 14, run miner manually; verify report contains real disagreements
- Reports retain history for 30 days for QA review

**Common pitfalls:**
- Filtering too aggressively — discards real signal; start permissively, tighten later
- Not capturing TRG context — context-aware disagreements need context-aware rules

---

### 5.2 Sonnet batch submission (Day 30+)

**Goal:** Automated rule suggestion via Sonnet Batch API.

**Dependencies:** 5.1

**Reference:** `AR §6.12`

**Tasks:**

1. Implement `RuleSuggestionPromptBuilder`:
   - For each disagreement group, format examples for Sonnet
   - Include TRG context per example
   - Use rule suggestion system prompt (Appendix F.5 of AR)

2. Implement `SUGGEST_RULES_TOOL` schema (per the conversation thread on auto-correction loop)

3. Implement `BatchSubmissionJob`:
   - Builds batch_requests list (one per disagreement group)
   - Submits via Anthropic Batch API
   - Stores `batch_id` for later collection

4. Schedule:
   - 03:15 UTC daily

5. Add Prometheus metrics:
   - `trimatch_suggestion_batches_submitted_total`
   - `trimatch_suggestion_groups_per_batch` (histogram)

**Validation:**
- Submit batch with 3 disagreement groups; verify batch_id returned
- Cost per batch within expected ($15-30/month total)

**Common pitfalls:**
- Synchronous submission of large batches — use async client
- Forgetting to track batch_id — can't collect results otherwise

---

### 5.3 Batch result collection

**Goal:** Parse Sonnet batch results into pending rules.

**Dependencies:** 5.2

**Tasks:**

1. Implement `BatchCollectorJob`:
   - Run at 09:00 UTC daily
   - Fetch results from Anthropic Batch API for previous day's batches
   - Parse tool_use outputs into proposed rules
   - Insert into `trimatch_rules` with `approval_status="pending"`, `source="llm_suggested"`, `suggested_by_run_id` populated

2. Validate suggested rules via Pydantic on insertion

3. Add Prometheus metric: `trimatch_rules_suggested_total{rule_type}`

**Validation:**
- After batch submission, results land in `trimatch_rules` next morning
- Each pending rule has rationale, expected_precision, false_positive_risks

**Common pitfalls:**
- Inserting invalid rules silently — Pydantic validation must reject and log
- Marking rules as approved automatically at this phase — must be `pending` until Day 60

---

### 5.4 Manual approval queue

**Goal:** CSR-reviewable list of pending rules.

**Dependencies:** 5.3

**Tasks:**

1. Implement admin API endpoints:
   - `GET /admin/trimatch/pending` — list pending rules
   - `POST /admin/trimatch/{rule_id}/approve` — approve rule
   - `POST /admin/trimatch/{rule_id}/reject` — reject with reason

2. Implement approval workflow:
   - On approve: status → `approved`, set `enabled=true`, trigger hot reload
   - On reject: status → `rejected`, archived

3. CSR admin UI is out of scope (`D-029`); CSRs use API or simple admin tools

**Validation:**
- Approval triggers Tri-Match reload within 5 minutes
- Approved rule fires on next matching message
- Rejected rules excluded from active rule set

---

### 5.5 Hot-reload mechanism

**Goal:** New approved rules take effect without service restart.

**Dependencies:** 5.4

**Tasks:**

1. Implement `TriMatchEngine.reload(session)`:
   - Re-fetch all approved+enabled rules from DB
   - Rebuild indexes (exact, keyword, regex, fuzzy corpus, semantic embeddings)
   - Atomic state swap (build new state, replace pointer)

2. Trigger hot reload:
   - On approval (immediate)
   - Daily at 09:15 UTC (after auto-correction)
   - On admin signal

3. Add Prometheus metrics:
   - `trimatch_reload_total`
   - `trimatch_reload_seconds`
   - `trimatch_active_rules` (gauge, by type)

**Validation:**
- Reload completes in < 30 seconds
- Atomic swap: in-flight requests use either old state OR new state, never partial

**Common pitfalls:**
- Mutating live state during reload — leads to inconsistent results mid-flight; build new state first
- Forgetting to embed new semantic phrases — caching layer needed

---

### 5.6 Auto-approval gates (Day 60+)

**Goal:** High-precision suggestions auto-approve without human review.

**Dependencies:** 5.4, 5.5, 60+ days operational data

**Reference:** `D-028`

**Tasks:**

1. Implement auto-approval check in collection job:
   - `expected_precision >= 0.95`
   - AND `covers ≥ 10 examples`
   - AND no false-positive risks flagged
   - AND target intent has ≥ 95% empirical precision in existing rules

2. If all conditions met: `approval_status = "approved"`, `enabled=true`, but enter shadow-on-shadow mode for first 100 matches:
   - Add `is_shadow: bool` to rule
   - Shadow rules don't influence Decision Layer
   - After 100 matches, evaluate empirical precision
   - If ≥ 0.85, promote to active

3. Configuration:
   - `TRIMATCH_AUTOAPPROVE_ENABLED=false` (default)
   - Flip to `true` only after Day 60

4. Add Prometheus metrics:
   - `trimatch_auto_approved_total`
   - `trimatch_shadow_promoted_total`
   - `trimatch_shadow_demoted_total`

**Validation:**
- Suggested rule meeting all criteria auto-approves
- Auto-approved rule enters shadow mode
- After 100 matches, calibration drives promotion or demotion

**Common pitfalls:**
- Skipping shadow-on-shadow phase — bypasses one safety layer
- Forgetting to demote bad shadow rules — they accumulate and confuse calibration

---

### 5.7 Phase 5 acceptance tests

**Tasks:**

1. End-to-end test:
   - Inject 10 disagreement turns
   - Run mining job
   - Submit batch
   - Collect results
   - Verify pending rules created
   - Approve via admin API
   - Verify rule fires on next matching message

2. Validate Day 60 promotion:
   - Force time forward in test environment
   - Auto-approval condition met
   - Rule enters shadow mode
   - 100 matches simulated → promotion

**Validation:**
- All tests pass
- Manual approval workflow tested by actual CSR

---

### Phase 5 Exit Criteria

- [ ] All Phase 5 acceptance tests pass
- [ ] Day 30 manual approval validated
- [ ] Day 60 auto-approval gates working
- [ ] Tri-Match rule corpus growing (≥ 50 new rules/month from suggestions)
- [ ] Empirical precision tracked per rule
- [ ] Auto-deprecation of bad rules verified
- [ ] No production incidents from suggested rules in 30+ days
- [ ] Shortcut layer promotion candidates identified for Phase transition planning

---

## Phase 6: High-Stakes Documents

**Goal:** Autonomous NDA and service agreement generation with bounded blast radius.

**Components addressed:** Component 8

**Critical constraint:** The architectural rule from `AR §6.8` is absolute: **the LLM never produces a single character of legal text**. Phased rollout per `D-051` is non-negotiable.

---

### 6.1 Document templates and lawyer review

**Goal:** NDA and service agreement Jinja2 templates, lawyer-reviewed and frozen.

**Dependencies:** Legal counsel engagement

**Reference:** `AR §6.8`, `D-050`

**Tasks:**

1. Engage legal counsel for template authoring
2. Templates authored in `src/bookcraft/templates/legal/`:
   - `nda_v1.0.j2`
   - `service_agreement_v1.0.j2`

3. Template requirements:
   - All variable substitutions explicitly marked: `{{ customer_name }}`
   - No conditional legal prose based on free text
   - Variant blocks acceptable if each is itself lawyer-reviewed
   - Footer: `[Auto-generated draft — Template version: {{ template_version }}] [Generated: {{ generation_timestamp }}]`

4. Implement `TemplateRegistry`:
   - Versioned templates
   - `PRODUCTION_VERSIONS: dict[str, str]` controls active version per type

5. Implement template loading with Jinja2 `StrictUndefined`:
   - Any missing variable raises immediately at render time

6. Establish template change process:
   - Template changes require lawyer sign-off
   - Sign-off recorded in `docs/template-changelog.md`
   - Version bump for any change

**Validation:**
- Lawyer sign-off documented for v1.0 templates
- StrictUndefined enforcement: missing variable raises Jinja2 exception
- Template change process tested with a deliberate trivial change

**Common pitfalls:**
- Adding "small fixes" to templates without lawyer review — even punctuation changes risk legal interpretation
- Letting LLM "improve" template language — D-050 forbids absolutely

---

### 6.2 Document parameter schemas

**Goal:** Typed parameter objects with FieldMeta provenance.

**Dependencies:** 1.1, 1.7

**Reference:** `AR §6.8`

**Tasks:**

1. Implement `NDAParameters` Pydantic model:
   - All required fields: `customer_name`, `customer_email`, `project_summary`, `effective_date`
   - FieldMeta companion fields for each
   - Pydantic validation enforces non-null required fields

2. Implement `ServiceAgreementParameters`:
   - Customer fields, project fields, services list, total_amount, currency, payment_schedule, timeline_dates, quote_id
   - All with FieldMeta

3. Implement parameter builders:
   - `build_nda_parameters(state, thread) -> NDAParameters`
   - `build_agreement_parameters(state, thread) -> ServiceAgreementParameters`
   - Pure functions that read from `ThreadState`; no LLM involvement

**Validation:**
- Parameter builder fails loudly when required state missing
- FieldMeta provenance preserved through build

---

### 6.3 Confidence gate

**Goal:** Refuses to render documents from low-confidence inputs.

**Dependencies:** 6.2

**Reference:** `AR §6.8`, `D-053`

**Tasks:**

1. Implement `ConfidenceGate`:
   - `ALLOWED_SOURCES = {USER_STATED, USER_CONFIRMED, CSR_ENTERED}`
   - `CONFIDENCE_FLOOR_NDA = 0.90`
   - `CONFIDENCE_FLOOR_AGREEMENT = 0.95`
   - Returns `(passed, reasons[])`

2. Implement clarification flow:
   - On gate fail, generate user confirmation request
   - User confirmation flips affected FieldMeta source from `ai_extracted` to `user_confirmed`
   - On retry, gate passes

**Validation:**
- AI-extracted email at confidence 0.85 → gate fails for NDA
- User-confirmed email → gate passes
- Mock all-perfect-source state → gate passes

**Common pitfalls:**
- Bypassing gate "for testing" — once code path exists, it gets used in production
- Allowing system-source values (e.g., calculated dates) without restriction — must be in `ALLOWED_SOURCES`

---

### 6.4 Idempotency

**Goal:** Identical parameters return cached document; no duplicate sends.

**Dependencies:** 6.3

**Tasks:**

1. Implement idempotency key computation:
   - SHA-256 of canonical JSON of:
     - Document type
     - Customer email
     - Thread ID
     - Template version
     - All meaningful parameter values

2. Cache: 24-hour Redis cache (per `D-062`)

3. Wire into dispatcher's standard idempotency mechanism

**Validation:**
- Identical inputs → same `idempotency_key`
- Different parameters → different keys
- Repeated calls within 24h return cached result

---

### 6.5 Render pipeline

**Goal:** Jinja2 render with strict undefined enforcement.

**Dependencies:** 6.1, 6.2

**Tasks:**

1. Implement `DocumentRenderer`:
   - Loads active template
   - Renders with parameters
   - Returns rendered Markdown text

2. Add Prometheus metric: `document_render_seconds`

**Validation:**
- Test render with valid parameters → produces expected output
- Test render with missing variable → raises StrictUndefined error

---

### 6.6 Verifier

**Goal:** Second-LLM cross-check of rendered document vs. thread state.

**Dependencies:** 6.5

**Reference:** `AR §6.8`, `D-052`, `AR Appendix F.4`

**Tasks:**

1. Implement `VERIFIER_TOOL` schema with `matches_thread_state`, `anomalies[]`, `approve_for_delivery`, `rationale` fields

2. Author verifier prompt per `AR Appendix F.4`

3. Implement `DocumentVerifier`:
   - For NDA: uses Haiku 4.5
   - For service agreement: uses Sonnet 4.6 (per `D-052`)
   - Sends thread state summary, parameters JSON, rendered text
   - Tool call returns approve/reject

4. Implement verifier outcome handling:
   - Approve → continue to PDF generation
   - Reject → flag for human review; do NOT send

5. Add Prometheus metrics:
   - `document_verifier_invoked_total{type}`
   - `document_verifier_rejected_total{type, reason}`

**Validation:**
- Test rendered NDA with name mismatch vs. state → verifier rejects
- Test rendered agreement with all-matching → verifier approves
- Verifier output validated by Pydantic

---

### 6.7 PDF generation and S3 upload

**Goal:** Render Markdown → PDF; upload to S3; generate signed URL.

**Dependencies:** 6.6

**Tasks:**

1. Implement `PDFGenerator`:
   - Uses WeasyPrint with custom CSS (`docs/pdf_styles.css`)
   - Computes content hash (SHA-256 of PDF bytes)

2. Implement S3 upload:
   - Key: `documents/{thread_id}/{type}/{content_hash}.pdf`
   - Server-side encryption (SSE-KMS)
   - Metadata: template_version, customer_email, thread_id, generated_at

3. Implement signed URL generation:
   - 24-hour TTL per `AR §7.5`

4. Add Prometheus metrics:
   - `document_pdf_render_seconds`
   - `document_s3_upload_seconds`

**Validation:**
- Test render produces valid PDF (open in viewer)
- Content hash deterministic for same inputs
- Signed URL works within 24h, fails after

---

### 6.8 Email delivery

**Goal:** Send document email with signed URL and STOP signal instructions.

**Dependencies:** 6.7

**Reference:** `D-054` (retraction window)

**Tasks:**

1. Implement email template with:
   - Document attached or linked via signed URL
   - 24-hour STOP signal language
   - Clear retraction instructions

2. Implement `DocumentEmailer`:
   - Uses configured email provider (SendGrid or SES)
   - Tracks delivery status

3. Track delivery in audit log

4. Add Prometheus metrics:
   - `document_email_sent_total{type}`
   - `document_email_delivery_seconds`

**Validation:**
- Test email rendering and delivery to test account
- STOP signal text clearly visible

---

### 6.9 Document state recording

**Goal:** Persist document delivery to thread state and event log.

**Dependencies:** 6.8

**Tasks:**

1. Implement `record_document_delivery`:
   - Append `DocumentRecord` to `ThreadState.commercial.documents`
   - Append `document_delivered` event with full forensic payload (see `AR §6.8`)
   - Hash-chained automatically via 1.6

2. Verify event payload includes:
   - Document ID
   - Type
   - Template version
   - Content hash
   - S3 key
   - All parameters
   - Parameter provenance (FieldMeta values)
   - Verifier result
   - Delivered email
   - Delivered timestamp

**Validation:**
- After successful delivery, query `thread_events` shows `document_delivered` row
- Hash chain still verifiable
- Forensic reconstruction possible from event payload alone

---

### 6.10 Retraction window

**Goal:** STOP signal handling within 24 hours of delivery.

**Dependencies:** 6.9

**Reference:** `D-054`

**Tasks:**

1. Implement STOP detection:
   - User message containing "STOP" (case-insensitive) within 24h of `document_delivered`
   - Triggers `void_document.v1` tool

2. Implement `void_document.v1` tool:
   - Marks document voided in state
   - Appends `document_voided` event
   - Sends void confirmation email
   - Creates CSR followup task

3. After 24-hour window:
   - STOP messages don't void
   - User informed: "the retraction window has passed; a team member will follow up"

**Validation:**
- STOP within window voids document
- STOP after window doesn't void; CSR notified
- Confirmation email sent on void

---

### 6.11 Anomaly detection

**Goal:** Auto-suspend autonomous mode when anomalies detected.

**Dependencies:** 6.9

**Reference:** `AR §6.8`

**Tasks:**

1. Implement Prometheus alerts per `AR Appendix E.2`:
   - DocumentGenerationVolumeAnomaly (3σ outside 7-day baseline)
   - VerifierRejectionRateHigh (> 10% over 1 hour)
   - SingleCustomerDocumentBurst (> 3 in 15 min)
   - AgreementWithLowConfidenceInputs (> 0)

2. Implement auto-suspend mechanism:
   - On any P0 alert in document category, set env: `NDA_MODE=manual`, `AGREEMENT_MODE=manual`
   - Manual ack required to resume

3. Notification channels:
   - On-call page
   - Slack alert
   - Optional: pause flag in admin UI

**Validation:**
- Inject burst of documents from one customer → SingleCustomerDocumentBurst alert fires
- Suspend triggered; subsequent calls deferred to manual

---

### 6.12 Phased rollout

**Goal:** NDA and agreement modes evolve through 4 phases.

**Reference:** `D-051`

**Tasks:**

1. **Phase 6.A (Manual mode):**
   - `NDA_MODE=manual`, `AGREEMENT_MODE=manual`
   - All requests defer to CSR queue
   - Validate parameter building, rendering, verifier (without auto-send)
   - Run for 60+ days minimum

2. **Phase 6.B (Verifier-gated):**
   - `NDA_MODE=verifier_gated`, `AGREEMENT_MODE=manual`
   - Verifier-approved documents go to CSR with "ready to send" flag
   - Verifier-rejected documents go to investigation queue
   - Run for 60+ days

3. **Phase 6.C (NDA autonomous):**
   - `NDA_MODE=autonomous`, `AGREEMENT_MODE=verifier_gated`
   - NDA delivers automatically after verifier approval
   - Track metrics; zero incidents required

4. **Phase 6.D (Agreement autonomous):**
   - `AGREEMENT_MODE=autonomous`
   - Only after 6+ months NDA-clean operation

**Validation per phase:**
- Each transition gated by 60+ days clean operational data
- No exceptions

**Common pitfalls:**
- Pressure to skip phases for "speed" — D-051 exists for legal risk reasons
- Forgetting to monitor during transitions — alert thresholds must be tighter during transition windows

---

### Phase 6 Exit Criteria

- [ ] NDA and service agreement templates lawyer-approved
- [ ] All 10 stages of document pipeline tested end-to-end
- [ ] Verifier rejecting bad inputs in test scenarios
- [ ] Anomaly detection auto-suspending in test scenarios
- [ ] Hash-chained audit log verifiable for delivered documents
- [ ] Retraction window working (within and after)
- [ ] Phased rollout plan documented and tracked
- [ ] CSR admin UI deployed (separate frontend track per `D-029`)
- [ ] All P0/P1 alerts wired to PagerDuty
- [ ] DR drill completed: simulate misgenerated document, verify forensic reconstruction

---

## Cross-Phase Activities

These activities run continuously across all phases.

### CP.1 Testing strategy rollout

| Phase | Test additions |
|---|---|
| Phase 1 | Unit tests for domain types, integration tests for storage, property tests for state machine |
| Phase 2 | Eval harness (intent + extraction), end-to-end conversation tests, mocked LLM tests |
| Phase 3 | WebSocket integration tests, load tests, chaos tests for vendor outages |
| Phase 4 | Multi-vendor contract tests, race-with-quorum correctness tests |
| Phase 5 | Auto-correction loop end-to-end, calibration counter tests |
| Phase 6 | Document pipeline regression tests with golden samples, lawyer-defined acceptance tests |

### CP.2 Observability maturity

| Phase | Dashboards/alerts |
|---|---|
| P (prereq) | Cost dashboard skeleton, latency dashboard skeleton, log aggregation |
| Phase 1 | Storage metrics, dispatcher metrics, language guard metrics |
| Phase 2 | LLM cost tracking, response generation metrics, eval harness scores |
| Phase 3 | TRG dashboard, conversation funnel dashboard, WebSocket metrics |
| Phase 4 | Multi-vendor LLM dashboard, ensemble consensus dashboard, Tri-Match metrics |
| Phase 5 | Auto-correction loop dashboard, calibration trends |
| Phase 6 | Document dashboard, anomaly detection alerts, audit chain integrity check |

### CP.3 Documentation practices

- Every component has a README in its module
- Every ADR is recorded in `docs/adrs/` with template:
  - Status (proposed/accepted/superseded)
  - Context
  - Decision
  - Consequences
  - References to AR sections
- Runbooks in `ops/runbooks/`:
  - One per failure mode (FM-001 through FM-020)
  - Detection signal, immediate actions, escalation, postmortem template

### CP.4 Security hardening

- **Phase 1:** Vault setup, no secrets in code, encryption at rest verified
- **Phase 2:** PII scrubbing in logs, zero-retention on Anthropic configured
- **Phase 3:** WebSocket origin validation, rate limiting, CSRF tokens
- **Phase 4:** OpenAI zero-retention configured, DeepSeek egress monitoring
- **Phase 5:** Rule injection prevention (validate suggested rules)
- **Phase 6:** Document signing keys in HSM, audit chain integrity nightly job

### CP.5 Performance optimization

Continuous activity. Monthly review:

- Cache hit rates per component
- Output token drift (Sonnet, Haiku)
- RAG context size
- Worst conversations by cost (top 50 reviewed)
- Tri-Match shortcut hit rate growth (Phase 5+)

### CP.6 Disaster recovery drills

Quarterly:

- Postgres failover test
- S3 cross-region failover
- Single-vendor LLM outage simulation
- Document misgeneration tabletop exercise

### CP.7 Capacity planning reviews

Monthly:

- Trend conversation volume
- Trend per-component resource utilization
- Anticipate scaling triggers
- Request rate-limit increases ahead of need

---

## Appendices

### Appendix A — Common pitfalls and solutions

| Pitfall | Symptom | Solution |
|---|---|---|
| Mutating state without `update_thread_state` | Event log gaps; hash chain breaks | Code review; lint rule against direct state mutation |
| Forgetting `cache_control` on system prompts | Cache miss every call; cost spike | Helper function for Anthropic call construction |
| Not awaiting async tasks in post-response | Lost extractions, missing events | Use `asyncio.create_task` and track them properly |
| Hardcoded prices in code | Tool returns same value regardless of input | Pricing must come from engine; pre-validation guards |
| Allowing LLM to write legal prose | Liability exposure | D-050 enforced; templates only |
| Re-embedding query in RAG | Latency 2× expected | Always reuse `ProcessedMessage.embedding` |
| Sticky session forgotten | Bubbles arrive on different workers | Verify load balancer config |
| Optimistic lock retry without re-read | Same conflict every retry | Always fetch fresh state in retry loop |
| Tri-Match shortcut enabled before calibration | Confidently wrong shortcuts | `TRIMATCH_MODE=shadow` enforced via env |
| Document gate bypass for testing | Real customer gets bad document | No bypass in production; test with mocked state |

### Appendix B — Validation queries

```sql
-- Verify hash chain integrity for a thread
WITH chain AS (
  SELECT 
    id, sequence, content_hash, prev_hash,
    LAG(content_hash) OVER (ORDER BY sequence) AS expected_prev
  FROM thread_events
  WHERE thread_id = '...'
)
SELECT * FROM chain WHERE prev_hash IS DISTINCT FROM expected_prev;
-- Expected: 0 rows (chain intact)

-- Detect orphaned state updates (no matching event)
SELECT t.id 
FROM threads t
LEFT JOIN thread_events e ON e.thread_id = t.id
WHERE t.updated_at > now() - interval '1 day'
GROUP BY t.id, t.updated_at, t.version
HAVING t.version > COUNT(e.id);
-- Expected: 0 rows

-- Tri-Match rule precision audit
SELECT 
  rule_type, target_dimension, target_value,
  times_matched, times_correct,
  CASE WHEN times_matched > 0 
    THEN times_correct::float / times_matched 
    ELSE NULL 
  END AS empirical_precision
FROM trimatch_rules
WHERE enabled = true AND times_matched >= 100
ORDER BY empirical_precision NULLS LAST;
-- Look for rules with precision < 0.85 (auto-deprecation candidates)

-- Document audit reconstruction
SELECT 
  payload->>'document_id' AS doc_id,
  payload->>'template_version' AS version,
  payload->>'content_hash' AS hash,
  payload->'parameter_provenance' AS provenance,
  payload->'verifier_result' AS verifier,
  created_at
FROM thread_events
WHERE event_type = 'document_delivered'
  AND thread_id = '...'
ORDER BY created_at DESC;
```

### Appendix C — Health check endpoints

Implement at `/health` and `/health/deep`:

- `/health` — fast (< 100ms), checks process is alive
- `/health/deep` — comprehensive:
  - Postgres reachable
  - Redis reachable
  - ES reachable
  - TEI reachable
  - Anthropic API key valid (cached, refreshed every 5 min)
  - OpenAI API key valid (cached)
  - DeepSeek endpoint reachable

### Appendix D — Smoke test procedures

After every deployment:

1. **Liveness:** `curl /health` returns 200
2. **Deep health:** `curl /health/deep` returns 200 with all green
3. **Smoke conversation:** Send "hi" → receive greeting response
4. **Smoke pricing:** Send pre-canned pricing inquiry → verify tool invocation in trace
5. **Smoke document (Phase 6):** Trigger NDA generation in test mode → verify successful pipeline (no actual send)

### Appendix E — Rollback procedures

| Component | Rollback procedure |
|---|---|
| Application | Blue/green: switch traffic to previous version; promote to active in seconds |
| Database migration | Maintain forward-only migrations; rollbacks via expand-contract reversal |
| Tri-Match rule | Set `enabled=false` on rule via admin API; reload triggers; effect immediate |
| Sonnet prompt change | Feature flag rollback; restart not required |
| Document template | Revert `PRODUCTION_VERSIONS` env var to previous; legal sign-off required |
| LLM mode flip | Set `TRIMATCH_MODE=shadow` via env; restart workers |

### Appendix F — Phase transition gates reference

Quick reference of all phase gates:

| Gate | Hard prerequisites |
|---|---|
| P → 1 | Local dev ✓; infra ✓; CI green ✓; observability ✓ |
| 1 → 2 | All Phase 1 acceptance ✓; coverage 80% ✓; metrics live ✓ |
| 2 → 3 | E2E conversation ✓; eval baseline ✓; cost in budget ✓ |
| 3 → 4 | All SLOs met under load ✓; soft-launch demo ✓; TRG operational ✓ |
| 4 → 5 | Ensemble live 14+ days ✓; calibration data accumulating ✓ |
| 5 → 6 | Auto-correction running ✓; manual approval validated ✓ |
| 6.A → 6.B | 60+ days manual mode clean ✓; verifier validated ✓ |
| 6.B → 6.C | 60+ days verifier-gated clean ✓; CSR confidence high ✓ |
| 6.C → 6.D | 6+ months NDA autonomous, zero incidents ✓ |

---

*End of implementation guide. Source code structure, ADRs, runbooks, and dashboards live in the BookCraft AI source repository. This guide is the canonical implementation playbook; deviations from the recommended sequence must be recorded as ADRs cross-referencing the architectural decisions they affect.*
