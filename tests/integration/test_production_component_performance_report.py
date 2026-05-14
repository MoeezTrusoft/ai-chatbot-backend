from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_production_component_report_requires_setup_values() -> None:
    result = subprocess.run(  # noqa: S603 - fixed repo script under test.
        [
            sys.executable,
            "scripts/data/run_production_component_performance_report.py",
            "--output-dir",
            str(ROOT / "reports" / "production"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={},
    )

    assert result.returncode == 1

    report_path = ROOT / "reports" / "production" / "production_component_performance_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert report["summary"]["valid"] is False
    assert report["summary"]["setup_error_count"] >= 1
    assert report["summary"]["message_count"] == 0
    assert report["component_summary"]["critical_issue_count"] == 0


def test_production_component_report_includes_provider_analysis_sections() -> None:
    script = ROOT / "scripts" / "data" / "run_production_component_performance_report.py"
    text = script.read_text(encoding="utf-8")

    assert "analyze_provider_votes" in text
    assert "provider_health" in text
    assert "fallback_summary" in text
    assert "response_quality" in text
    assert "soft_warning_count" in text


def test_provider_analysis_handles_null_audit_trail() -> None:
    script = ROOT / "scripts" / "data" / "run_production_component_performance_report.py"
    text = script.read_text(encoding="utf-8")

    assert "raw_audit_trail" in text
    assert "audit_trail = raw_audit_trail if isinstance(raw_audit_trail, list) else []" in text
    assert "evidence = raw_evidence if isinstance(raw_evidence, list) else []" in text
