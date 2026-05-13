# Tri-Match Shadow Runtime Review Runbook

## Purpose

The shadow runtime review runs structured chat cases with extra Tri-Match RulePacks enabled in shadow mode.

It verifies that:

- extra shadow rules run
- disagreement/shadow events are logged
- final responses remain governed by the normal runtime path
- no activation occurs

## Command

```bash
uv run python scripts/data/run_trimatch_shadow_runtime_review.py
Outputs
reports/trimatch/trimatch_shadow_runtime_review.json
reports/trimatch/trimatch_shadow_runtime_review.md
Safety

This review is observational only.

It does not activate Rules Army v2 or approved candidate RulePacks.

Promotion path
shadow runtime review
→ calibration report
→ human review
→ advisory candidate
→ tiebreaker candidate
→ shortcut candidate
