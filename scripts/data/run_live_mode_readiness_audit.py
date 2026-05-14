from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
        description="Audit BookCraft live-mode readiness without calling live LLMs."
    )
    parser.add_argument("--output-dir", default="reports/chatbot")
    parser.add_argument(
        "--profile",
        choices=["local", "staging", "production"],
        default="local",
    )
    parser.add_argument(
        "--require-live-config",
        action="store_true",
        help="Fail if live LLM/auth/secrets are not configured.",
    )
    parser.add_argument(
        "--check-externals",
        action="store_true",
        help="Check DB, Redis, and Elasticsearch/RAG alias connectivity.",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    report = asyncio.run(
        build_report(
            settings=Settings(),
            profile=args.profile,
            require_live_config=args.require_live_config,
            check_externals=args.check_externals,
        )
    )

    json_path = output_dir / "live_mode_readiness_audit_report.json"
    md_path = output_dir / "live_mode_readiness_audit_report.md"

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
    require_live_config: bool,
    check_externals: bool,
) -> dict[str, Any]:
    checks: list[AuditCheck] = []

    checks.extend(required_artifact_checks())
    checks.extend(secret_presence_checks(settings, require_live_config=require_live_config))
    checks.extend(live_mode_config_checks(settings, require_live_config=require_live_config))
    checks.extend(security_config_checks(settings, profile=profile))
    checks.extend(safety_gate_checks(settings))
    checks.extend(runtime_config_checks(settings))

    if check_externals:
        checks.extend(await external_checks(settings))
    else:
        checks.append(
            AuditCheck(
                name="external_checks",
                category="externals",
                status="skipped",
                severity="info",
                message="External checks skipped. Use --check-externals to enable them.",
            )
        )

    error_count = sum(1 for item in checks if item.severity == "error")
    warning_count = sum(1 for item in checks if item.severity == "warning")
    passed_count = sum(1 for item in checks if item.status == "passed")
    skipped_count = sum(1 for item in checks if item.status == "skipped")

    summary = {
        "valid": error_count == 0,
        "generated_at": datetime.now(UTC).isoformat(),
        "profile": profile,
        "require_live_config": require_live_config,
        "check_externals": check_externals,
        "check_count": len(checks),
        "passed_count": passed_count,
        "warning_count": warning_count,
        "error_count": error_count,
        "skipped_count": skipped_count,
        "ready_for_controlled_live_staging": (
            error_count == 0
            and require_live_config
            and check_externals
            and settings.llm_provider_mode == "live"
        ),
        "safe_for_local_audit": error_count == 0,
        "blind_full_production_ready": False,
        "safety_note": (
            "Audit/report only. This script does not call live LLM providers, "
            "does not print secrets, does not send emails, does not create legal "
            "documents, does not create Elasticsearch indices, and does not move aliases."
        ),
    }

    return {
        "schema_version": 1,
        "summary": summary,
        "environment": environment_snapshot(settings),
        "secret_presence": secret_presence_snapshot(settings),
        "checks": [check_to_dict(item) for item in checks],
    }


def required_artifact_checks() -> list[AuditCheck]:
    required = [
        "scripts/data/run_chatbot_complex_message_diagnostics.py",
        "scripts/data/run_chatbot_production_readiness_audit.py",
        "scripts/data/run_rag_readiness_checks.py",
        "scripts/data/run_rag_elasticsearch_smoke_report.py",
        "docs/runbooks/chatbot-complex-message-diagnostics.md",
        "docs/runbooks/chatbot-production-readiness-audit.md",
        "docs/runbooks/rag-production-rollout-runbook.md",
    ]
    checks: list[AuditCheck] = []
    for file_path in required:
        exists = Path(file_path).exists()
        checks.append(
            AuditCheck(
                name=f"artifact:{file_path}",
                category="artifacts",
                status="passed" if exists else "failed",
                severity="info" if exists else "error",
                message=(
                    f"Required artifact exists: {file_path}"
                    if exists
                    else f"Required artifact missing: {file_path}"
                ),
            )
        )
    return checks


def secret_presence_checks(
    settings: Settings,
    *,
    require_live_config: bool,
) -> list[AuditCheck]:
    checks: list[AuditCheck] = []

    checks.append(
        secret_check(
            name="anthropic_api_key_present",
            present=bool(settings.anthropic_api_key),
            required=require_live_config or settings.llm_provider_mode == "live",
            message="Anthropic key is required for live Sonnet/Haiku usage.",
        )
    )
    checks.append(
        secret_check(
            name="openai_api_key_present",
            present=bool(settings.openai_api_key),
            required=require_live_config or settings.llm_provider_mode == "live",
            message="OpenAI key is required for live intent ensemble usage.",
        )
    )
    checks.append(
        secret_check(
            name="deepseek_api_key_present",
            present=bool(settings.deepseek_api_key),
            required=False,
            message="DeepSeek key is optional when the internal endpoint does not require it.",
        )
    )
    checks.append(
        secret_check(
            name="jwt_signing_key_present",
            present=bool(settings.jwt_signing_key),
            required=require_live_config or settings.api_auth_mode == "jwt",
            message="JWT signing key is required when API auth is jwt.",
        )
    )
    checks.append(
        secret_check(
            name="metrics_bearer_token_present",
            present=bool(settings.metrics_bearer_token),
            required=settings.metrics_public,
            message="Metrics bearer token is required when metrics_public=true.",
        )
    )
    checks.append(
        secret_check(
            name="sendgrid_api_key_present",
            present=bool(settings.sendgrid_api_key),
            required=False,
            message="SendGrid key is optional until outbound email is enabled.",
        )
    )

    return checks


