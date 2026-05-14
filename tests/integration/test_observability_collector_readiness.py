from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_observability_collector_readiness_safe_default() -> None:
    result = subprocess.run(  # noqa: S603 - fixed repo script under test.
        [
            sys.executable,
            "scripts/data/run_observability_collector_readiness.py",
            "--output-dir",
            str(ROOT / "reports" / "chatbot"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr

    report_path = ROOT / "reports" / "chatbot" / "observability_collector_readiness_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    summary = report["summary"]

    assert summary["valid"] is True
    assert summary["check_externals"] is False
    assert summary["collector_required_for_staging"] is True

    check_names = {check["name"] for check in report["checks"]}
    assert "artifact:ops/otel/otel-collector-config.yaml" in check_names
    assert "otel_endpoint_configured" in check_names
    assert "external_observability_checks" in check_names
