# Tri-Match Review Ingestion Audit Report Runbook

## Purpose

This report audits Tri-Match human-review rows after template ingestion.

It summarizes:

- review decision counts
- approval/defer/unsafe counts
- promotion-scope risk
- duplicate review IDs
- duplicate candidate review coverage
- unknown candidate references
- candidate review coverage
- unreviewed pending candidates

## Command

```bash
uv run python scripts/data/build_trimatch_review_ingestion_audit_report.py
Outputs
reports/trimatch/trimatch_review_ingestion_audit_report.json
reports/trimatch/trimatch_review_ingestion_audit_report.md
Safety

This tool is observational only.

It does not:

approve candidates
compile candidates
stage RulePacks
activate Rules Army v2
activate approved candidate RulePacks
enable advisory mode
enable tiebreaker mode
enable shortcut mode
change runtime classification
change pricing behavior
change portfolio behavior
generate NDA or agreement text
Recommended workflow
review template ingestion
→ ingestion audit report
→ reinforcement validation
→ approved candidate compiler check
→ calibration report

Recommended commands:

uv run python scripts/data/import_trimatch_review_batch_template.py
uv run python scripts/data/build_trimatch_review_ingestion_audit_report.py
uv run python scripts/data/validate_trimatch_reinforcement.py
uv run python scripts/data/compile_approved_trimatch_candidates.py --version approved_candidates.audit_check.v1 --output reports/trimatch/approved_candidates.audit_check.rulepack.json
uv run python scripts/data/build_trimatch_calibration_report.py
