# Tri-Match Human Review Batch Tools Runbook

## Purpose

These tools make the human-review stage easier without bypassing human approval.

They support:

- building a review queue from pending candidates
- generating a non-approval review batch template
- validating reinforcement data after manual review edits

## Tools

```bash
uv run python scripts/data/build_trimatch_human_review_queue.py
uv run python scripts/data/create_trimatch_review_batch_template.py
Outputs
reports/trimatch/trimatch_human_review_queue.json
reports/trimatch/trimatch_human_review_queue.md
reports/trimatch/trimatch_review_batch_template.jsonl
Review workflow
Build the review queue.
Generate a batch template.
Human reviewer opens the template and inspects each candidate.
Human reviewer manually edits decisions when appropriate.
Only manually reviewed JSONL rows should be copied into:
data/trimatch/reinforcement/reviews/generated/
Run validation:
uv run python scripts/data/validate_trimatch_reinforcement.py
Only after validation, compile approved candidates into a staged RulePack:
uv run python scripts/data/compile_approved_trimatch_candidates.py
Safety

The batch template generator intentionally uses safe non-approval decisions only:

defer
needs_more_examples
reject
duplicate
unsafe

It does not generate approve or edit_and_approve.

This prevents accidental activation through the approved-candidate compiler.

These tools do not:

activate Rules Army v2
activate approved candidate RulePacks
move Tri-Match into advisory mode
move Tri-Match into tiebreaker mode
enable shortcut behavior
change runtime classification
change pricing behavior
change portfolio behavior
generate NDA or agreement text
