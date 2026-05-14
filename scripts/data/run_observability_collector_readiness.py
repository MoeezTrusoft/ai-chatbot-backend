from __future__ import annotations

import argparse
import json
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from bookcraft.infra.config import Settings


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    category: str
    status: str
    severity: str
    message: str
    details: dict[str, Any] | None = None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check local/staging observability collector readiness."
    )
    parser.add_argument("--output-dir", default="reports/chatbot")
    parser.add_argument(
        "--check-externals",
        action="store_true",
        help="Check local collector/Prometheus/Grafana/Loki endpoints.",
    )
    parser.add_argument("--otel-grpc-url", default=None)
    parser.add_argument("--otel-http-url", default="http://localhost:4318")
    parser.add_argument("--otel-prom-url", default="http://localhost:8889/metrics")
    parser.add_argument("--prometheus-url", default="http://localhost:9090/-/ready")
    parser.add_argument("--grafana-url", default="http://localhost:3000/api/health")
    parser.add_argument("--loki-url", default="http://localhost:3100/ready")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings()
    report = build_report(
        settings=settings,
        check_externals=args.check_externals,
        otel_grpc_url=args.otel_grpc_url or settings.otel_exporter_otlp_endpoint,
        otel_http_url=args.otel_http_url,
        otel_prom_url=args.otel_prom_url,
        prometheus_url=args.prometheus_url,
        grafana_url=args.grafana_url,
        loki_url=args.loki_url,
    )

    json_path = output_dir / "observability_collector_readiness_report.json"
    md_path = output_dir / "observability_collector_readiness_report.md"

    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(markdown(report), encoding="utf-8")

    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(f"json_report={json_path}")
    print(f"markdown_report={md_path}")

    return 0 if report["summary"]["valid"] else 1


def build_report(
    *,
    settings: Settings,
    check_externals: bool,
    otel_grpc_url: str,
    otel_http_url: str,
    otel_prom_url: str,
    prometheus_url: str,
    grafana_url: str,
    loki_url: str,
) -> dict[str, Any]:
    checks: list[ReadinessCheck] = []

    checks.extend(static_artifact_checks())
    checks.extend(config_checks(settings=settings, otel_grpc_url=otel_grpc_url))

    if check_externals:
        checks.extend(
            external_checks(
                otel_grpc_url=otel_grpc_url,
                otel_http_url=otel_http_url,
                otel_prom_url=otel_prom_url,
                prometheus_url=prometheus_url,
                grafana_url=grafana_url,
                loki_url=loki_url,
            )
        )
    else:
        checks.append(
            ReadinessCheck(
                name="external_observability_checks",
                category="externals",
                status="skipped",
                severity="info",
                message=(
                    "External observability checks skipped. Use --check-externals "
                    "after starting otel-collector/prometheus/grafana/loki."
                ),
            )
        )

    error_count = sum(1 for item in checks if item.severity == "error")
    warning_count = sum(1 for item in checks if item.severity == "warning")
    passed_count = sum(1 for item in checks if item.status == "passed")
    skipped_count = sum(1 for item in checks if item.status == "skipped")

    return {
        "schema_version": 1,
        "summary": {
            "valid": error_count == 0,
            "generated_at": datetime.now(UTC).isoformat(),
            "check_externals": check_externals,
            "check_count": len(checks),
            "passed_count": passed_count,
            "warning_count": warning_count,
            "error_count": error_count,
            "skipped_count": skipped_count,
            "collector_required_for_local_dev": False,
            "collector_required_for_staging": True,
            "safety_note": (
                "Observation-only report. It does not modify collector config, "
                "start containers, call live LLMs, send emails, create legal "
                "documents, create Elasticsearch indices, or move aliases."
            ),
        },
        "environment": {
            "otel_exporter_otlp_endpoint": settings.otel_exporter_otlp_endpoint,
            "log_format": settings.log_format,
            "sentry_configured": bool(settings.sentry_dsn),
            "metrics_public": settings.metrics_public,
        },
        "endpoints": {
            "otel_grpc_url": otel_grpc_url,
            "otel_http_url": otel_http_url,
            "otel_prom_url": otel_prom_url,
            "prometheus_url": prometheus_url,
            "grafana_url": grafana_url,
            "loki_url": loki_url,
        },
        "checks": [check_to_dict(item) for item in checks],
        "recommended_local_start_command": (
            "docker compose up -d otel-collector prometheus grafana loki"
        ),
    }


def static_artifact_checks() -> list[ReadinessCheck]:
    required_files = [
        "ops/otel/otel-collector-config.yaml",
        "ops/prometheus/prometheus.yml",
        "ops/grafana/provisioning",
        "ops/loki/local-config.yaml",
        "docker-compose.yml",
        "scripts/data/run_api_smoke_report.py",
        "docs/runbooks/api-smoke-runbook.md",
    ]

    checks: list[ReadinessCheck] = []
    for file_path in required_files:
        path = Path(file_path)
        exists = path.exists()
        checks.append(
            ReadinessCheck(
                name=f"artifact:{file_path}",
                category="artifacts",
                status="passed" if exists else "failed",
                severity="info" if exists else "error",
                message=(
                    f"Required observability artifact exists: {file_path}"
                    if exists
                    else f"Required observability artifact missing: {file_path}"
                ),
            )
        )
    return checks


