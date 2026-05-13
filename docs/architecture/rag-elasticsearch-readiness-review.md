# RAG Elasticsearch Readiness Review

## Status

Documentation review only.

This branch does not change runtime behavior.

## Current implementation

The current RAG retriever already supports:

```text
Elasticsearch BM25 retrieval
Elasticsearch vector retrieval
Reciprocal Rank Fusion
service_category filtering
allowed_for_response filtering
pricing/timeline RAG bypass
Prometheus metrics for retrieval
The current smoke script supports:

TEI query embedding
Elasticsearch connection
RagRetriever execution
non-empty chunk verification

The current schemas support:

RagChunkMetadata
RagChunk
RejectedChunk
RagIngestionReport
RetrievedChunk
RagRetrievalRequest
Main gap

The production gap is not basic retrieval.

The gap is the governed ingestion/index lifecycle:

source markdown
→ chunking
→ verification
→ embedding
→ Elasticsearch index creation
→ bulk indexing
→ smoke validation
→ alias swap
→ report artifact
→ rollback path
Required Elasticsearch index shape

The future index should contain these fields:

chunk_id: keyword
content: text
content_vector: dense_vector
source_id: keyword
source_type: keyword
title: text + keyword
section: keyword
service_category: keyword
subservice: keyword
audience: keyword
funnel_stage: keyword
source_filename: keyword
tags: keyword
content_version: keyword
checksum: keyword
allowed_for_response: boolean
created_at: date
updated_at: date
Required mapping requirements

The mapping must support:

BM25 match on content
filter by allowed_for_response
filter by service_category
script_score cosineSimilarity on content_vector
stable keyword lookup by chunk_id
stable source lineage by source_id/checksum
Required ingestion gates

Before any alias swap, ingestion must verify:

source files exist
source checksums are stable
accepted_count > 0
rejected_count is reported
all accepted chunks have content
all accepted chunks have checksum
all accepted chunks have metadata.title
all accepted chunks have metadata.section
all accepted chunks have allowed_for_response
all embedded vectors match EMBEDDING_DIMENSIONS
no unsafe response chunks pass verifier
Elasticsearch bulk indexing succeeded
smoke query returns at least one chunk
Required verifier gates

The ingestion verifier should reject chunks containing:

placeholder pricing
unapproved discounts
fake guarantees
unsafe legal promises
private credentials
raw API keys
internal-only notes
draft-only content
broken portfolio URLs
unsupported service claims
Required alias strategy

Use versioned physical indices:

bookcraft_rag_vYYYYMMDDHHMMSS

Use stable alias:

bookcraft_rag_current

Alias swap must be atomic:

remove alias from old index
add alias to new index
Rollback

Rollback should only require alias movement:

move bookcraft_rag_current back to previous healthy index

Rollback must not require:

application deploy
database migration
document deletion
manual chunk edits
Required smoke tests

Minimum smoke coverage:

ghostwriting query returns ghostwriting chunk
editing query returns editing/proofreading chunk
cover design query returns cover/illustration chunk
pricing query returns no RAG chunks
timeline query returns no RAG chunks
unknown service query does not crash
zero-vector query falls back to BM25/no vector
missing Elasticsearch fails clearly
missing TEI fails clearly
Required future reports

Future tooling should produce:

reports/rag/rag_index_build_report.json
reports/rag/rag_index_build_report.md
reports/rag/rag_smoke_report.json
reports/rag/rag_smoke_report.md
Recommended implementation order
docs/rag-elasticsearch-readiness-review
tooling/rag-index-build-report
feat/rag-elasticsearch-indexer
test/rag-elasticsearch-governance-smoke
ci/rag-readiness-checks
Environment variables

Relevant settings:

ELASTICSEARCH_URL=http://localhost:9200
TEI_URL=http://localhost:8080
RAG_INDEX_ALIAS=bookcraft_rag_current
RAG_INDEX_VERSION=bookcraft_rag_v1
RAG_SOURCE_DIR=data/rag-corpus/source_markdown
RAG_BUILD_DIR=data/rag-corpus/build
EMBEDDING_DIMENSIONS=384
Production recommendation

Do not enable production RAG until the indexer, build report, smoke report, alias rollback, and CI readiness checks exist.
