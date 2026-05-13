# Tri-Match Shortcut Application Governance Smoke Runbook

## Purpose

This smoke test protects gated shortcut application after merge.

It verifies:

- shortcut audit remains valid
- at least one safe shortcut can apply
- sensitive pricing/document/portfolio shortcuts stay blocked
- `side_effects_allowed` remains `false`
- shortcut application does not bypass pricing/document/portfolio/RAG/response/tool systems
- `shortcut_candidate` uses `TriMatchMode.SHORTCUT_ENABLED` only for extra shortcut evaluation
- rollback remains config-only through `TRIMATCH_EXTRA_MODE=off`

## Command

```bash
uv run pytest tests/integration/test_trimatch_shortcut_application_governance_smoke.py -q
Safety

This test does not change runtime behavior.

It does not activate Rules Army v2 globally, bypass deterministic pricing, bypass portfolio registry, bypass NDA/agreement templates, change RAG routing directly, invoke tools directly, or bypass response generation.
