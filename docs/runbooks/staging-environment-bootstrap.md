# Staging Environment Bootstrap Runbook

## Purpose

This runbook explains how to configure a staging environment for the BookCraft chatbot without committing secrets.

Use `.env.staging.example` as the safe reference template.

Do not commit real `.env`, `.env.staging.local`, API keys, JWT keys, database passwords, metrics tokens, or provider credentials.

## 1. Create staging secret set

Copy the example into your real secret manager or local staging file:

```bash
cp .env.staging.example .env.staging.local
Then replace all placeholder values:

<user>
<password>
<postgres-host>
<database>
<redis-host>
<elasticsearch-host>
<tei-host>
<staging-frontend-domain>
<set-in-secret-manager>
2. Required staging safety settings

For controlled staging, these should be true:

APP_ENV=staging
API_AUTH_MODE=jwt
READINESS_CHECK_EXTERNALS=true
NDA_MODE=manual
AGREEMENT_MODE=manual
DOCUMENT_PDF_RENDERING_ENABLED=false
METRICS_PUBLIC=false
TRIMATCH_EXTRA_MODE=off
RAG_INDEX_ALIAS=bookcraft_rag_current
3. Initial boot mode

Start with mock mode:

LLM_PROVIDER_MODE=mock

Then run:

uv run python scripts/data/run_chatbot_production_readiness_audit.py \
  --profile staging \
  --check-externals
4. Live-mode preflight

Only after API keys are configured:

LLM_PROVIDER_MODE=live
ANTHROPIC_API_KEY=<secret>
OPENAI_API_KEY=<secret>
JWT_SIGNING_KEY=<secret>

Run:

uv run python scripts/data/run_live_mode_readiness_audit.py \
  --profile staging \
  --require-live-config \
  --check-externals

Go condition:

valid=true
ready_for_controlled_live_staging=true
error_count=0
5. RAG preflight
uv run python scripts/data/run_rag_elasticsearch_smoke_report.py \
  --check-externals \
  --require-externals

uv run python scripts/data/audit_rag_source_service_category_coverage.py --strict

Go condition:

RAG smoke valid=true
failed_turns=0
coverage_passed=true
error_count=0
6. API smoke

Start the API with staging env loaded by your process manager.

Then run:

uv run python scripts/data/run_api_smoke_report.py \
  --base-url "$STAGING_API_BASE_URL" \
  --expect-auth \
  --jwt-signing-key "$JWT_SIGNING_KEY"

For metrics:

uv run python scripts/data/run_api_smoke_report.py \
  --base-url "$STAGING_API_BASE_URL" \
  --expect-metrics-protected \
  --metrics-token "$METRICS_BEARER_TOKEN"
7. Observability preflight
uv run python scripts/data/run_observability_collector_readiness.py --check-externals

Go condition:

valid=true
error_count=0
8. Final checklist

Before controlled staging traffic, run through:

docs/runbooks/final-launch-readiness-checklist.md
No-Go conditions

Do not launch staging traffic if:

JWT auth is off
live LLM keys are missing when live mode is required
metrics are public without token
NDA_MODE=autonomous
AGREEMENT_MODE=autonomous
RAG alias is missing
RAG smoke fails
DB or Redis connectivity fails
observability collector fails in staging

