# RAG Source Front Matter Repair Plan Runbook

## Purpose

This tool creates a proposed front matter repair plan for RAG source markdown files.

It is observation-only.

## Command

```bash
uv run python scripts/data/build_rag_source_frontmatter_repair_plan.py
Outputs
reports/rag/rag_source_frontmatter_repair_plan.json
reports/rag/rag_source_frontmatter_repair_plan.md
What it does
scans source markdown
infers title/source_id/service_category/section/content_version
marks confidence level
writes JSON/Markdown repair plan
What it does not do
does not modify source markdown
does not create Elasticsearch indices
does not embed content
does not bulk index documents
does not change aliases
does not enable production RAG
Next step

Review the generated repair plan manually.

After review, apply front matter in a separate source-repair branch.
