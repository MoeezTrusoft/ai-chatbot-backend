# Tri-Match Calibration Report Runbook

## Purpose

The calibration report summarizes Tri-Match reinforcement readiness using:

- Rules Army shadow-eval evidence
- reinforcement candidates
- human reviews
- compiled staged RulePacks
- production-flow reports, when available

It does **not** activate any rule pack.

## Command

```bash
uv run python scripts/data/build_trimatch_calibration_report.py
Outputs
reports/trimatch/trimatch_calibration_report.json
reports/trimatch/trimatch_calibration_report.md
Recommendation meanings
hold: shadow regressions must be resolved before promotion
hold: production-flow safety failures must be resolved before promotion
hold: no approved compiled reinforcement rules are ready
ready_for_shadow_runtime_review
continue_collecting_evidence
Promotion path
calibration report
→ human review
→ shadow runtime review
→ advisory candidate
→ tiebreaker candidate
→ shortcut candidate
Safety

This report is observational only.

It should be used to decide whether to continue collecting evidence or prepare staged rules for broader shadow runtime review.
