# Tri-Match Shortcut Governance Smoke Test Runbook

## Purpose

This smoke test protects the system before shortcut mode is implemented.

It verifies that:

- `TRIMATCH_EXTRA_MODE=shortcut_candidate` is rejected today
- shortcut design blocks sensitive query intents
- shortcut design requires exact/regex-only scope
- shortcut design rejects semantic/fuzzy shortcut scope
- shortcut design requires side effects to stay disabled
- shortcut design forbids direct tool/generation bypass
- shortcut readiness runbook references required reports
- shortcut readiness runbook defines the safe future branch order

## Command

```bash
uv run pytest tests/integration/test_trimatch_shortcut_governance_smoke.py -q
Safety

This test does not implement shortcut mode.

It does not activate Rules Army v2 globally, does not bypass deterministic pricing, does not bypass portfolio registry, does not bypass NDA/agreement templates, does not change RAG routing, and does not change response generation.
