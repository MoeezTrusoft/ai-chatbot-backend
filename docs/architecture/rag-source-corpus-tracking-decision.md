# RAG Source Corpus Tracking Decision

## Decision

Track curated RAG source markdown in Git under:

```text
data/rag-corpus/source_markdown/
Continue ignoring generated RAG artifacts:

data/rag-corpus/build/
reports/
Why

The application settings already use:

rag_source_dir = data/rag-corpus/source_markdown
rag_build_dir = data/rag-corpus/build

So data/rag-corpus/source_markdown is the operational RAG source path.

Keeping that path ignored caused the front matter repair PR to merge the repair tool, runbook, and tests, but not the repaired source markdown files.

For production RAG, the curated source corpus must be reproducible from Git.

Rejected option

Rejected:

Keep docs/ as canonical and generate data/rag-corpus/source_markdown from docs/

Reason:

adds a sync step
creates two source-of-truth locations
makes indexing dependent on generated local state
makes CI/staging/prod harder to reproduce
Accepted option

Accepted:

Track data/rag-corpus/source_markdown as curated source content.

Keep ignored:

data/rag-corpus/build/
reports/
Required .gitignore change

Replace the current RAG ignore rule with exceptions that allow source markdown tracking:

# RAG source corpus is curated and tracked.
!data/rag-corpus/
!data/rag-corpus/source_markdown/
!data/rag-corpus/source_markdown/**/*.md

# Generated RAG artifacts remain ignored.
data/rag-corpus/build/
reports/
Next branch
git checkout -b data/rag-source-corpus-track-frontmatter

That branch should:

update .gitignore
force-add data/rag-corpus/source_markdown markdown files if needed
run verify_rag_source_metadata.py --strict
run build_rag_index_build_report.py
commit only source corpus + .gitignore change
Safety

This decision does not index documents, embed content, create Elasticsearch indices, change aliases, or enable production RAG.
