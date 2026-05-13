from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def test_review_template_is_non_approval_by_default(tmp_path: Path) -> None:
    root = _write_reinforcement_fixture(tmp_path)
    reports = tmp_path / "reports"

    _run(
        "scripts/data/build_trimatch_human_review_queue.py",
        "--reinforcement-root",
        str(root),
        "--output-dir",
        str(reports),
    )

    queue_report = reports / "trimatch_human_review_queue.json"
    queue = json.loads(queue_report.read_text(encoding="utf-8"))

    assert queue["summary"]["queue_count"] == 1
    assert queue["summary"]["safety_note"].startswith("This report is observational only")

    template_path = reports / "review_batch_template.jsonl"
    _run(
        "scripts/data/create_trimatch_review_batch_template.py",
        "--queue-report",
        str(queue_report),
        "--output",
        str(template_path),
        "--reviewer",
        "governance_smoke",
    )

    rows = _load_jsonl(template_path)

    assert len(rows) == 1
    assert rows[0]["decision"] == "defer"
    assert rows[0]["promotion_scope"] == "none"
    assert rows[0]["decision"] not in {"approve", "edit_and_approve"}


def test_review_ingestion_blocks_approval_without_explicit_flag(tmp_path: Path) -> None:
    root = _write_reinforcement_fixture(tmp_path)
    batch_path = tmp_path / "manual_review_batch.jsonl"
    output_path = root / "reviews" / "generated" / "manual_reviews.jsonl"

    approval_review = _review_row(
        review_id="review_20260513_9901",
        candidate_id="cand_20260513_9901",
        decision="approve",
        promotion_scope="staged_only",
    )
    _write_jsonl(batch_path, [approval_review])

    blocked = _run(
        "scripts/data/import_trimatch_review_batch_template.py",
        "--input",
        str(batch_path),
        "--reinforcement-root",
        str(root),
        "--output",
        str(output_path),
        expected_returncode=1,
    )

    assert "approval decision requires --allow-approval-decisions" in blocked.stdout
    assert not output_path.exists()

    allowed_dry_run = _run(
        "scripts/data/import_trimatch_review_batch_template.py",
        "--input",
        str(batch_path),
        "--reinforcement-root",
        str(root),
        "--output",
        str(output_path),
        "--allow-approval-decisions",
    )

    assert '"valid": true' in allowed_dry_run.stdout
    assert not output_path.exists()

    _run(
        "scripts/data/import_trimatch_review_batch_template.py",
        "--input",
        str(batch_path),
        "--reinforcement-root",
        str(root),
        "--output",
        str(output_path),
        "--allow-approval-decisions",
        "--apply",
    )

    assert output_path.exists()
    ingested = _load_jsonl(output_path)
    assert ingested == [approval_review]


def test_approved_candidate_compile_remains_staged_and_shortcut_disabled(
    tmp_path: Path,
) -> None:
    root = _write_reinforcement_fixture(tmp_path)
    review_path = root / "reviews" / "generated" / "approved_reviews.jsonl"
    compiled_path = tmp_path / "compiled.rulepack.json"

    _write_jsonl(
        review_path,
        [
            _review_row(
                review_id="review_20260513_9902",
                candidate_id="cand_20260513_9901",
                decision="approve",
                promotion_scope="staged_only",
            )
        ],
    )

    _run(
        "scripts/data/compile_approved_trimatch_candidates.py",
        "--reinforcement-root",
        str(root),
        "--output",
        str(compiled_path),
        "--version",
        "approved_candidates.governance_smoke.v1",
    )

    compiled = json.loads(compiled_path.read_text(encoding="utf-8"))

    assert compiled["version"] == "approved_candidates.governance_smoke.v1"
    assert len(compiled["rules"]) == 1
    assert compiled["rules"][0]["enabled"] is True
    assert compiled["rules"][0]["shortcut_allowed"] is False


def test_runtime_shadow_review_still_passes_observationally() -> None:
    result = _run("scripts/data/run_trimatch_shadow_runtime_review.py")

    assert '"valid": true' in result.stdout
    assert '"recommendation": "shadow_runtime_review_passed"' in result.stdout
    assert '"extra_shadow_event_count": 8' in result.stdout


def _write_reinforcement_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "reinforcement"
    candidates_dir = root / "candidates" / "generated"
    reviews_dir = root / "reviews" / "generated"
    candidates_dir.mkdir(parents=True, exist_ok=True)
    reviews_dir.mkdir(parents=True, exist_ok=True)

    _write_jsonl(
        candidates_dir / "governance_candidates.jsonl",
        [
            {
                "candidate_id": "cand_20260513_9901",
                "candidate_type": "pattern",
                "target_layer": "pattern",
                "target_dimension": "service_intent",
                "target_label": "editing_proofreading",
                "source": {
                    "source_type": "diagnostic_failure",
                    "source_id": "governance_smoke",
                    "conversation_id": None,
                    "turn_index": 1,
                },
                "proposal": {
                    "value": "proofreading help",
                    "normalized_value": "proofreading help",
                    "regex_flags": None,
                },
                "positive_examples": [
                    "I need proofreading help.",
                    "Please proofread my manuscript.",
                    "Can BookCraft edit this draft?",
                ],
                "negative_examples": [
                    "I do not need proofreading help.",
                    "Do not include editing in this scope.",
                    "Editing is not approved yet.",
                ],
                "risk_note": (
                    "Governance smoke candidate; confirm it does not overmatch "
                    "negated, hedged, counterfactual, pricing, portfolio, NDA, "
                    "or agreement contexts."
                ),
                "suggested_weight": 0.62,
                "suggested_by": "diagnostic_analyzer",
                "status": "pending_human_review",
                "created_at": "2026-05-13T00:00:00Z",
            }
        ],
    )

    return root


def _review_row(
    *,
    review_id: str,
    candidate_id: str,
    decision: str,
    promotion_scope: str,
) -> dict[str, Any]:
    return {
        "review_id": review_id,
        "candidate_id": candidate_id,
        "decision": decision,
        "reviewer": "governance_smoke",
        "reviewed_at": "2026-05-13T00:00:00Z",
        "human_label": {
            "service_primary": "editing_proofreading",
            "query_primary": "service_question",
            "funnel_stage": "service_discovery",
            "negated_services": [],
        },
        "edited_proposal": None,
        "reason": (
            "Governance smoke review row used to verify explicit human approval "
            "gates and staged-only compilation."
        ),
        "promotion_scope": promotion_scope,
        "required_followups": [
            "Confirm no pricing, document, portfolio, or shortcut bypass.",
        ],
    }


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _run(
    *args: str,
    expected_returncode: int = 0,
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, *args]

    result = subprocess.run(  # noqa: S603 - test invokes fixed repo scripts via sys.executable.
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == expected_returncode, (
        "command failed\n"
        f"command: {' '.join(command)}\n"
        f"returncode: {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    return result
