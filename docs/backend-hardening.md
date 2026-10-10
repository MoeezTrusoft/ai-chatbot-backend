# Backend hardening and rollout

This change adds authenticated conversation ownership, serialized mutations,
durable action results and CSR delivery, bounded turn execution, and independent
CI checks. The container workflow builds, smoke-tests and exports an image artifact.
Deployment and registry publication remain the responsibility of the deployment system.

## Compatibility and migration

1. Back up PostgreSQL and run **uv run --frozen alembic upgrade head** before starting
   the updated workers. Revision 20261010_0001 adds thread ownership, saved action
   results, and the CSR delivery outbox.
2. Issue HS256 JWTs with a stable, nonempty sub and finite numeric exp. The optional
   customer_id must be the authenticated customer's UUID. HTTP and both WebSocket
   routes enforce the same identity. Tokens without expiration are rejected.
3. Give the trusted CSR bridge scope "chat:manage" for fact injection, CSR turns,
   handover, consultation callbacks and debug state. Calendar administration needs
   calendar:manage or chat:manage. Never issue these scopes to browser visitors.
4. Production requires JWT authentication and a signing key of at least 32 characters.
   Set JWT_ISSUER and JWT_AUDIENCE when your issuer supports those claims, and
   enable READINESS_CHECK_EXTERNALS=true in deployed environments.
5. Existing threads with a customer ID can be accessed by a token for that customer.
   Threads with neither an owner nor a customer must be assigned an owner by a trusted
   migration using verified CRM mappings, or replaced with a new conversation.
   There is no public endpoint that adopts unowned historical threads.

Pause workers before downgrading. The command "alembic downgrade -1" removes the new
ownership, action result and outbox columns/tables and therefore loses their data.
Restore a backup if those records are needed.

## Persistence and retries

Every conversation mutation uses the same execution boundary. PostgreSQL advisory
locks serialize chat and CSR updates across workers. Lock connections use a separate
unpooled engine, capped at four active lock holders/waiters per repository instance,
so lock holders cannot exhaust the application's query pool. SQLite uses local locks
for tests only. State writes additionally compare and increment the version atomically;
a stale write returns HTTP 409 rather than retrying stale state.

Buffered events are flushed before immediate events and committed alongside state
where applicable. Early successful returns flush outstanding events. Failed flushes
propagate; they are no longer reported as successful turns with missing audit records.
Child extraction and graph persistence tasks finish or are cancelled before releasing
the conversation lock. The overall timeout includes lock waiting and child cleanup.

Sales actions persist completed results and replay them after a worker restart.
A known failed action can be claimed again atomically. An interrupted processing
claim is not automatically retried: the remote side effect may already have succeeded.
After checking the downstream system, an operator can call
ActionIdempotencyRepository.release_abandoned with confirmed_no_side_effect=True
for an expired claim. Never release one solely because its TTL elapsed.

When CSR_NODE_API_URL is configured, creating/rescheduling a consultation writes an
outbox row in the same transaction. The worker uses bounded HTTP calls, optional
CSR_NODE_API_TOKEN, exponential retries, and creation order per consultation;
a failed consultation does not block unrelated bookings.

Delivery is **at least once**. The receiving Node service must deduplicate the
Idempotency-Key header and upsert using externalAppointmentId. The latter is a stable
appointment UUID; the former is a stable delivery UUID, retained across retries.
The receiver must acknowledge only after committing the update. This external receiver
is outside this repository and must be verified before rollout. Monitor
csr_outbox_deliveries_total and pending/oldest outbox rows; investigate persistent
4xx errors rather than repeatedly creating replacement appointments.

## Performance controls

| Setting | Default | Behavior |
| --- | --- | --- |
| CHAT_TURN_TIMEOUT_SECONDS | 60 | Total mutation deadline; HTTP 504 / WebSocket turn_timeout |
| LLM_BOUNDED_TIMEOUTS_ENABLED | true | Finite provider reads |
| LLM_READ_TIMEOUT_GENERATION_SECONDS | 20 | Generation read budget |
| LLM_READ_TIMEOUT_EXTRACTION_SECONDS | 8 | Extraction read budget |
| PROMPT_CACHE_ENABLED | true | Existing provider prompt caching |
| EVENT_LOG_BATCHING_ENABLED | false | Batch noncritical audit events |
| TRG_BACKGROUND_PERSIST_ENABLED | false | Overlap graph persistence within the turn boundary |
| LLM_EXTRACTION_OVERLAP_ENABLED | false | Overlap independent extraction work |
| TRIMATCH_EVENT_EVIDENCE_SUMMARY | false | Store compact rule evidence |
| RAG_VECTOR_SEARCH_MODE | exact | Opt-in knn; same approval/service filters |
| RAG_KNN_NUM_CANDIDATES | 100 | ANN candidate count |

