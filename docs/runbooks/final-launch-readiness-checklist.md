# Final Launch Readiness Checklist

## Purpose

This is the final operator checklist before controlled staging traffic for the BookCraft AI chatbot.

It does not introduce runtime behavior.

It references the existing readiness, smoke, diagnostics, RAG, live-mode, and observability runbooks.

## Current Verdict

```text
Production candidate: YES
Controlled staging ready: YES
Blind full production ready: NOT YET
Blind full production should wait until controlled staging has produced clean live traffic, monitoring, rollback, and incident-response evidence.

Required Runbooks and Reports

Before launch, confirm these files exist and were run successfully:

docs/runbooks/chatbot-complex-message-diagnostics.md
docs/runbooks/chatbot-production-readiness-audit.md
docs/runbooks/live-mode-readiness-audit.md
docs/runbooks/api-smoke-runbook.md
docs/runbooks/observability-collector-readiness.md
docs/runbooks/rag-production-rollout-runbook.md
docs/runbooks/rag-external-rollout-checklist-report.md
docs/runbooks/rag-elasticsearch-smoke-report.md
docs/runbooks/rag-source-service-category-coverage-audit.md
Pre-Launch Infrastructure Checklist
1. Core services
docker compose ps postgres redis elasticsearch tei

Expected:

postgres running
redis reachable
elasticsearch healthy
tei reachable
2. Observability services
docker compose ps otel-collector prometheus grafana loki

Expected:

otel-collector running
prometheus running
grafana running
loki ready

If needed:

docker compose up -d otel-collector prometheus grafana loki

Then:

uv run python scripts/data/run_observability_collector_readiness.py --check-externals

Go condition:

valid=true
error_count=0
Pre-Launch Application Checklist
1. Production readiness audit
uv run python scripts/data/run_chatbot_production_readiness_audit.py \
  --profile local \
  --check-externals

Go condition:

valid=true
production_candidate=true
controlled_staging_ready=true
error_count=0
2. Live-mode readiness audit

Safe local mode:

uv run python scripts/data/run_live_mode_readiness_audit.py \
  --profile local \
  --check-externals

Staging mode, only after staging secrets are configured:

uv run python scripts/data/run_live_mode_readiness_audit.py \
  --profile staging \
  --require-live-config \
  --check-externals

Go condition for controlled live staging:

valid=true
ready_for_controlled_live_staging=true
error_count=0
3. 50-message diagnostic report
uv run python scripts/data/run_chatbot_complex_message_diagnostics.py --check-rag

Go condition:

valid=true
message_count=50
trg_failed_count=0
trg_missing_count=0
trimatch_missing_count=0
intent_missing_count=0
rag_failure_count=0
RAG Launch Checklist
1. Confirm live alias
curl -s "http://localhost:9200/_cat/aliases?v"

Go condition:

bookcraft_rag_current points to a green smoke-passing index
2. Run RAG smoke
uv run python scripts/data/run_rag_elasticsearch_smoke_report.py \
  --check-externals \
  --require-externals

Go condition:

valid=true
passed_turns=5
failed_turns=0
errors=[]
3. Confirm source metadata coverage
uv run python scripts/data/audit_rag_source_service_category_coverage.py --strict

Go condition:

coverage_passed=true
error_count=0
API Smoke Checklist

Start local/staging API:

APP_ENV=dev \
LLM_PROVIDER_MODE=mock \
READINESS_CHECK_EXTERNALS=true \
uv run uvicorn bookcraft.api.main:app --host 0.0.0.0 --port 8000

Smoke API:

uv run python scripts/data/run_api_smoke_report.py \
  --base-url http://localhost:8000

Go condition:

valid=true
error_count=0

For staging JWT mode:

uv run python scripts/data/run_api_smoke_report.py \
  --base-url "$STAGING_API_BASE_URL" \
  --expect-auth \
  --jwt-signing-key "$JWT_SIGNING_KEY"

For metrics protection:

uv run python scripts/data/run_api_smoke_report.py \
  --base-url "$STAGING_API_BASE_URL" \
  --expect-metrics-protected \
  --metrics-token "$METRICS_BEARER_TOKEN"
Go / No-Go Criteria
Go

Launch controlled staging only if all are true:

production readiness audit passes
live-mode readiness audit passes for intended mode
50-message diagnostic passes
RAG smoke passes
API smoke passes
observability external readiness passes
NDA mode is not autonomous
agreement mode is not autonomous
metrics are protected when public
JWT auth is enabled for staging/production
No-Go

Do not launch if any are true:

DB check fails
Redis check fails
RAG alias missing
RAG smoke fails
TRG missing or failing in diagnostics
Tri-Match missing in diagnostics
intent classifier missing in diagnostics
metrics public without token
JWT auth missing in staging/production
NDA or agreement mode set to autonomous
live LLM keys missing when live mode is required
observability collector unavailable in staging
Rollback Actions
RAG rollback

If RAG retrieval regresses:

curl -s "http://localhost:9200/_cat/aliases?v"

Move bookcraft_rag_current back to the last known healthy index.

Do not delete failed candidate indices until reports and logs are reviewed.

API rollback

If API smoke fails after deploy:

stop new traffic
restore previous deployment image/commit
rerun API smoke report
rerun production readiness audit
check logs and traces
Live LLM rollback

If live provider behavior regresses:

switch LLM_PROVIDER_MODE back to mock or previous stable mode
disable customer-facing traffic
capture diagnostic report
review provider errors/timeouts
Document safety rollback

If document gating regresses:

set NDA_MODE=manual
set AGREEMENT_MODE=manual
disable PDF rendering if needed
review tool audit logs
Post-Launch Monitoring

For the first controlled staging window, monitor:

HTTP 5xx rate
HTTP 429 rate
chat latency p95
intent disagreement rate
Tri-Match disagreement logs
TRG failure count
RAG retrieval failures
pricing/document gating failures
Redis errors
Postgres errors
Elasticsearch errors
LLM timeout/error rate
OTLP collector export errors
Loki readiness
Prometheus scrape health
Grafana dashboards
Final Operator Sign-Off

Before controlled staging traffic:

[ ] main branch is current
[ ] all merged branches are deleted
[ ] local/staging services are healthy
[ ] RAG alias points to smoke-passing index
[ ] API smoke report passes
[ ] production readiness audit passes
[ ] live-mode readiness audit passes for intended mode
[ ] observability readiness passes
[ ] rollback plan is understood
[ ] monitoring window is staffed
[ ] blind full production is still blocked until staging evidence is reviewed

