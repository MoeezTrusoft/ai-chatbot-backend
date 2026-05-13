# Tri-Match Shortcut Audit Report Runbook

## Purpose

This report runs controlled shortcut-candidate review cases and verifies the mode remains consideration-only.

It checks:

- `trimatch.extra_shortcut_considered` events are logged
- tiebreaker/advisory/shadow events are not logged in shortcut mode
- `shortcut.eligible` remains `false`
- `shortcut.applied` remains `false`
- `safety.side_effects_allowed` remains `false`
- pricing/document/portfolio sensitivity flags are counted
- final intent remains unchanged by shortcut consideration

## Command

```bash
uv run python scripts/data/run_trimatch_shortcut_audit_report.py
Outputs
reports/trimatch/trimatch_shortcut_audit_report.json
reports/trimatch/trimatch_shortcut_audit_report.md
Safety

This report is observational only.

It does not:

apply shortcuts
activate Rules Army v2 globally
enable direct shortcut routing
change final intent
change extraction
change pricing
change portfolio
generate NDA text
generate agreement text
change RAG routing
change response generation
Recommended validation
uv run python scripts/data/run_trimatch_shortcut_audit_report.py
uv run pytest tests/integration/test_trimatch_shortcut_candidate_considered.py -q
uv run pytest tests/integration/test_trimatch_shortcut_governance_smoke.py -q
uv run mypy src
