# Tri-Match Runtime Review Candidate Miner Runbook

## Purpose

This miner reads the Tri-Match shadow runtime review report and turns useful runtime findings into pending human-review candidates.

It is part of the observational reinforcement loop:

```text
shadow runtime review
→ runtime review candidate miner
→ human review
→ approved candidate compiler
→ staged RulePack
→ shadow runtime review again
Command
uv run python scripts/data/mine_trimatch_runtime_review_candidates.py
Input
reports/trimatch/trimatch_shadow_runtime_review.json
Output
data/trimatch/reinforcement/candidates/generated/runtime_review_candidates.auto.jsonl
Optional disagreement mining

By default, the script mines failed runtime review cases.

To also mine passed-turn shadow/final disagreements, run:

uv run python scripts/data/mine_trimatch_runtime_review_candidates.py --include-passed-disagreements

Synthetic marker cases are always skipped.

Safety

This script only creates pending human-review candidates.

It does not:

activate Rules Army v2
activate approved candidate RulePacks
change runtime classification
change pricing behavior
change portfolio behavior
generate NDA or agreement text
bypass deterministic quote gating
compile candidates into staged rules
