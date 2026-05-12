# Deployment and Promotion Guide

## Stage 0 — Validation

```bash
python scripts/validate_rules_army.py .
```

## Stage 1 — Shadow load

Load `*.v2.rules_army.json` as a separate rule-pack version. Keep `TRIMATCH_MODE=shadow`.

Collect:
- would_match
- would_correct
- would_override
- would_break
- rule_id evidence

## Stage 2 — Human review

Use `data/trimatch/candidates/schema.json` and `data/trimatch/reviews/schema.json`. No rule promoted without positive/negative examples and human approval.

## Stage 3 — Advisory mode

Tri-Match enriches LLM decisions but does not override. Watch disagreement logs.

## Stage 4 — Tiebreaker mode

Only exact/regex rules with measured precision >= 0.93 can break LLM disagreements.

## Stage 5 — Shortcut mode

Only rules with measured precision >= 0.985, zero safety regressions, and reviewer approval may shortcut LLM classification.
