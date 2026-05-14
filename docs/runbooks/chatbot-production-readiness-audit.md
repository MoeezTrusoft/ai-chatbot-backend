# Chatbot Production Readiness Audit Runbook

## Purpose

This audit summarizes whether the BookCraft chatbot is ready for controlled staging.

It checks:

```text
required readiness/report artifacts
safety gates
auth/rate-limit configuration
LLM mode
Tri-Match mode
RAG alias configuration
optional DB/Redis/Elasticsearch/API readiness
Safe command
uv run python scripts/data/run_chatbot_production_readiness_audit.py
Staging profile
uv run python scripts/data/run_chatbot_production_readiness_audit.py --profile staging
External checks

Requires local/staging services:

uv run python scripts/data/run_chatbot_production_readiness_audit.py \
  --profile staging \
  --check-externals \
  --base-url http://localhost:8000
Outputs
reports/chatbot/production_readiness_audit_report.json
reports/chatbot/production_readiness_audit_report.md
Safety

This audit does not call live LLMs, send emails, create legal documents, create Elasticsearch indices, move aliases, or modify customer data.
