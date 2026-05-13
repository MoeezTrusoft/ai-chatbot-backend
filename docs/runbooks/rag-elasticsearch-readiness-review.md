
RAG Elasticsearch Readiness Review Runbook
Purpose

This runbook explains how to review the current RAG/Elasticsearch readiness state.

It is documentation-only.

Current state

Implemented:

RagRetriever
BM25 retrieval
vector retrieval
Reciprocal Rank Fusion
service filter
allowed_for_response filter
RAG smoke script
RAG schemas

Still needed:

index build tool
chunk verifier
embedding batch pipeline
Elasticsearch mapping generator
bulk indexing
alias swap
rollback command
smoke report
CI readiness job
Local external smoke

Run only when Elasticsearch and TEI are both running:

uv run python scripts/data/rag_smoke.py

Expected success:

rag smoke returned N chunks

Expected failure when infra is missing:

connection refused
embedding failure
rag smoke returned no chunks
Recommended next branch
git checkout -b tooling/rag-index-build-report
Next branch scope

The next branch should add an observational report only:

scan RAG source dir
count markdown files
estimate chunks
validate expected metadata fields
report missing source docs
report whether Elasticsearch/TEI are reachable
do not create an index yet
do not change alias yet
Do not do yet

Do not implement production indexing before the report branch.

Do not swap aliases before smoke validation exists.

Do not connect RAG to final production rollout until CI readiness exists.
