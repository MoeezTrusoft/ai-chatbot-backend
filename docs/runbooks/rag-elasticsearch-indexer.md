# RAG Elasticsearch Indexer Runbook

## Purpose

This tool builds a versioned Elasticsearch RAG index from tracked source markdown.

Default mode is dry-run.

## Dry run

```bash
uv run python scripts/data/build_rag_elasticsearch_index.py
Create versioned index, no alias swap

Requires Elasticsearch and TEI:

uv run python scripts/data/build_rag_elasticsearch_index.py --apply
Create index and swap alias

Use only after smoke validation policy is satisfied:

uv run python scripts/data/build_rag_elasticsearch_index.py --apply --swap-alias
Outputs
reports/rag/rag_elasticsearch_index_report.json
reports/rag/rag_elasticsearch_index_report.md
Safety
Dry-run by default.
Index creation requires --apply.
Alias movement requires --apply --swap-alias.
The tool does not enable production RAG.
The tool does not change runtime config.
Required pre-checks
uv run python scripts/data/verify_rag_source_metadata.py --strict
uv run python scripts/data/build_rag_index_build_report.py
Rollback

If alias was swapped, move bookcraft_rag_current back to the previous healthy index.
