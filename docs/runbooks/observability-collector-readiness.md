# Observability Collector Readiness Runbook

## Purpose

This report explains and checks the local/staging OpenTelemetry collector setup.

The API exports traces to the configured OTLP endpoint. In local dev, if the collector is not running, the app may log trace export warnings. That warning is not a chatbot failure, but staging should run the collector.

## Safe command

```bash
uv run python scripts/data/run_observability_collector_readiness.py
Start local observability stack
docker compose up -d otel-collector prometheus grafana loki
Check external observability services
uv run python scripts/data/run_observability_collector_readiness.py --check-externals
Useful endpoints
OTLP gRPC: localhost:4317
OTLP HTTP: http://localhost:4318
Collector Prometheus exporter: http://localhost:8889/metrics
Prometheus: http://localhost:9090
Grafana: http://localhost:3000
Loki: http://localhost:3100
Outputs
reports/chatbot/observability_collector_readiness_report.json
reports/chatbot/observability_collector_readiness_report.md
Safety

This report does not modify collector config, start containers, call live LLMs, send emails, create legal documents, create Elasticsearch indices, or move aliases.
