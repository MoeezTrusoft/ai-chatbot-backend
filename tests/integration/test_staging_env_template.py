from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = ROOT / ".env.staging.example"
RUNBOOK = ROOT / "docs" / "runbooks" / "staging-environment-bootstrap.md"


def test_staging_env_example_exists_and_has_required_keys() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")

    required_keys = [
        "APP_ENV=staging",
        "READINESS_CHECK_EXTERNALS=true",
        "DATABASE_URL=",
        "REDIS_URL=",
        "ELASTICSEARCH_URL=",
        "TEI_URL=",
        "LLM_PROVIDER_MODE=mock",
        "ANTHROPIC_API_KEY=",
        "OPENAI_API_KEY=",
        "API_AUTH_MODE=jwt",
        "JWT_SIGNING_KEY=",
        "METRICS_PUBLIC=false",
        "NDA_MODE=manual",
        "AGREEMENT_MODE=manual",
        "DOCUMENT_PDF_RENDERING_ENABLED=false",
        "TRIMATCH_EXTRA_MODE=off",
        "RAG_INDEX_ALIAS=bookcraft_rag_current",
        "OTEL_EXPORTER_OTLP_ENDPOINT=",
    ]

    for key in required_keys:
        assert key in text


def test_staging_env_example_does_not_contain_real_secret_literals() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8").lower()

    forbidden_literals = [
        "sk-",
        "xoxb-",
        "ghp_",
        "gho_",
        "postgres://bookcraft:bookcraft_dev@",
        "bookcraft_dev@localhost",
    ]

    for literal in forbidden_literals:
        assert literal not in text


def test_staging_bootstrap_runbook_references_required_audits() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")

    required_phrases = [
        "run_chatbot_production_readiness_audit.py",
        "run_live_mode_readiness_audit.py",
        "run_rag_elasticsearch_smoke_report.py",
        "audit_rag_source_service_category_coverage.py",
        "run_api_smoke_report.py",
        "run_observability_collector_readiness.py",
        "final-launch-readiness-checklist.md",
    ]

    for phrase in required_phrases:
        assert phrase in text
