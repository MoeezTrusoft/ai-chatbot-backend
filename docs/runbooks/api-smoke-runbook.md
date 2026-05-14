# API Smoke Runbook

## Purpose

Smoke a running BookCraft API process before controlled staging traffic.

Checks include:

```text
GET /healthz
GET /readyz
POST /api/v1/chat/turn
GET /metrics
optional auth enforcement
optional metrics protection
optional rate-limit behavior
Safe dry-run
uv run python scripts/data/run_api_smoke_report.py
Start local API

Example local command:

APP_ENV=dev \
LLM_PROVIDER_MODE=mock \
READINESS_CHECK_EXTERNALS=true \
uv run uvicorn bookcraft.api.main:app --host 0.0.0.0 --port 8000
Smoke local API without auth
uv run python scripts/data/run_api_smoke_report.py \
  --base-url http://localhost:8000
Smoke local/staging API with JWT auth

Use only a staging/local signing key:

uv run python scripts/data/run_api_smoke_report.py \
  --base-url http://localhost:8000 \
  --expect-auth \
  --jwt-signing-key "$JWT_SIGNING_KEY"
Smoke metrics protection
uv run python scripts/data/run_api_smoke_report.py \
  --base-url http://localhost:8000 \
  --expect-metrics-protected \
  --metrics-token "$METRICS_BEARER_TOKEN"
Rate-limit probe

This intentionally sends repeated chat requests.

uv run python scripts/data/run_api_smoke_report.py \
  --base-url http://localhost:8000 \
  --rate-limit-probe \
  --rate-limit-attempts 35
Outputs
reports/chatbot/api_smoke_report.json
reports/chatbot/api_smoke_report.md
Safety

This smoke report does not create Elasticsearch indices, move aliases, send emails, create legal documents, or directly call live LLM providers.
