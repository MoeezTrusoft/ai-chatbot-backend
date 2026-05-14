from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from elasticsearch import AsyncElasticsearch
from sqlalchemy import text

from bookcraft.components.storage.db import create_engine
from bookcraft.infra.cache import create_redis_client
from bookcraft.infra.config import Settings


@dataclass(frozen=True)
class AuditCheck:
    name: str
    category: str
    status: str
    severity: str
    message: str
    details: dict[str, Any] | None = None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a staging production-readiness audit for BookCraft chatbot."
    )
    parser.add_argument("--output-dir", default="reports/chatbot")
    parser.add_argument(
        "--profile",
        choices=["local", "staging", "production"],
        default="local",
    )
    parser.add_argument(
        "--check-externals",
        action="store_true",
        help="Check DB, Redis, Elasticsearch/RAG alias, and optional HTTP readiness.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Optional running API base URL, e.g. http://localhost:8000.",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    report = asyncio.run(
        build_report(
            settings=Settings(),
            profile=args.profile,
            check_externals=args.check_externals,
            base_url=args.base_url,
        )
    )

    json_path = output_dir / "production_readiness_audit_report.json"
    md_path = output_dir / "production_readiness_audit_report.md"

    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(markdown(report), encoding="utf-8")

    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(f"json_report={json_path}")
    print(f"markdown_report={md_path}")

    return 0 if report["summary"]["valid"] else 1


async def build_report(
    *,
    settings: Settings,
    profile: str,
    check_externals: bool,
    base_url: str | None,
) -> dict[str, Any]:
    checks: list[AuditCheck] = []

    checks.extend(static_file_checks())
    checks.extend(config_safety_checks(settings=settings, profile=profile))
    checks.extend(runtime_mode_checks(settings=settings, profile=profile))

    if check_externals:
        checks.extend(await external_checks(settings=settings, base_url=base_url))
    else:
        checks.append(
            AuditCheck(
                name="external_services",
                category="externals",
                status="skipped",
                severity="info",
                message=(
                    "External checks skipped. Pass --check-externals to check DB, "
                    "Redis, Elasticsearch/RAG alias, and optional HTTP readiness."
                ),
            )
        )

    error_count = sum(1 for check in checks if check.severity == "error")
    warning_count = sum(1 for check in checks if check.severity == "warning")
    passed_count = sum(1 for check in checks if check.status == "passed")
    skipped_count = sum(1 for check in checks if check.status == "skipped")

    summary = {
        "valid": error_count == 0,
        "generated_at": datetime.now(UTC).isoformat(),
        "profile": profile,
        "check_externals": check_externals,
        "base_url": base_url,
        "check_count": len(checks),
        "passed_count": passed_count,
        "warning_count": warning_count,
        "error_count": error_count,
        "skipped_count": skipped_count,
        "production_candidate": error_count == 0,
        "controlled_staging_ready": error_count == 0,
        "blind_full_production_ready": False,
        "safety_note": (
            "Audit/report only. It does not call live LLMs, send emails, create "
            "legal documents, move Elasticsearch aliases, create indices, or modify "
            "customer data."
        ),
    }

    return {
        "schema_version": 1,
        "summary": summary,
        "environment": environment_snapshot(settings),
        "checks": [check_to_dict(check) for check in checks],
    }


def static_file_checks() -> list[AuditCheck]:
    required_files = [
        "scripts/data/run_chatbot_complex_message_diagnostics.py",
        "scripts/data/run_rag_readiness_checks.py",
        "scripts/data/run_rag_elasticsearch_smoke_report.py",
        "scripts/data/audit_rag_source_service_category_coverage.py",
        "docs/runbooks/chatbot-complex-message-diagnostics.md",
        "docs/runbooks/rag-production-rollout-runbook.md",
        "docs/runbooks/rag-external-rollout-checklist-report.md",
    ]

    checks: list[AuditCheck] = []
    for file_path in required_files:
        path = Path(file_path)
        checks.append(
            AuditCheck(
                name=f"file_exists:{file_path}",
                category="static_files",
                status="passed" if path.exists() else "failed",
                severity="info" if path.exists() else "error",
                message=(
                    f"Required readiness artifact exists: {file_path}"
                    if path.exists()
                    else f"Required readiness artifact missing: {file_path}"
                ),
            )
        )
    return checks


def config_safety_checks(*, settings: Settings, profile: str) -> list[AuditCheck]:
    checks: list[AuditCheck] = []

    checks.append(
        boolean_check(
            name="nda_not_autonomous",
            category="safety_gates",
            condition=settings.nda_mode != "autonomous",
            message_ok=f"NDA mode is safe: {settings.nda_mode}",
            message_bad="NDA mode is autonomous. Legal docs must remain gated.",
        )
    )
    checks.append(
        boolean_check(
            name="agreement_not_autonomous",
            category="safety_gates",
            condition=settings.agreement_mode != "autonomous",
            message_ok=f"Agreement mode is safe: {settings.agreement_mode}",
            message_bad="Agreement mode is autonomous. Service agreements must remain gated.",
        )
    )
    checks.append(
        boolean_check(
            name="metrics_not_public_without_token",
            category="security",
            condition=(not settings.metrics_public) or bool(settings.metrics_bearer_token),
            message_ok="Metrics endpoint is not publicly exposed without a bearer token.",
            message_bad="metrics_public=true without metrics_bearer_token.",
        )
    )
    checks.append(
        boolean_check(
            name="pricing_values_guarded",
            category="commercial_safety",
            condition=(
                (not settings.pricing_v2_values_approved) or settings.pricing_engine_version == "v2"
            ),
            message_ok="Pricing values are guarded by v2 pricing configuration.",
            message_bad="Pricing values appear enabled without v2 engine.",
        )
    )

    if profile in {"staging", "production"}:
        checks.append(
            boolean_check(
                name="api_auth_enabled_for_staging_or_production",
                category="security",
                condition=settings.api_auth_mode == "jwt" and bool(settings.jwt_signing_key),
                message_ok="JWT auth is configured.",
                message_bad="JWT auth/signing key is required for staging/production.",
            )
        )
    else:
        checks.append(
            AuditCheck(
                name="api_auth_local_mode",
                category="security",
                status="passed" if settings.api_auth_mode in {"off", "jwt"} else "failed",
                severity="info" if settings.api_auth_mode in {"off", "jwt"} else "error",
                message=f"Local audit observed api_auth_mode={settings.api_auth_mode}.",
            )
        )

    if settings.trimatch_extra_mode != "off":
        checks.append(
            AuditCheck(
                name="trimatch_extra_mode",
                category="tri_match",
                status="passed",
                severity="warning",
                message=(
                    f"trimatch_extra_mode={settings.trimatch_extra_mode}; confirm this is "
                    "intentional before staging traffic."
                ),
            )
        )
    else:
        checks.append(
            AuditCheck(
                name="trimatch_extra_mode",
                category="tri_match",
                status="passed",
                severity="info",
                message="Tri-Match extra mode is off by default.",
            )
        )

    return checks


def runtime_mode_checks(*, settings: Settings, profile: str) -> list[AuditCheck]:
    checks: list[AuditCheck] = []

    if settings.llm_provider_mode == "live":
        live_keys_present = bool(settings.anthropic_api_key and settings.openai_api_key)
        checks.append(
            boolean_check(
                name="live_llm_keys_present",
                category="llm",
                condition=live_keys_present,
                message_ok="Live LLM mode has required Anthropic and OpenAI keys configured.",
                message_bad="Live LLM mode requires Anthropic and OpenAI keys.",
            )
        )
    else:
        checks.append(
            AuditCheck(
                name="llm_provider_mode",
                category="llm",
                status="passed",
                severity="warning" if profile in {"staging", "production"} else "info",
                message=(
                    f"llm_provider_mode={settings.llm_provider_mode}. This is safe for audit, "
                    "but staging response quality should later be tested in live mode."
                ),
            )
        )

    checks.append(
        AuditCheck(
            name="rate_limit_configured",
            category="abuse_protection",
            status="passed" if settings.rate_limit_per_ip_per_minute > 0 else "failed",
            severity="info" if settings.rate_limit_per_ip_per_minute > 0 else "error",
            message=f"rate_limit_per_ip_per_minute={settings.rate_limit_per_ip_per_minute}",
        )
    )

    checks.append(
        AuditCheck(
            name="rag_alias_configured",
            category="rag",
            status="passed" if settings.rag_index_alias else "failed",
            severity="info" if settings.rag_index_alias else "error",
            message=f"rag_index_alias={settings.rag_index_alias}",
        )
    )

    return checks


async def external_checks(*, settings: Settings, base_url: str | None) -> list[AuditCheck]:
    checks: list[AuditCheck] = []

    checks.append(await check_database(settings))
    checks.append(await check_redis(settings))
    checks.append(await check_elasticsearch_alias(settings))

    if base_url:
        checks.extend(await check_http_readiness(base_url))
    else:
        checks.append(
            AuditCheck(
                name="http_readiness",
                category="http",
                status="skipped",
                severity="info",
                message="No --base-url provided, so /healthz and /readyz were not checked.",
            )
        )

    return checks


async def check_database(settings: Settings) -> AuditCheck:
    engine = create_engine(settings)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            value = result.scalar_one()
        return AuditCheck(
            name="database_connectivity",
            category="externals",
            status="passed" if value == 1 else "failed",
            severity="info" if value == 1 else "error",
            message=(
                "Database connectivity check passed."
                if value == 1
                else "Database returned unexpected result."
            ),
        )
    except Exception as exc:  # noqa: BLE001 - report all external failures.
        return AuditCheck(
            name="database_connectivity",
            category="externals",
            status="failed",
            severity="error",
            message=f"Database connectivity failed: {exc.__class__.__name__}: {exc}",
        )
    finally:
        await engine.dispose()


async def check_redis(settings: Settings) -> AuditCheck:
    client = create_redis_client(settings)
    try:
        pong = await client.ping()
        return AuditCheck(
            name="redis_connectivity",
            category="externals",
            status="passed" if pong else "failed",
            severity="info" if pong else "error",
            message="Redis connectivity check passed." if pong else "Redis ping failed.",
        )
    except Exception as exc:  # noqa: BLE001 - report all external failures.
        return AuditCheck(
            name="redis_connectivity",
            category="externals",
            status="failed",
            severity="error",
            message=f"Redis connectivity failed: {exc.__class__.__name__}: {exc}",
        )
    finally:
        await client.aclose()


async def check_elasticsearch_alias(settings: Settings) -> AuditCheck:
    client = AsyncElasticsearch(settings.elasticsearch_url)
    try:
        alias = await client.indices.get_alias(name=settings.rag_index_alias)
        count = await client.count(index=settings.rag_index_alias)
        return AuditCheck(
            name="rag_live_alias",
            category="externals",
            status="passed",
            severity="info",
            message=(
                f"RAG alias `{settings.rag_index_alias}` exists and resolves with "
                f"{count.get('count')} documents."
            ),
            details={
                "alias": settings.rag_index_alias,
                "targets": sorted(alias.keys()),
                "document_count": count.get("count"),
            },
        )
    except Exception as exc:  # noqa: BLE001 - report all external failures.
        return AuditCheck(
            name="rag_live_alias",
            category="externals",
            status="failed",
            severity="error",
            message=f"RAG alias check failed: {exc.__class__.__name__}: {exc}",
        )
    finally:
        await client.close()


async def check_http_readiness(base_url: str) -> list[AuditCheck]:
    checks: list[AuditCheck] = []
    async with httpx.AsyncClient(timeout=5.0) as client:
        for path in ("/healthz", "/readyz"):
            try:
                response = await client.get(base_url.rstrip("/") + path)
                passed = response.status_code < 500
                checks.append(
                    AuditCheck(
                        name=f"http:{path}",
                        category="http",
                        status="passed" if passed else "failed",
                        severity="info" if passed else "error",
                        message=f"{path} returned HTTP {response.status_code}.",
                        details={"status_code": response.status_code},
                    )
                )
            except Exception as exc:  # noqa: BLE001 - report all external failures.
                checks.append(
                    AuditCheck(
                        name=f"http:{path}",
                        category="http",
                        status="failed",
                        severity="error",
                        message=f"{path} check failed: {exc.__class__.__name__}: {exc}",
                    )
                )
    return checks


def boolean_check(
    *,
    name: str,
    category: str,
    condition: bool,
    message_ok: str,
    message_bad: str,
) -> AuditCheck:
    return AuditCheck(
        name=name,
        category=category,
        status="passed" if condition else "failed",
        severity="info" if condition else "error",
        message=message_ok if condition else message_bad,
    )


def check_to_dict(check: AuditCheck) -> dict[str, Any]:
    return {
        "name": check.name,
        "category": check.category,
        "status": check.status,
        "severity": check.severity,
        "message": check.message,
        "details": check.details or {},
    }


def environment_snapshot(settings: Settings) -> dict[str, Any]:
    return {
        "app_env": settings.app_env,
        "llm_provider_mode": settings.llm_provider_mode,
        "api_auth_mode": settings.api_auth_mode,
        "rate_limit_per_ip_per_minute": settings.rate_limit_per_ip_per_minute,
        "readiness_check_externals": settings.readiness_check_externals,
        "nda_mode": settings.nda_mode,
        "agreement_mode": settings.agreement_mode,
        "document_pdf_rendering_enabled": settings.document_pdf_rendering_enabled,
        "metrics_public": settings.metrics_public,
        "trimatch_mode": settings.trimatch_mode,
        "trimatch_extra_mode": settings.trimatch_extra_mode,
        "rag_index_alias": settings.rag_index_alias,
        "pricing_engine_version": settings.pricing_engine_version,
        "pricing_v2_values_approved": settings.pricing_v2_values_approved,
    }


def markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# BookCraft Chatbot Production Readiness Audit",
        "",
        "## Summary",
        "",
        f"- Generated at: `{summary['generated_at']}`",
        f"- Profile: `{summary['profile']}`",
        f"- Valid: `{summary['valid']}`",
        f"- Production candidate: `{summary['production_candidate']}`",
        f"- Controlled staging ready: `{summary['controlled_staging_ready']}`",
        f"- Blind full production ready: `{summary['blind_full_production_ready']}`",
        f"- Checks: `{summary['check_count']}`",
        f"- Passed: `{summary['passed_count']}`",
        f"- Warnings: `{summary['warning_count']}`",
        f"- Errors: `{summary['error_count']}`",
        f"- Skipped: `{summary['skipped_count']}`",
        "",
        "## Safety Note",
        "",
        summary["safety_note"],
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