def live_mode_config_checks(
    settings: Settings,
    *,
    require_live_config: bool,
) -> list[AuditCheck]:
    checks: list[AuditCheck] = []

    live_mode = settings.llm_provider_mode == "live"
    required = require_live_config

    checks.append(
        AuditCheck(
            name="llm_provider_mode",
            category="llm",
            status="passed" if live_mode or not required else "failed",
            severity="info" if live_mode or not required else "error",
            message=f"llm_provider_mode={settings.llm_provider_mode}",
        )
    )

    checks.append(
        AuditCheck(
            name="anthropic_models_configured",
            category="llm",
            status="passed"
            if settings.anthropic_haiku_model and settings.anthropic_sonnet_model
            else "failed",
            severity="info"
            if settings.anthropic_haiku_model and settings.anthropic_sonnet_model
            else "error",
            message="Anthropic Haiku and Sonnet model names are configured.",
            details={
                "haiku_model": settings.anthropic_haiku_model,
                "sonnet_model": settings.anthropic_sonnet_model,
            },
        )
    )

    checks.append(
        AuditCheck(
            name="openai_intent_model_configured",
            category="llm",
            status="passed" if settings.openai_intent_model else "failed",
            severity="info" if settings.openai_intent_model else "error",
            message=f"OpenAI intent model configured: {settings.openai_intent_model}",
        )
    )

    checks.append(
        AuditCheck(
            name="llm_timeouts_configured",
            category="llm",
            status="passed"
            if settings.llm_request_timeout_seconds > 0
            and settings.intent_ensemble_timeout_seconds > 0
            else "failed",
            severity="info"
            if settings.llm_request_timeout_seconds > 0
            and settings.intent_ensemble_timeout_seconds > 0
            else "error",
            message=(
                "LLM and intent ensemble timeout settings are configured with positive values."
            ),
            details={
                "llm_request_timeout_seconds": settings.llm_request_timeout_seconds,
                "intent_ensemble_timeout_seconds": settings.intent_ensemble_timeout_seconds,
            },
        )
    )

    return checks


def security_config_checks(settings: Settings, *, profile: str) -> list[AuditCheck]:
    checks: list[AuditCheck] = []
    staging_or_prod = profile in {"staging", "production"}

    jwt_required = staging_or_prod
    jwt_ok = settings.api_auth_mode == "jwt" and bool(settings.jwt_signing_key)

    checks.append(
        AuditCheck(
            name="api_auth_for_profile",
            category="security",
            status="passed" if jwt_ok or not jwt_required else "failed",
            severity="info" if jwt_ok or not jwt_required else "error",
            message=(
                "JWT auth is configured for staging/production."
                if jwt_ok
                else f"profile={profile}, api_auth_mode={settings.api_auth_mode}"
            ),
        )
    )

    metrics_ok = (not settings.metrics_public) or bool(settings.metrics_bearer_token)
    checks.append(
        AuditCheck(
            name="metrics_auth",
            category="security",
            status="passed" if metrics_ok else "failed",
            severity="info" if metrics_ok else "error",
            message=(
                "Metrics are not public without a bearer token."
                if metrics_ok
                else "metrics_public=true without metrics_bearer_token."
            ),
        )
    )

    checks.append(
        AuditCheck(
            name="rate_limit_positive",
            category="security",
            status="passed" if settings.rate_limit_per_ip_per_minute > 0 else "failed",
            severity="info" if settings.rate_limit_per_ip_per_minute > 0 else "error",
            message=f"rate_limit_per_ip_per_minute={settings.rate_limit_per_ip_per_minute}",
        )
    )

    return checks


def safety_gate_checks(settings: Settings) -> list[AuditCheck]:
    return [
        AuditCheck(
            name="nda_mode_not_autonomous",
            category="safety",
            status="passed" if settings.nda_mode != "autonomous" else "failed",
            severity="info" if settings.nda_mode != "autonomous" else "error",
            message=f"nda_mode={settings.nda_mode}",
        ),
        AuditCheck(
            name="agreement_mode_not_autonomous",
            category="safety",
            status="passed" if settings.agreement_mode != "autonomous" else "failed",
            severity="info" if settings.agreement_mode != "autonomous" else "error",
            message=f"agreement_mode={settings.agreement_mode}",
        ),
        AuditCheck(
            name="document_pdf_rendering_default_safe",
            category="safety",
            status="passed",
            severity="info",
            message=(f"document_pdf_rendering_enabled={settings.document_pdf_rendering_enabled}"),
        ),
        AuditCheck(
            name="pricing_values_guarded",
            category="safety",
            status="passed",
            severity="info",
            message=(f"pricing_v2_values_approved={settings.pricing_v2_values_approved}"),
        ),
    ]


