# Production Threaded Component Load Report

## Purpose

Run 100 controlled production canary messages split into random 10-20 message threads.

Outputs:

```text
reports/production/production_threaded_component_load_report.json
reports/production/production_threaded_component_load_report.md
reports/production/production_threaded_component_load_report.html
reports/production/production_threaded_component_load_report.pdf
What it analyzes
thread-level latency
thread-level warning pressure
provider vote health
fallback usage
Tri-Match disagreements
TRG presence/failure
decision-layer presence/failure
RAG hard failures
response-quality warnings
intent/service distribution
Run
set -a
source .env.production.local
set +a

uv run python scripts/data/run_production_threaded_component_load_report.py \
  --base-url "$STAGING_API_BASE_URL" \
  --jwt-signing-key "$JWT_SIGNING_KEY" \
  --customer-id "$SMOKE_CUSTOMER_ID" \
  --database-url "$DATABASE_URL" \
  --message-count 100 \
  --min-thread-size 10 \
  --max-thread-size 20 \
  --seed-customer \
  --pdf
Expected result
valid=true
message_count=100
success_count=100
failure_count=0
critical_issue_count=0

Soft warnings may still be high while provider ensemble health is being optimized.
