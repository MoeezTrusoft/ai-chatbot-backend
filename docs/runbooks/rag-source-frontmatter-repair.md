# RAG Source Front Matter Repair Runbook

## Purpose

This runbook applies reviewed front matter repairs to RAG source markdown files.

Unlike the repair plan, this branch modifies source markdown.

## Dry run

```bash
uv run python scripts/data/apply_rag_source_frontmatter_repairs.py
Apply
uv run python scripts/data/apply_rag_source_frontmatter_repairs.py --apply
Validate after apply
uv run python scripts/data/verify_rag_source_metadata.py --strict
uv run python scripts/data/build_rag_index_build_report.py
Safety

This tool only edits RAG source markdown front matter.

It does not create Elasticsearch indices, embed content, bulk index documents, change aliases, or enable production RAG.
