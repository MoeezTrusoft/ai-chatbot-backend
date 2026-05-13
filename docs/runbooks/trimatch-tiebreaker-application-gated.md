# Tri-Match Tiebreaker Application Gated Runbook

## Purpose

This phase allows tightly gated tiebreaker application for safe intent dimensions only.

Allowed dimensions:

```text
service_primary
query_primary
Forbidden / blocked areas:

pricing
timeline
portfolio
NDA
agreement
payment
complaints
ready_to_buy
spam/off-topic
negation
counterfactual
Guarantees

Even when a tiebreaker applies:

{
  "safety": {
    "side_effects_allowed": false
  }
}

The implementation does not directly call:

pricing engine
portfolio registry
document generator
RAG retriever
response generator
tool dispatcher

It only updates the internal IntentVote before the normal downstream pipeline continues.

Validation
uv run pytest tests/integration/test_trimatch_tiebreaker_application_gated.py -q
uv run pytest tests/integration/test_trimatch_tiebreaker_eligibility_evaluator.py -q
uv run python scripts/data/run_trimatch_tiebreaker_audit_report.py
uv run mypy src
Rollback

Disable through config:

TRIMATCH_EXTRA_MODE=off

