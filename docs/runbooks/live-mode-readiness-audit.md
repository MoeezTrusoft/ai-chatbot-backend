# Live-Mode Readiness Audit Runbook

## Purpose

This audit checks whether BookCraft is ready to be configured for live/staging mode.

It checks:

```text
LLM mode and required key presence
JWT auth readiness
metrics token safety
NDA/agreement safety modes
Tri-Match extra mode
RAG alias config
optional DB/Redis/RAG external connectivity
Safe local command
uv run python scripts/data/run_live_mode_readiness_audit.py
Require live configuration

Use this only when staging secrets are configured:

uv run python scripts/data/run_live_mode_readiness_audit.py \
  --profile staging \
  --require-live-config
External checks
uv run python scripts/data/run_live_mode_readiness_audit.py \
  --profile staging \
  --require-live-config \
  --check-externals
Outputs
reports/chatbot/live_mode_readiness_audit_report.json
reports/chatbot/live_mode_readiness_audit_report.md
Safety

This audit does not call live LLM providers, print secrets, send emails, create legal documents, create Elasticsearch indices, or move aliases.
