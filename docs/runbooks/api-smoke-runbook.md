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
```

## Safe dry-run

```bash
uv run python scripts/data/run_api_smoke_report.py
```

## Start local API

Example local command:

```bash
APP_ENV=dev \
LLM_PROVIDER_MODE=mock \
READINESS_CHECK_EXTERNALS=true \
uv run uvicorn bookcraft.api.main:app --host 0.0.0.0 --port 8000
```

## Smoke local API without auth

```bash
uv run python scripts/data/run_api_smoke_report.py \
  --base-url http://localhost:8000
```

## Smoke local/staging API with JWT auth

Use only a staging/local signing key:

```bash
uv run python scripts/data/run_api_smoke_report.py \
  --base-url http://localhost:8000 \
  --expect-auth \
  --jwt-signing-key "$JWT_SIGNING_KEY"
```

## Smoke with an existing seeded customer

When `API_AUTH_MODE=jwt`, the JWT may include a `customer_id`.

For local/staging smoke, seed a customer first, then pass the same ID:

```bash
uv run python scripts/data/run_api_smoke_report.py \
  --base-url "$STAGING_API_BASE_URL" \
  --expect-auth \
  --jwt-signing-key "$JWT_SIGNING_KEY" \
  --customer-id "$SMOKE_CUSTOMER_ID"
```

The customer must already exist in the `customers` table.

## Smoke metrics protection

```bash
uv run python scripts/data/run_api_smoke_report.py \
  --base-url http://localhost:8000 \
  --expect-metrics-protected \
  --metrics-token "$METRICS_BEARER_TOKEN"
```

## Rate-limit probe

This intentionally sends repeated chat requests.

```bash
uv run python scripts/data/run_api_smoke_report.py \
  --base-url http://localhost:8000 \
  --rate-limit-probe \
  --rate-limit-attempts 35
```

## Outputs

```text
reports/chatbot/api_smoke_report.json
reports/chatbot/api_smoke_report.md
```

## Safety

This smoke report does not create Elasticsearch indices, move aliases, send emails, create legal documents, or directly call live LLM providers.