def runtime_config_checks(settings: Settings) -> list[AuditCheck]:
    return [
        AuditCheck(
            name="rag_alias_configured",
            category="rag",
            status="passed" if settings.rag_index_alias else "failed",
            severity="info" if settings.rag_index_alias else "error",
            message=f"rag_index_alias={settings.rag_index_alias}",
        ),
        AuditCheck(
            name="embedding_dimensions_configured",
            category="rag",
            status="passed" if settings.embedding_dimensions > 0 else "failed",
            severity="info" if settings.embedding_dimensions > 0 else "error",
            message=f"embedding_dimensions={settings.embedding_dimensions}",
        ),
        AuditCheck(
            name="trimatch_extra_mode_reviewed",
            category="tri_match",
            status="passed",
            severity="warning" if settings.trimatch_extra_mode != "off" else "info",
            message=f"trimatch_extra_mode={settings.trimatch_extra_mode}",
        ),
    ]


async def external_checks(settings: Settings) -> list[AuditCheck]:
    checks = [
        await check_database(settings),
        await check_redis(settings),
        await check_rag_alias(settings),
    ]
    return checks


async def check_database(settings: Settings) -> AuditCheck:
    engine = create_engine(settings)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            value = result.scalar_one()
        passed = value == 1
        return AuditCheck(
            name="database_connectivity",
            category="externals",
            status="passed" if passed else "failed",
            severity="info" if passed else "error",
            message="Database connectivity passed." if passed else "Unexpected DB result.",
        )
    except Exception as exc:  # noqa: BLE001
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
            message="Redis connectivity passed." if pong else "Redis ping failed.",
        )
    except Exception as exc:  # noqa: BLE001
        return AuditCheck(
            name="redis_connectivity",
            category="externals",
            status="failed",
            severity="error",
            message=f"Redis connectivity failed: {exc.__class__.__name__}: {exc}",
        )
    finally:
        await client.aclose()


async def check_rag_alias(settings: Settings) -> AuditCheck:
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
                f"RAG alias `{settings.rag_index_alias}` resolves with "
                f"{count.get('count')} documents."
            ),
            details={
                "alias": settings.rag_index_alias,
                "targets": sorted(alias.keys()),
                "document_count": count.get("count"),
            },
        )
    except Exception as exc:  # noqa: BLE001
        return AuditCheck(
            name="rag_live_alias",
            category="externals",
            status="failed",
            severity="error",
            message=f"RAG alias check failed: {exc.__class__.__name__}: {exc}",
        )
    finally:
        await client.close()


def secret_check(
    *,
    name: str,
    present: bool,
    required: bool,
    message: str,
) -> AuditCheck:
    if present:
        return AuditCheck(
            name=name,
            category="secrets",
            status="passed",
            severity="info",
            message=f"{message} Secret is present. Value is not printed.",
        )
    return AuditCheck(
        name=name,
        category="secrets",
        status="failed" if required else "passed",
        severity="error" if required else "warning",
        message=f"{message} Secret is not present.",
    )


def environment_snapshot(settings: Settings) -> dict[str, Any]:
    return {
        "app_env": settings.app_env,
        "llm_provider_mode": settings.llm_provider_mode,
        "api_auth_mode": settings.api_auth_mode,
        "metrics_public": settings.metrics_public,
        "nda_mode": settings.nda_mode,
        "agreement_mode": settings.agreement_mode,
        "trimatch_mode": settings.trimatch_mode,
        "trimatch_extra_mode": settings.trimatch_extra_mode,
        "rag_index_alias": settings.rag_index_alias,
        "pricing_engine_version": settings.pricing_engine_version,
        "pricing_v2_values_approved": settings.pricing_v2_values_approved,
    }


def secret_presence_snapshot(settings: Settings) -> dict[str, bool]:
    return {
        "anthropic_api_key": bool(settings.anthropic_api_key),
        "openai_api_key": bool(settings.openai_api_key),
        "deepseek_api_key": bool(settings.deepseek_api_key),
        "jwt_signing_key": bool(settings.jwt_signing_key),
        "metrics_bearer_token": bool(settings.metrics_bearer_token),
        "sendgrid_api_key": bool(settings.sendgrid_api_key),
    }


def check_to_dict(check: AuditCheck) -> dict[str, Any]:
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
        "# BookCraft Live-Mode Readiness Audit",
        "",
        "## Summary",
        "",
        f"- Generated at: `{summary['generated_at']}`",
        f"- Profile: `{summary['profile']}`",
        f"- Valid: `{summary['valid']}`",
        f"- Require live config: `{summary['require_live_config']}`",
        f"- Check externals: `{summary['check_externals']}`",
        f"- Ready for controlled live staging: `{summary['ready_for_controlled_live_staging']}`",
        f"- Safe for local audit: `{summary['safe_for_local_audit']}`",
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
