from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_rag_external_rollout_checklist_default_is_safe() -> None:
    result = subprocess.run(  # noqa: S603 - fixed repo script under test.
        [
            sys.executable,
            "scripts/data/build_rag_external_rollout_checklist_report.py",
            "--output-dir",
            str(ROOT / "reports" / "rag"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr

    report_path = ROOT / "reports" / "rag" / "rag_external_rollout_checklist_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    summary = report["summary"]

    assert summary["valid"] is True
    assert summary["check_externals"] is False
    assert summary["creates_index"] is False
    assert summary["moves_alias"] is False
    assert summary["embeds_source_documents"] is False
    assert report["external_result"] is None


def test_rag_external_rollout_checklist_contains_gated_commands() -> None:
    result = subprocess.run(  # noqa: S603 - fixed repo script under test.
        [
            sys.executable,
            "scripts/data/build_rag_external_rollout_checklist_report.py",
            "--output-dir",
            str(ROOT / "reports" / "rag"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr

    report_path = ROOT / "reports" / "rag" / "rag_external_rollout_checklist_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))

    commands = {item["name"]: item for item in report["rollout_commands"]}

    assert commands["candidate_index_build"]["creates_index"] is True
    assert commands["candidate_index_build"]["moves_alias"] is False
    assert commands["alias_swap"]["creates_index"] is True
    assert commands["alias_swap"]["moves_alias"] is True
    assert commands["candidate_smoke"]["requires_externals"] is True
    assert commands["live_alias_smoke"]["requires_externals"] is True

    assert any("pricing query returns RAG chunks" in item for item in report["stop_conditions"])
    assert report["rollback"]["strategy"] == "alias_only"
