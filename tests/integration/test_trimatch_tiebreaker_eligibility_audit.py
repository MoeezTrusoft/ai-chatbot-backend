from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_tiebreaker_audit_reports_eligibility_and_blocked_reasons() -> None:
    result = subprocess.run(  # noqa: S603 - fixed repo script under test.
        [
            sys.executable,
            "scripts/data/run_trimatch_tiebreaker_audit_report.py",
            "--output-dir",
            str(ROOT / "reports" / "trimatch"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr

    report_path = ROOT / "reports" / "trimatch" / "trimatch_tiebreaker_audit_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    summary = report["summary"]

    assert summary["valid"] is True
    assert summary["failed_turns"] == 0
    assert summary["applied_count"] == 0
    assert summary["side_effects_allowed_count"] == 0
    assert "blocked_reason_counts" in summary
    assert isinstance(summary["blocked_reason_counts"], dict)

    blocked_reasons = summary["blocked_reason_counts"]

    assert "safety-sensitive intent cannot use tiebreaker" in blocked_reasons
    assert "forbidden recommended value: pricing_question" in blocked_reasons
    assert "forbidden recommended value: agreement_request" in blocked_reasons
    assert "forbidden recommended value: portfolio_request" in blocked_reasons
