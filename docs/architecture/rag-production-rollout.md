# RAG Production Rollout

## Status

Documentation only.

This document defines the safe external rollout sequence for RAG Elasticsearch.

## Current readiness

Completed:

```text
tracked RAG source markdown
source metadata verifier
front matter repair tooling
dry-run indexer
Elasticsearch smoke report
CI-safe readiness checks
Production rollout principle

Never swap the live RAG alias before:

metadata verifier passes
dry-run readiness passes
versioned index is created successfully
external smoke passes against the candidate index/alias
rollback target is known
Required external services
Elasticsearch
TEI embedding service

Expected defaults:

ELASTICSEARCH_URL=http://localhost:9200
TEI_URL=http://localhost:8080
RAG_INDEX_ALIAS=bookcraft_rag_current
RAG_INDEX_VERSION=bookcraft_rag_v1
EMBEDDING_DIMENSIONS=384
Rollout sequence
1. Confirm source readiness
uv run python scripts/data/verify_rag_source_metadata.py --strict
uv run python scripts/data/build_rag_index_build_report.py
uv run python scripts/data/run_rag_readiness_checks.py
2. Confirm external connectivity
uv run python scripts/data/build_rag_index_build_report.py --check-externals
3. Build a versioned candidate index without alias swap
uv run python scripts/data/build_rag_elasticsearch_index.py --apply

This creates a versioned index but does not move bookcraft_rag_current.

4. Smoke test retrieval

If the alias has not yet moved, point smoke tooling at the candidate index only if needed through environment override.

Preferred safe path:

RAG_INDEX_ALIAS=<candidate_index_name> \
uv run python scripts/data/run_rag_elasticsearch_smoke_report.py \
  --check-externals \
  --require-externals
5. Swap alias only after smoke passes
uv run python scripts/data/build_rag_elasticsearch_index.py --apply --swap-alias
6. Smoke test live alias
uv run python scripts/data/run_rag_elasticsearch_smoke_report.py \
  --check-externals \
  --require-externals
Stop conditions

Stop rollout if any of these occur:

metadata verifier fails
TEI is unreachable
Elasticsearch is unreachable
embedding dimension mismatch
bulk indexing errors
smoke report invalid
pricing query returns RAG chunks
timeline query returns RAG chunks
expected service chunks missing
alias swap fails
Rollback

Rollback should be alias-only.

Move bookcraft_rag_current back to the previous healthy index.

Do not delete the failed candidate index until after investigation.

What this rollout does not do
does not enable production RAG automatically
does not change chat runtime config
does not bypass deterministic pricing
does not bypass portfolio registry
does not bypass document generation policy
Required reports to archive
reports/rag/rag_source_metadata_report.json
reports/rag/rag_index_build_report.json
reports/rag/rag_elasticsearch_index_report.json
reports/rag/rag_elasticsearch_smoke_report.json
reports/rag/rag_readiness_checks_report.json

