from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_shortcut_application_audit_has_safe_application_only() -> None:
    result = subprocess.run(  # noqa: S603 - fixed repo script under test.
        [
            sys.executable,
            "scripts/data/run_trimatch_shortcut_audit_report.py",
            "--output-dir",
            str(ROOT / "reports" / "trimatch"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr

    report = json.loads(
        (ROOT / "reports" / "trimatch" / "trimatch_shortcut_audit_report.json").read_text(
            encoding="utf-8"
        )
    )
    summary = report["summary"]

    assert summary["valid"] is True
    assert summary["failed_turns"] == 0
    assert summary["eligible_count"] >= 1
    assert summary["applied_count"] >= 1
    assert summary["side_effects_allowed_count"] == 0
    assert summary["sensitive_block_count"] == 3
    assert summary["pricing_sensitive_count"] == 1
    assert summary["document_sensitive_count"] == 1
    assert summary["portfolio_sensitive_count"] == 1

    assert summary["applied_dimension_counts"] == {"service_primary": 1}
    assert summary["applied_value_counts"] == {"editing_proofreading": 1}


def test_shortcut_application_keeps_sensitive_cases_blocked() -> None:
    report_path = ROOT / "reports" / "trimatch" / "trimatch_shortcut_audit_report.json"

    if not report_path.exists():
        subprocess.run(  # noqa: S603 - fixed repo script under test.
            [
                sys.executable,
                "scripts/data/run_trimatch_shortcut_audit_report.py",
                "--output-dir",
                str(ROOT / "reports" / "trimatch"),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    sensitive_cases = {
        "pricing_shortcut_blocked",
        "agreement_shortcut_blocked",
        "portfolio_shortcut_blocked",
    }

    seen_sensitive_cases = set()
    for turn in report["turns"]:
        if turn["name"] not in sensitive_cases:
            continue

        seen_sensitive_cases.add(turn["name"])
        actual = turn["actual"]
        assert actual["shortcut_applied"] is False
        assert actual["side_effects_allowed"] is False

    assert seen_sensitive_cases == sensitive_cases


def test_shortcut_application_does_not_bypass_critical_systems() -> None:
    chat_service = (ROOT / "src" / "bookcraft" / "services" / "chat.py").read_text(encoding="utf-8")

    shortcut_start = chat_service.index("def _apply_shortcut_to_intent(")
    shortcut_end = chat_service.index("def _primary_shortcut_evidence(")
    shortcut_function = chat_service[shortcut_start:shortcut_end]

    forbidden_direct_calls = [
        "pricing_engine",
        "portfolio_engine",
        "document",
        "rag_retriever",
        "response_generator",
        "tool_dispatcher",
    ]

    for forbidden_call in forbidden_direct_calls:
        assert forbidden_call not in shortcut_function

    assert "model_copy" in shortcut_function
    assert "side_effects_allowed" not in shortcut_function


def test_shortcut_candidate_uses_shortcut_enabled_extra_engine_only() -> None:
    main_py = (ROOT / "src" / "bookcraft" / "api" / "main.py").read_text(encoding="utf-8")

    assert 'settings.trimatch_extra_mode == "shortcut_candidate"' in main_py
    assert "TriMatchMode.SHORTCUT_ENABLED" in main_py
    assert "settings.trimatch_shortcut_layers" in main_py
    assert "settings.trimatch_shortcut_threshold" in main_py


def test_shortcut_application_runbook_preserves_rollback_and_safety() -> None:
    runbook = (ROOT / "docs" / "runbooks" / "trimatch-shortcut-application-gated.md").read_text(
        encoding="utf-8"
    )

    required_text = [
        "side_effects_allowed",
        "TRIMATCH_EXTRA_MODE=off",
        "pricing engine",
        "portfolio registry",
        "document generator",
        "RAG retriever",
        "response generator",
        "tool dispatcher",
    ]

    for item in required_text:
        assert item in runbook
