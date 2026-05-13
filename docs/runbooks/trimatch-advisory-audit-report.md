# Tri-Match Advisory Audit Report Runbook

## Purpose

This report runs controlled advisory-mode review cases and summarizes advisory recommendations.

It checks:

- advisory recommendation events are logged
- shadow events are not logged in advisory mode
- `advisory_applied` remains `false`
- `side_effects_allowed` remains `false`
- advisory/final matches are counted
- advisory/final differences are counted
- sensitive advisory recommendations are counted

## Command

```bash
uv run python scripts/data/run_trimatch_advisory_audit_report.py
Outputs
reports/trimatch/trimatch_advisory_audit_report.json
reports/trimatch/trimatch_advisory_audit_report.md
Safety

This report is observational only.

It does not:

activate Rules Army v2
enable tiebreaker mode
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
uv run python scripts/data/run_trimatch_advisory_audit_report.py
uv run pytest tests/integration/test_trimatch_extra_advisory_mode.py -q
uv run pytest tests/integration/test_trimatch_reinforcement_governance_smoke.py -q
uv run mypy src
