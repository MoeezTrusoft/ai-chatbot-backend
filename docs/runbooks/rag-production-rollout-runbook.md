
RAG Production Rollout Runbook
Purpose

This runbook explains how to roll out RAG Elasticsearch safely after CI readiness has passed.

This is documentation only.

Pre-flight

Run:

git checkout main
git pull origin main

uv run python scripts/data/run_rag_readiness_checks.py
uv run python scripts/data/verify_rag_source_metadata.py --strict
uv run python scripts/data/build_rag_index_build_report.py

Expected:

valid=true
ready_for_indexing=true
files_missing_metadata_count=0
External readiness

Start or confirm:

Elasticsearch
TEI

Then run:

uv run python scripts/data/build_rag_index_build_report.py --check-externals

Expected:

Elasticsearch reachable
TEI reachable
embedding dimensions match
Candidate index build

Create versioned index without alias swap:

uv run python scripts/data/build_rag_elasticsearch_index.py --apply

Record the generated index name from:

reports/rag/rag_elasticsearch_index_report.json
Candidate smoke

Run smoke against the candidate index:

RAG_INDEX_ALIAS=<candidate_index_name> \
uv run python scripts/data/run_rag_elasticsearch_smoke_report.py \
  --check-externals \
  --require-externals

Expected:

valid=true
failed_turns=0
ghostwriting query returns ghostwriting chunk
editing query returns editing/proofreading chunk
cover design query returns cover/illustration chunk
pricing query returns no chunks
timeline query returns no chunks
Alias swap

Only after candidate smoke passes:

uv run python scripts/data/build_rag_elasticsearch_index.py --apply --swap-alias
Live smoke

After alias swap:

uv run python scripts/data/run_rag_elasticsearch_smoke_report.py \
  --check-externals \
  --require-externals
Rollback

Rollback by moving:

bookcraft_rag_current

back to the previous healthy index.

Do not delete the failed index until logs and reports are reviewed.

Stop conditions

Stop immediately if:

metadata verifier fails
readiness checks fail
Elasticsearch unreachable
TEI unreachable
embedding dimension mismatch
bulk errors appear
smoke report invalid
pricing/timeline queries return RAG chunks
alias swap fails
Safety note

This process does not enable production RAG automatically.

It only prepares and validates an Elasticsearch-backed RAG index.
