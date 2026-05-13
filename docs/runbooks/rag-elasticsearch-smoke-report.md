# RAG Elasticsearch Smoke Report Runbook

## Purpose

This report validates RAG retrieval against an existing Elasticsearch alias.

Default mode is safe and skips external calls.

## Safe local report

```bash
uv run python scripts/data/run_rag_elasticsearch_smoke_report.py
External smoke

Requires:

Elasticsearch running
TEI running
a built RAG index
bookcraft_rag_current alias pointing to that index

Run:

uv run python scripts/data/run_rag_elasticsearch_smoke_report.py --check-externals --require-externals
Outputs
reports/rag/rag_elasticsearch_smoke_report.json
reports/rag/rag_elasticsearch_smoke_report.md
What it checks with externals
ghostwriting query returns ghostwriting chunks
editing query returns editing/proofreading chunks
cover design query returns cover/illustration chunks
pricing query returns no RAG chunks
timeline query returns no RAG chunks
Safety

This report does not create Elasticsearch indices, embed source documents, bulk index content, move aliases, or enable production RAG.
