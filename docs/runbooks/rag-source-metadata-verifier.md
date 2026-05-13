# RAG Source Metadata Verifier Runbook

## Purpose

This verifier checks source markdown front matter before any Elasticsearch indexing work.

It is observation-only by default.

## Command

```bash
uv run python scripts/data/verify_rag_source_metadata.py
Strict mode:

uv run python scripts/data/verify_rag_source_metadata.py --strict
Outputs
reports/rag/rag_source_metadata_report.json
reports/rag/rag_source_metadata_report.md
Required front matter
---
title: Ghostwriting FAQ
source_id: ghostwriting_faq
service_category: ghostwriting
section: faq
content_version: v1
allowed_for_response: true
tags: [ghostwriting, faq]
---
Valid service categories
ghostwriting
editing_proofreading
cover_design_illustration
interior_formatting
audiobook_production
publishing_distribution
marketing_promotion
author_website
video_trailer
Safety

This tool does not create Elasticsearch indices, embed documents, bulk index content, swap aliases, or enable production RAG.
