# Tri-Match Tiebreaker Audit Report Runbook

## Purpose

This report runs controlled tiebreaker-candidate review cases and verifies the mode remains consideration-only.

It checks:

- `trimatch.extra_tiebreaker_considered` events are logged
- advisory events are not logged in tiebreaker mode
- shadow events are not logged in tiebreaker mode
- `decision.eligible` remains `false`
- `decision.applied` remains `false`
- `safety.side_effects_allowed` remains `false`
- pricing/document/portfolio sensitivity flags are counted
- final intent remains unchanged by tiebreaker consideration

## Command

```bash
uv run python scripts/data/run_trimatch_tiebreaker_audit_report.py
Outputs
reports/trimatch/trimatch_tiebreaker_audit_report.json
reports/trimatch/trimatch_tiebreaker_audit_report.md
Safety

This report is observational only.

It does not:

apply tiebreakers
activate Rules Army v2
enable shortcut mode
change final intent
change extraction
change pricing
change portfolio
generate NDA text
generate agreement text
change RAG routing
change response generation
Recommended validation
uv run python scripts/data/run_trimatch_tiebreaker_audit_report.py
uv run pytest tests/integration/test_trimatch_tiebreaker_candidate_considered.py -q
uv run pytest tests/integration/test_trimatch_tiebreaker_governance_smoke.py -q
uv run mypy src

