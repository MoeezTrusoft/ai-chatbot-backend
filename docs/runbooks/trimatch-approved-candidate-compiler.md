# Tri-Match Approved Candidate Compiler Runbook

## Purpose

The approved candidate compiler converts human-approved reinforcement candidates into a staged Tri-Match RulePack.

It does **not** activate rules in production.

## Input

The compiler reads:

```text
data/trimatch/reinforcement/candidates/**/*.jsonl
data/trimatch/reinforcement/reviews/**/*.jsonl
Only reviews with these decisions are compiled:

approve
edit_and_approve
Output

Default output:

data/trimatch/reinforcement/staged_from_reviews/approved_candidates.rulepack.json
Command
uv run python scripts/data/compile_approved_trimatch_candidates.py
Safety model

Compiled rules are staged only.

They must still pass:

RulePack validation
Tri-Match eval
shadow comparison
human calibration review

before any runtime advisory or activation mode.

Promotion path
candidate
→ human review
→ compiled staged RulePack
→ eval
→ shadow
→ advisory
→ tiebreaker
→ shortcut candidate

Shortcut promotion must be handled by a separate safety gate.
