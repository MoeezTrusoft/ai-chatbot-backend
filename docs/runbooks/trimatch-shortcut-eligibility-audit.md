# Tri-Match Shortcut Eligibility Audit Runbook

## Purpose

This report update audits shortcut eligibility without enabling shortcut application.

It verifies:

- `eligible_count` is reported
- `eligible_not_applied_count` is reported
- `applied_count` remains `0`
- `side_effects_allowed_count` remains `0`
- sensitive pricing/document/portfolio cases remain blocked
- applied dimension/value/rule counters remain empty before application

## Commands

```bash
uv run python scripts/data/run_trimatch_shortcut_audit_report.py
uv run pytest tests/integration/test_trimatch_shortcut_eligibility_audit.py -q
Safety

This branch is reporting-only.

It does not apply shortcuts, activate Rules Army v2 globally, enable direct shortcut routing, change final intent, change extraction, change pricing, change portfolio, generate NDA/agreement text, change RAG routing, or change response generation.
