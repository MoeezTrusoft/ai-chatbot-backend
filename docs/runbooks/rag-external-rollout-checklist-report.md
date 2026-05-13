# RAG External Rollout Checklist Report Runbook

## Purpose

This report prepares an operator-facing checklist before touching real Elasticsearch/TEI infrastructure.

Default mode is safe and does not check externals.

## Safe command

```bash
uv run python scripts/data/build_rag_external_rollout_checklist_report.py
Optional external connectivity check
uv run python scripts/data/build_rag_external_rollout_checklist_report.py --check-externals
Outputs
reports/rag/rag_external_rollout_checklist_report.json
reports/rag/rag_external_rollout_checklist_report.md
What it does
runs CI-safe RAG preflight checks
summarizes configured Elasticsearch/TEI/RAG env values
prints exact candidate build/smoke/swap/live-smoke commands
lists stop conditions
lists rollback strategy
What it does not do
does not create Elasticsearch indices
does not embed source documents
does not bulk index content
does not move aliases
does not enable production RAG
Use before

Use this before running:

uv run python scripts/data/build_rag_elasticsearch_index.py --apply

