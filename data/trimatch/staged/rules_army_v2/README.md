# Tri-Match Rules Army v2

This package is a staged, production-oriented rule-army for BookCraft's Tri-Match Intent Classification Engine.

## Counts

- Service intent rules: **458**
- Query intent rules: **342**
- Funnel stage rules: **147**
- Total schema-compatible RulePack rules: **947**
- Core eval examples: **374**
- Advanced context eval examples: **8**

## Important production note

The rule files under `data/trimatch/rules/` are compatible with the current RulePack shape: `version` + `rules`, and each rule uses only current schema fields. The advanced context file under `data/trimatch/sidecars/_context_rules.v1.json` and examples under `data/trimatch/eval_advanced/` require the next preprocessor upgrades: terminator-aware negation, backward negation scope, ordered service atoms, and counterfactual tagging.

## Recommended use

1. Do not replace production v1 rules directly.
2. Copy this package into a branch.
3. Run `python scripts/validate_rules_army.py .`.
4. Load as `v2.rules_army` in shadow mode first.
5. Run the 50-message production-flow test in mock and live mode.
6. Route advanced eval failures to human review.
7. Promote high-precision exact/regex rules only after calibration.

## Why this exists

The reviewed advisories identified that production Tri-Match was too small and `intent/hardening.py` was doing classifier work that belongs in governed rule packs. This package moves the system toward the intended architecture: LLMs propose, humans approve, and Tri-Match becomes the fast audited domain classifier.
