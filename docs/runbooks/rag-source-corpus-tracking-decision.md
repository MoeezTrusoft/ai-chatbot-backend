
RAG Source Corpus Tracking Decision Runbook
Decision

Use:

data/rag-corpus/source_markdown/

as the tracked, canonical RAG source corpus path.

Keep generated outputs ignored:

data/rag-corpus/build/
reports/
Why

The RAG settings and tooling already read from data/rag-corpus/source_markdown.

If this directory remains ignored, source repairs can pass locally but not be included in Git or CI.

Next command

After this decision is merged:

git checkout main
git pull origin main
git checkout -b data/rag-source-corpus-track-frontmatter

Then update .gitignore and commit the repaired markdown source files.

Required validation
uv run python scripts/data/verify_rag_source_metadata.py --strict
uv run python scripts/data/build_rag_index_build_report.py
uv run mypy src
Safety

This is a source-tracking decision only.

It does not create Elasticsearch indices, embed content, bulk index documents, change aliases, or enable production RAG.
