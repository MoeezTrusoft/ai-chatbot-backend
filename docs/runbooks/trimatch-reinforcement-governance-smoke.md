# Tri-Match Reinforcement Governance Smoke Test Runbook

## Purpose

This smoke test verifies that the Tri-Match reinforcement pipeline remains human-governed.

It checks that:

- review batch templates use non-approval decisions by default
- approval review rows are blocked unless explicitly allowed
- dry-run ingestion does not write files
- explicit `--apply` is required to copy review rows
- compiled approved candidates remain staged and shortcut-disabled
- runtime shadow review remains observational and passes

## Command

```bash
uv run pytest tests/integration/test_trimatch_reinforcement_governance_smoke.py -q
Safety

This test does not activate Rules Army v2 or approved candidate RulePacks.

It does not enable advisory mode, tiebreaker mode, or shortcut mode.

It does not change pricing, portfolio, NDA, agreement, or response-routing behavior.
