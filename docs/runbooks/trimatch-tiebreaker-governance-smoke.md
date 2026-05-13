# Tri-Match Tiebreaker Governance Smoke Test Runbook

## Purpose

This smoke test protects the system before tiebreaker candidate mode is implemented.

It verifies that:

- `TRIMATCH_EXTRA_MODE=tiebreaker_candidate` is rejected today
- advisory mode does not emit tiebreaker events
- advisory recommendations remain logging-only
- tiebreaker design docs block sensitive query intents
- tiebreaker design docs require `side_effects_allowed=false`
- readiness runbook references all required safety checks

## Command

```bash
uv run pytest tests/integration/test_trimatch_tiebreaker_governance_smoke.py -q
Safety

This test does not implement tiebreaker mode.

It does not activate Rules Army v2, approved candidate RulePacks, tiebreaker behavior, shortcut behavior, pricing behavior, portfolio behavior, NDA generation, agreement generation, RAG routing, or response routing.
