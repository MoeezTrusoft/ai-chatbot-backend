# Tri-Match Review Template Ingestion Runbook

## Purpose

This tool validates a manually edited Tri-Match review batch JSONL file and optionally copies it into the reinforcement review store.

It is designed for this safe flow:

```text
review queue
→ safe non-approval batch template
→ manual human edits
→ ingestion dry-run
→ explicit ingestion with --apply
→ reinforcement validation
→ approved candidate compiler
Default dry-run
uv run python scripts/data/import_trimatch_review_batch_template.py

The default mode validates only. It does not write files.

Apply after manual review
uv run python scripts/data/import_trimatch_review_batch_template.py --apply
Approval safety

Approval decisions are blocked unless explicitly allowed:

uv run python scripts/data/import_trimatch_review_batch_template.py \
  --apply \
  --allow-approval-decisions

This is intentional. The template generator does not create approvals; approvals must be manually edited by a human reviewer.

Risky promotion scope safety

These promotion scopes are blocked by default:

advisory
tiebreaker_candidate
shortcut_candidate

To ingest them, a reviewer must explicitly pass:

uv run python scripts/data/import_trimatch_review_batch_template.py \
  --apply \
  --allow-risky-promotion-scope
Recommended validation after ingestion
uv run python scripts/data/validate_trimatch_reinforcement.py
uv run python scripts/data/compile_approved_trimatch_candidates.py \
  --version approved_candidates.sample.v1

For compile checks that should not modify the tracked staged RulePack, write to a report path:

uv run python scripts/data/compile_approved_trimatch_candidates.py \
  --version approved_candidates.ingestion_check.v1 \
  --output reports/trimatch/approved_candidates.ingestion_check.rulepack.json
Safety

This tool does not:

generate approvals
compile rules automatically
activate Rules Army v2
activate approved candidate RulePacks
enable advisory mode
enable tiebreaker mode
enable shortcut mode
change runtime classification
change pricing behavior
change portfolio behavior
generate NDA or agreement text
