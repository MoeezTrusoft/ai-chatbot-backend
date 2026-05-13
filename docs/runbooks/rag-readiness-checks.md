# RAG Readiness Checks Runbook

## Purpose

This runbook defines the CI-safe RAG readiness checks.

The checks do not require Elasticsearch or TEI.

## Command

```bash
uv run python scripts/data/run_rag_readiness_checks.py
Outputs
reports/rag/rag_readiness_checks_report.json
reports/rag/rag_readiness_checks_report.md
Included checks
verify_rag_source_metadata.py --strict
build_rag_index_build_report.py
build_rag_elasticsearch_index.py
run_rag_elasticsearch_smoke_report.py
CI workflow
.github/workflows/rag-readiness.yml

The workflow runs on PRs touching RAG source, RAG scripts, RAG tests, RAG components, or the RAG workflow itself.

Safety

These checks are CI-safe.

They do not require Elasticsearch or TEI, do not create Elasticsearch indices, do not embed source documents, do not bulk index documents, do not move aliases, and do not enable production RAG.
