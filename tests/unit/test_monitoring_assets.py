from __future__ import annotations

import json
from pathlib import Path


def test_grafana_dashboards_are_valid_json() -> None:
    dashboards = list(Path("ops/grafana/dashboards").glob("*.json"))

    assert dashboards
    for dashboard in dashboards:
        payload = json.loads(dashboard.read_text(encoding="utf-8"))
        assert payload["title"].startswith("BookCraft")
        assert payload["panels"]


def test_prometheus_loads_all_alert_files() -> None:
    prometheus = Path("ops/prometheus/prometheus.yml").read_text(encoding="utf-8")

    assert "/etc/prometheus/alerts.yml" in prometheus
    assert "/etc/prometheus/pricing-alerts.yml" in prometheus


def test_monitoring_verifier_has_required_metric_names() -> None:
    verifier = Path("scripts/ops/verify_monitoring.py").read_text(encoding="utf-8")

    assert "chatbot_turns_total" in verifier
    assert "tool_invocation_failures_total" in verifier
    assert "document_generation_failures_total" in verifier
