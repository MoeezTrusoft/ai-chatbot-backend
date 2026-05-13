# Tri-Match Shortcut Application Gated Runbook

## Purpose

This phase allows tightly gated shortcut application for safe exact/regex rules only.

Allowed dimensions:

```text
service_primary
query_primary
Forbidden areas:

pricing
timeline
portfolio
NDA
agreement
payment
ready_to_buy
spam/off-topic
semantic/fuzzy evidence
negation
counterfactual
missing rule_id
Guarantees

Even when a shortcut applies:

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
uv run pytest tests/integration/test_trimatch_shortcut_application_gated.py -q
uv run python scripts/data/run_trimatch_shortcut_audit_report.py
uv run mypy src
Rollback

Disable through config:

TRIMATCH_EXTRA_MODE=off