def config_checks(*, settings: Settings, otel_grpc_url: str) -> list[ReadinessCheck]:
    parsed = urlparse(otel_grpc_url)
    has_endpoint = bool(parsed.hostname and parsed.port)

    return [
        ReadinessCheck(
            name="otel_endpoint_configured",
            category="config",
            status="passed" if has_endpoint else "failed",
            severity="info" if has_endpoint else "error",
            message=f"OTLP endpoint configured as {otel_grpc_url}.",
            details={
                "hostname": parsed.hostname,
                "port": parsed.port,
                "scheme": parsed.scheme,
            },
        ),
        ReadinessCheck(
            name="sentry_optional",
            category="config",
            status="passed",
            severity="info" if settings.sentry_dsn else "warning",
            message=(
                "Sentry DSN is configured."
                if settings.sentry_dsn
                else "Sentry DSN is not configured; acceptable for local dev."
            ),
        ),
        ReadinessCheck(
            name="metrics_public_safety",
            category="config",
            status="passed"
            if (not settings.metrics_public) or bool(settings.metrics_bearer_token)
            else "failed",
            severity="info"
            if (not settings.metrics_public) or bool(settings.metrics_bearer_token)
            else "error",
            message=(
                "Metrics are not public without a token."
                if (not settings.metrics_public) or bool(settings.metrics_bearer_token)
                else "metrics_public=true without metrics_bearer_token."
            ),
        ),
    ]


def external_checks(
    *,
    otel_grpc_url: str,
    otel_http_url: str,
    otel_prom_url: str,
    prometheus_url: str,
    grafana_url: str,
    loki_url: str,
) -> list[ReadinessCheck]:
    checks = [
        tcp_check("otel_grpc_tcp", "collector", otel_grpc_url),
        http_check("otel_http_receiver", "collector", otel_http_url),
        http_check("otel_prometheus_exporter", "collector", otel_prom_url),
        http_check("prometheus_ready", "prometheus", prometheus_url),
        http_check("grafana_health", "grafana", grafana_url),
        http_check("loki_ready", "loki", loki_url),
    ]
    return checks


def tcp_check(name: str, category: str, endpoint: str) -> ReadinessCheck:
    parsed = urlparse(endpoint)
    host = parsed.hostname
    port = parsed.port

    if not host or not port:
        return ReadinessCheck(
            name=name,
            category=category,
            status="failed",
            severity="error",
            message=f"Invalid TCP endpoint: {endpoint}",
        )

    try:
        with socket.create_connection((host, port), timeout=3.0):
            return ReadinessCheck(
                name=name,
                category=category,
                status="passed",
                severity="info",
                message=f"TCP connection to {host}:{port} succeeded.",
                details={"host": host, "port": port},
            )
    except OSError as exc:
        return ReadinessCheck(
            name=name,
            category=category,
            status="failed",
            severity="error",
            message=f"TCP connection to {host}:{port} failed: {exc}",
            details={"host": host, "port": port},
        )


def http_check(name: str, category: str, url: str) -> ReadinessCheck:
    try:
        response = httpx.get(url, timeout=5.0)
        passed = response.status_code < 500
        severity = "info" if passed else "error"
        return ReadinessCheck(
            name=name,
            category=category,
            status="passed" if passed else "failed",
            severity=severity,
            message=f"GET {url} returned HTTP {response.status_code}.",
            details={
                "url": url,
                "status_code": response.status_code,
                "content_type": response.headers.get("content-type"),
            },
        )
    except Exception as exc:  # noqa: BLE001
        return ReadinessCheck(
            name=name,
            category=category,
            status="failed",
            severity="error",
            message=f"GET {url} failed: {exc.__class__.__name__}: {exc}",
            details={"url": url},
        )


def check_to_dict(check: ReadinessCheck) -> dict[str, Any]:
    return {
        "name": check.name,
        "category": check.category,
        "status": check.status,
        "severity": check.severity,
        "message": check.message,
        "details": check.details or {},
    }


def markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Observability Collector Readiness Report",
        "",
        "## Summary",
        "",
        f"- Generated at: `{summary['generated_at']}`",
        f"- Valid: `{summary['valid']}`",
        f"- Check externals: `{summary['check_externals']}`",
        f"- Checks: `{summary['check_count']}`",
        f"- Passed: `{summary['passed_count']}`",
        f"- Warnings: `{summary['warning_count']}`",
        f"- Errors: `{summary['error_count']}`",
        f"- Skipped: `{summary['skipped_count']}`",
        f"- Collector required for local dev: `{summary['collector_required_for_local_dev']}`",
        f"- Collector required for staging: `{summary['collector_required_for_staging']}`",
        "",
        "## Safety Note",
        "",
        summary["safety_note"],
        "",
        "## Recommended Local Start Command",
        "",
        f"```bash\n{report['recommended_local_start_command']}\n```",
        "",
        "## Checks",
        "",
        "| Severity | Status | Category | Name | Message |",
        "|---|---|---|---|---|",
    ]

    for check in report["checks"]:
        lines.append(
            "| `{severity}` | `{status}` | `{category}` | `{name}` | {message} |".format(
                severity=check["severity"],
                status=check["status"],
                category=check["category"],
                name=check["name"],
                message=check["message"].replace("|", "\\|"),
            )
        )

    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