BM25 and vector queries run concurrently. Exact search remains the default until
representative retrieval evaluation justifies ANN. Run:

    uv run python scripts/perf/benchmark_retrieval.py cases.json --index YOUR_RAG_ALIAS

Each JSON case contains query, a matching indexed-model embedding, optional service,
and nonempty human-labeled relevant_ids. The benchmark alternates modes, warms each
mode and reports p50/p95 latency and recall@8. Compare quality and latency on staging
before enabling ANN or the opt-in concurrency flags. No production speedup or quality
equivalence is assumed from unit tests.

The /api/v1/chat/ws/stream endpoint now resolves separately from the UUID thread route.
Client chunks are emitted after the full response pipeline validates the text.
This improves delivery behavior but does not claim lower provider time to first token.

Actual structured-call provider usage is exported as llm_provider_tokens_total,
including cache reads and writes. Set LLM_PRICE_PER_MILLION_TOKENS to a JSON map
from exact model IDs to input, output, cache_read and cache_write USD rates.
Use current rates from your provider agreement. The llm_provider_cost_usd_total metric
is emitted only when every reported positive category has a finite, nonnegative rate;
missing pricing is not represented as zero cost. No customer text is used as a label.

Intent classification/audit and turn execution now live in services/turn/.
The remaining chat orchestration retains its established domain behavior for
incremental follow-up refactoring.

## Validation and image handoff

The make ci-local command runs lint, strict typing, tests, corpus/configuration verifiers,
secret scanning, vulnerability auditing and Compose validation. CI executes these
independently so a lint failure cannot hide the test or security results.

The PostgreSQL job uses a disposable database, applies migrations, tests competing
state/action writers and lock ordering, then downgrades/upgrades the new revision.
The image job runs a non-root container, checks liveness/readiness and the migration
head, and uploads bookcraft-image-COMMIT for seven days. It never pushes to a registry
or deploys. Run environment-specific dependency readiness and authenticated chat/CSR
smoke tests in the separate deployment pipeline before routing traffic.


## Validation evidence — 2026-10-10

The initial hardening run exposed older conversation-policy assertions and source
metadata inconsistencies. The follow-up revision reconciles those expectations
with welcome-first contact capture, answer-before-capture, explicit booking forms,
and the canonical manuscript status, and repairs the curated source metadata.
Failures remain blocking; no new skip or xfail is used to hide them.

At commit `63bebbb`, the complete Linux suite passed with 2,585 tests and 29
environment-dependent skips. Lint, strict typing, secret scanning, dependency
auditing and corpus/configuration verifiers also passed. The image built, passed
its fixable high/critical vulnerability gate and both service smoke tests, then
exported an image archive, SPDX SBOM, checksums and provenance/SBOM attestations.
[Validation run](https://github.com/MoeezTrusoft/ai-chatbot-backend/actions/runs/38006676513).

That run also caught consultation UUID schema drift in the migrated PostgreSQL
booking path. Revision `20261010_0004` aligns the legacy identity columns with
the ORM; the booking commit handler rolls back exclusion, serialization and
deadlock conflicts so clients can refresh and retry the whole transaction.
See [PostgreSQL transaction retry guidance](https://www.postgresql.org/docs/16/mvcc-serialization-failure-handling.html).
The dedicated PostgreSQL job exercises this migration and simultaneous bookings,
including the absence of partial thread state or outbox events for a losing write.

Always require every job on the final branch commit to pass before merging.
Historical run results above are evidence for their stated commit, not a substitute
for the current branch checks. Deployment remains separate. Live provider quality
and latency, reminder deliverability, external upload-service deletion and
environment-specific authenticated workflows require staging acceptance.

See [workspace and receiver operations](workspace-and-receiver.md) for migration
preflight, the companion receiver, customer data controls and configuration.
