# RAG Source Service Category Coverage Audit Runbook

## Purpose

This audit checks whether curated RAG source files are mapped to the expected service category.

The normal metadata verifier checks whether a `service_category` value is valid.

This audit checks whether the value is correct for the file.

## Command

```bash
uv run python scripts/data/audit_rag_source_service_category_coverage.py
Strict command
uv run python scripts/data/audit_rag_source_service_category_coverage.py --strict
Outputs
reports/rag/rag_source_service_category_coverage_report.json
reports/rag/rag_source_service_category_coverage_report.md
What it checks
filename is expected
source_id matches expected ownership
service_category matches expected ownership
title has basic alignment with the source topic
global/about source files are explicitly allowlisted
Safety

This is observation-only.

It does not create Elasticsearch indices, embed content, bulk index documents, move aliases, or enable production RAG.
