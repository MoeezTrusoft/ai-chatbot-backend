# RAG Index Build Report Runbook

## Purpose

This report checks RAG index readiness without creating an Elasticsearch index.

It is observation-only.

## Command

```bash
uv run python scripts/data/build_rag_index_build_report.py
Optional external connectivity check:

uv run python scripts/data/build_rag_index_build_report.py --check-externals
Outputs
reports/rag/rag_index_build_report.json
reports/rag/rag_index_build_report.md
What it checks
source directory existence
markdown source count
estimated chunk count
required metadata/front matter
source checksums
configured RAG index alias
configured RAG index version
configured embedding dimensions
optional Elasticsearch reachability
optional TEI reachability
What it does not do
does not create Elasticsearch index
does not bulk index documents
does not call alias swap
does not change RAG runtime behavior
does not enable production RAG
Next branch
git checkout -b feat/rag-elasticsearch-indexer

Only start index creation after this report is merged.
