from __future__ import annotations

import json
import re
from pathlib import Path

REQUIRED_DASHBOARDS = {
    "BookCraft System Health",
    "BookCraft Chat Funnel",
    "BookCraft LLM Providers and Cost",
    "BookCraft RAG Pricing Portfolio Documents",
    "BookCraft Tri-Match Funnel Tool Dispatcher",
    "BookCraft Database Redis",
}

REQUIRED_ALERTS = {
    "BookCraftApiTargetDown",
    "BookCraftHighHttpErrorRate",
    "BookCraftChatLatencySloBreach",
    "BookCraftLlmProviderFailures",
    "BookCraftElasticsearchFailures",
    "BookCraftTeiFailures",
    "PricingEngineHighErrorRate",
    "BookCraftDocumentGenerationFailures",
    "BookCraftVerifierFailures",
    "BookCraftToolFailures",
    "BookCraftCostSpike",
    "BookCraftNoTrafficAnomaly",
}

REQUIRED_METRICS = {
    "chatbot_turns_total",
    "chatbot_turn_latency_seconds",
    "llm_calls_total",
    "llm_call_latency_seconds",
    "llm_call_cost_usd_total",
    "rag_queries_total",
    "rag_query_latency_seconds",
    "pricing_quotes_total",
    "pricing_quote_failures_total",
    "portfolio_requests_total",
    "document_generation_total",
    "document_generation_failures_total",
    "trimatch_votes_total",
    "trimatch_precision",
    "trimatch_recall",
    "funnel_signal_votes_total",
    "tool_invocations_total",
    "tool_invocation_failures_total",
    "db_pool_checked_out",
    "redis_cache_hits_total",
}


def main() -> int:
    errors: list[str] = []
    dashboard_titles = _dashboard_titles(Path("ops/grafana/dashboards"), errors)
    missing_dashboards = sorted(REQUIRED_DASHBOARDS - dashboard_titles)
    errors.extend(f"missing dashboard: {title}" for title in missing_dashboards)

    alert_text = "\n".join(
        path.read_text(encoding="utf-8") for path in Path("ops/prometheus").glob("*alerts.yml")
    )
    alerts = set(re.findall(r"alert:\s*([A-Za-z0-9_]+)", alert_text))
    missing_alerts = sorted(REQUIRED_ALERTS - alerts)
    errors.extend(f"missing alert: {alert}" for alert in missing_alerts)

    prometheus = Path("ops/prometheus/prometheus.yml").read_text(encoding="utf-8")
    if "pricing-alerts.yml" not in prometheus:
        errors.append("prometheus.yml does not load pricing-alerts.yml")

    source_text = "\n".join(path.read_text(encoding="utf-8") for path in Path("src").rglob("*.py"))
    dashboard_text = "\n".join(
        path.read_text(encoding="utf-8") for path in Path("ops/grafana/dashboards").glob("*.json")
    )
    available_text = source_text + "\n" + dashboard_text + "\n" + alert_text
    missing_metrics = sorted(metric for metric in REQUIRED_METRICS if metric not in available_text)
    errors.extend(f"missing monitoring metric reference: {metric}" for metric in missing_metrics)

    print(json.dumps({"valid": not errors, "errors": errors}, indent=2, sort_keys=True))
    if errors:
        return 1
    print("monitoring verifier passed")
    return 0


def _dashboard_titles(root: Path, errors: list[str]) -> set[str]:
    titles: set[str] = set()
    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{path}: invalid JSON: {exc}")
            continue
        title = payload.get("title")
        if not isinstance(title, str):
            errors.append(f"{path}: missing dashboard title")
            continue
        if not payload.get("panels"):
            errors.append(f"{path}: dashboard has no panels")
        titles.add(title)
    return titles


if __name__ == "__main__":
    raise SystemExit(main())
