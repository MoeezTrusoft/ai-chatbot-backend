# Chatbot Complex Message Diagnostics Runbook

## Purpose

Runs 50 complex user messages through the BookCraft chatbot pipeline and writes a component-level report.

It focuses on:

```text
language guard
preprocessor
Tri-Match
intent classifier
extraction/state update
RAG retrieval when enabled
pricing/portfolio/document-safe response routing
TRG
assistant response source
Safe command
uv run python scripts/data/run_chatbot_complex_message_diagnostics.py
With local RAG

Requires Elasticsearch, TEI, and a live bookcraft_rag_current alias:

uv run python scripts/data/run_chatbot_complex_message_diagnostics.py --check-rag
Outputs
reports/chatbot/complex_message_diagnostic_report.json
reports/chatbot/complex_message_diagnostic_report.md
Safety

This diagnostic uses mock response generation by default.

It does not create Elasticsearch indices, move aliases, send emails, create legal documents, or contact real customers.
