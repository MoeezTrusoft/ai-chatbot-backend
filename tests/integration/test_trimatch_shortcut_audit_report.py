from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_shortcut_audit_report_tracks_gated_application() -> None:
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

    report_path = ROOT / "reports" / "trimatch" / "trimatch_shortcut_audit_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    summary = report["summary"]

    assert summary["valid"] is True
    assert summary["failed_turns"] == 0
    assert summary["eligible_count"] >= 1
    assert summary["applied_count"] >= 1
    assert summary["side_effects_allowed_count"] == 0
    assert summary["pricing_sensitive_count"] == 1
    assert summary["document_sensitive_count"] == 1
    assert summary["portfolio_sensitive_count"] == 1
    assert summary["applied_dimension_counts"]
    assert summary["applied_value_counts"]
    assert summary["applied_rule_id_counts"]
    assert "blocked_reason_counts" in summary
    assert isinstance(summary["blocked_reason_counts"], dict)
