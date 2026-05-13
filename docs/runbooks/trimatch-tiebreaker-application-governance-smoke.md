# Tri-Match Tiebreaker Application Governance Smoke Runbook

## Purpose

This smoke test protects the gated tiebreaker application phase.

It verifies:

- safe `service_primary` tiebreaker can apply
- safe `query_primary` tiebreaker can apply
- pricing-sensitive tiebreaker cannot apply
- document-sensitive tiebreaker cannot apply
- portfolio-sensitive tiebreaker cannot apply
- unsupported dimensions cannot apply
- `side_effects_allowed` remains `false`

## Command

```bash
uv run pytest tests/integration/test_trimatch_tiebreaker_application_governance_smoke.py -q
Safety

This test does not enable shortcut mode.

It does not activate Rules Army v2 globally.

It does not bypass deterministic pricing, portfolio registry, NDA/agreement templates, RAG routing, or response generation.
