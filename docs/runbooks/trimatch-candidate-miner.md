# Tri-Match Candidate Miner Runbook

## Purpose

The candidate miner converts diagnostic and shadow-evaluation evidence into human-review candidate proposals.

It does **not** activate rules.

## Inputs

Default inputs:

- `reports/trimatch/rules_army_v2_shadow_eval.json`
- latest `reports/production-flow/production_flow_50_*.json`, if present

## Output

Default output:

```text
data/trimatch/reinforcement/candidates/generated/candidates.auto.jsonl

The generated file is intentionally review-only. Human reviewers must approve, reject, edit, or request more examples before a candidate can become a staged rule.

Command
uv run python scripts/data/mine_trimatch_candidates.py --max-candidates 50
uv run python scripts/data/validate_trimatch_reinforcement.py
Review rules

Generated candidates must not be activated directly.

Reviewers should check:

positive examples
negative examples
risk note
negation behavior
counterfactual behavior
pricing safety
NDA/agreement safety
portfolio safety
broad overmatching risk
Promotion path
generated candidate
→ human review
→ staged rule
→ eval
→ shadow
→ advisory
→ tiebreaker
→ shortcut candidate
Safety note

The candidate miner is a discovery tool only. It can suggest candidate phrases, regexes, semantic clusters, and context patterns, but human review remains mandatory before any rule-pack promotion.
