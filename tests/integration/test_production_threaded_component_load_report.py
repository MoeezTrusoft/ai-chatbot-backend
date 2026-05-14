from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "data" / "run_production_threaded_component_load_report.py"
RUNBOOK = ROOT / "docs" / "runbooks" / "production-threaded-component-load-report.md"


def test_threaded_load_report_has_threading_features() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "build_thread_sizes" in text
    assert "min_thread_size" in text
    assert "max_thread_size" in text
    assert "thread_summary" in text
    assert "thread_sizes" in text
    assert "highest_latency_threads" in text
    assert "highest_warning_threads" in text
    assert "production_threaded_component_load_report.pdf" in text


def test_threaded_load_report_has_safe_setup_error_path() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "setup_error_report" in text
    assert "Missing --base-url or STAGING_API_BASE_URL." in text
    assert "Missing --jwt-signing-key or JWT_SIGNING_KEY." in text
    assert "Missing --customer-id or SMOKE_CUSTOMER_ID." in text
    assert "Missing --database-url or DATABASE_URL." in text
    assert "message-count must be >= min-thread-size." in text
    assert "max-thread-size must be >= min-thread-size." in text


def test_threaded_load_report_has_component_analysis_features() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "analyze_results" in text
    assert "soft_warning_score" in text
    assert "provider_health" in text
    assert "fallback_summary" in text
    assert "response_quality" in text


def test_threaded_load_report_runbook_documents_outputs() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")

    assert "production_threaded_component_load_report.json" in text
    assert "production_threaded_component_load_report.pdf" in text
    assert "--message-count 100" in text
    assert "--min-thread-size 10" in text
    assert "--max-thread-size 20" in text


def test_threaded_load_report_adds_repo_root_to_python_path() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "ROOT = Path(__file__).resolve().parents[2]" in text
    assert "sys.path.insert(0, str(ROOT))" in text
