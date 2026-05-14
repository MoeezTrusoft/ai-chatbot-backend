# Production Component Performance Report Runbook

## Purpose

Run controlled canary messages against the configured API and generate:

```text
reports/production/production_component_performance_report.json
reports/production/production_component_performance_report.md
reports/production/production_component_performance_report.html
reports/production/production_component_performance_report.pdf
The report analyzes:

HTTP status
latency avg/p50/p95/max
Decision Layer
Tri-Match classification
intent/NLP classification
extraction
TRG
Elasticsearch RAG failure events
assistant response events
raw response and raw thread_events
Required env
set -a
source .env.production.local
set +a

Required values:

STAGING_API_BASE_URL
JWT_SIGNING_KEY
SMOKE_CUSTOMER_ID
DATABASE_URL
Run with existing canary customer
uv run python scripts/data/run_production_component_performance_report.py \
  --base-url "$STAGING_API_BASE_URL" \
  --jwt-signing-key "$JWT_SIGNING_KEY" \
  --customer-id "$SMOKE_CUSTOMER_ID" \
  --database-url "$DATABASE_URL" \
  --message-count 10 \
  --pdf
Run and seed canary customer
uv run python scripts/data/run_production_component_performance_report.py \
  --base-url "$STAGING_API_BASE_URL" \
  --jwt-signing-key "$JWT_SIGNING_KEY" \
  --customer-id "$SMOKE_CUSTOMER_ID" \
  --database-url "$DATABASE_URL" \
  --message-count 10 \
  --seed-customer \
  --pdf
Safety

This script sends controlled test messages to the configured API. It does not send emails, create legal documents, create Elasticsearch indices, or move aliases.
