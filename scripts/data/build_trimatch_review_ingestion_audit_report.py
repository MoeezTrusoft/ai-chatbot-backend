from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

APPROVAL_DECISIONS = {"approve", "edit_and_approve"}
NON_APPROVAL_DECISIONS = {
    "reject",
    "needs_more_examples",
    "duplicate",
    "unsafe",
    "defer",
}
RISKY_PROMOTION_SCOPES = {
    "advisory",
    "tiebreaker_candidate",
    "shortcut_candidate",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a Tri-Match review ingestion audit report.")
    parser.add_argument(
        "--reinforcement-root",
        default="data/trimatch/reinforcement",
    )
    parser.add_argument(
        "--output-dir",
        default="reports/trimatch",
    )
    args = parser.parse_args()

    report = _build_report(Path(args.reinforcement_root))
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "trimatch_review_ingestion_audit_report.json"
    md_path = output_dir / "trimatch_review_ingestion_audit_report.md"

    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(_markdown(report), encoding="utf-8")

    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(f"json_report={json_path}")
    print(f"markdown_report={md_path}")
    return 0 if report["summary"]["valid"] else 1


def _build_report(root: Path) -> dict[str, Any]:
    candidates = _load_jsonl_many(root / "candidates")
    reviews = _load_jsonl_many(root / "reviews")

    candidate_by_id = {
        str(candidate.get("candidate_id")): candidate
        for candidate in candidates
        if candidate.get("candidate_id")
    }

    review_decision_counts = Counter(str(review.get("decision") or "missing") for review in reviews)
    promotion_scope_counts = Counter(
        str(review.get("promotion_scope") or "missing") for review in reviews
    )
    reviewer_counts = Counter(str(review.get("reviewer") or "missing") for review in reviews)
    review_source_counts = Counter(
        str(review.get("_source_file") or "unknown") for review in reviews
    )

    review_id_counts = Counter(
        str(review.get("review_id")) for review in reviews if review.get("review_id")
    )
    candidate_review_counts = Counter(
        str(review.get("candidate_id")) for review in reviews if review.get("candidate_id")
    )

    duplicate_review_ids = sorted(
        review_id for review_id, count in review_id_counts.items() if count > 1
    )
    duplicate_candidate_reviews = sorted(
        candidate_id for candidate_id, count in candidate_review_counts.items() if count > 1
    )

    reviewed_candidate_ids = set(candidate_review_counts)
    pending_candidate_ids = {
        str(candidate.get("candidate_id"))
        for candidate in candidates
        if candidate.get("status") == "pending_human_review" and candidate.get("candidate_id")
    }
    unreviewed_pending_ids = sorted(pending_candidate_ids - reviewed_candidate_ids)

    unknown_candidate_reviews = [
        _review_snapshot(review)
        for review in reviews
        if str(review.get("candidate_id") or "") not in candidate_by_id
    ]

    approval_reviews = [
        _review_snapshot(review)
        for review in reviews
        if review.get("decision") in APPROVAL_DECISIONS
    ]
    risky_promotion_reviews = [
        _review_snapshot(review)
        for review in reviews
        if review.get("promotion_scope") in RISKY_PROMOTION_SCOPES
    ]
    unsafe_reviews = [
        _review_snapshot(review) for review in reviews if review.get("decision") == "unsafe"
    ]

    non_approval_with_promotion = [
        _review_snapshot(review)
        for review in reviews
        if review.get("decision") in NON_APPROVAL_DECISIONS
        and review.get("promotion_scope") != "none"
    ]

    errors: list[str] = []
    warnings: list[str] = []

    if duplicate_review_ids:
        errors.append("duplicate review IDs detected")
    if unknown_candidate_reviews:
        errors.append("reviews reference unknown candidate IDs")
    if non_approval_with_promotion:
        errors.append("non-approval reviews must use promotion_scope=none")
    if risky_promotion_reviews:
        warnings.append("risky promotion scopes require explicit human governance")
    if duplicate_candidate_reviews:
        warnings.append("some candidates have multiple review rows")

    coverage_ratio = (
        round(len(reviewed_candidate_ids) / len(candidate_by_id), 4) if candidate_by_id else 0.0
    )

    summary = {
        "valid": not errors,
        "generated_at": datetime.now(UTC).isoformat(),
        "candidate_count": len(candidates),
        "review_count": len(reviews),
        "reviewed_candidate_count": len(reviewed_candidate_ids),
        "pending_candidate_count": len(pending_candidate_ids),
        "unreviewed_pending_candidate_count": len(unreviewed_pending_ids),
        "candidate_review_coverage_ratio": coverage_ratio,
        "approval_review_count": len(approval_reviews),
        "unsafe_review_count": len(unsafe_reviews),
        "risky_promotion_review_count": len(risky_promotion_reviews),
        "duplicate_review_id_count": len(duplicate_review_ids),
        "duplicate_candidate_review_count": len(duplicate_candidate_reviews),
        "unknown_candidate_review_count": len(unknown_candidate_reviews),
        "non_approval_with_promotion_count": len(non_approval_with_promotion),
        "review_decision_counts": dict(sorted(review_decision_counts.items())),
        "promotion_scope_counts": dict(sorted(promotion_scope_counts.items())),
        "reviewer_counts": dict(sorted(reviewer_counts.items())),
        "review_source_counts": dict(sorted(review_source_counts.items())),
        "errors": errors,
        "warnings": warnings,
        "safety_note": (
            "This report is observational only. It does not approve, compile, "
            "stage, activate, or change runtime behavior."
        ),
    }

    return {
        "schema_version": 1,
        "summary": summary,
        "details": {
            "duplicate_review_ids": duplicate_review_ids,
            "duplicate_candidate_review_ids": duplicate_candidate_reviews,
            "unknown_candidate_reviews": unknown_candidate_reviews,
            "approval_reviews": approval_reviews,
            "unsafe_reviews": unsafe_reviews,
            "risky_promotion_reviews": risky_promotion_reviews,
            "non_approval_with_promotion": non_approval_with_promotion,
            "unreviewed_pending_candidate_ids": unreviewed_pending_ids[:100],
        },
    }


def _review_snapshot(review: dict[str, Any]) -> dict[str, Any]:
    return {
        "review_id": review.get("review_id"),
        "candidate_id": review.get("candidate_id"),
        "decision": review.get("decision"),
        "reviewer": review.get("reviewer"),
        "promotion_scope": review.get("promotion_scope"),
        "source_file": review.get("_source_file"),
        "reason": review.get("reason"),
    }


def _load_jsonl_many(directory: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not directory.exists():
        return rows

    for path in sorted(directory.rglob("*.jsonl")):
        rows.extend(_load_jsonl(path))

    return rows


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line:
            continue
        loaded = json.loads(line)
        if not isinstance(loaded, dict):
            raise ValueError(f"{path}:{line_number}: row must be a JSON object")
        loaded["_source_file"] = str(path)
        rows.append(loaded)
    return rows


def _markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    details = report["details"]

    lines = [
        "# Tri-Match Review Ingestion Audit Report",
        "",
        "## Summary",
        "",
        f"- Generated at: `{summary['generated_at']}`",
        f"- Valid: `{summary['valid']}`",
        f"- Candidates: `{summary['candidate_count']}`",
        f"- Reviews: `{summary['review_count']}`",
        f"- Reviewed candidates: `{summary['reviewed_candidate_count']}`",
        f"- Pending candidates: `{summary['pending_candidate_count']}`",
        f"- Unreviewed pending candidates: `{summary['unreviewed_pending_candidate_count']}`",
        f"- Candidate review coverage ratio: `{summary['candidate_review_coverage_ratio']}`",
        f"- Approval reviews: `{summary['approval_review_count']}`",
        f"- Unsafe reviews: `{summary['unsafe_review_count']}`",
        f"- Risky promotion reviews: `{summary['risky_promotion_review_count']}`",
        "",
        "## Decision Counts",
        "",
        "```json",
        json.dumps(summary["review_decision_counts"], indent=2, sort_keys=True),
        "```",
        "",
        "## Promotion Scope Counts",
        "",
        "```json",
        json.dumps(summary["promotion_scope_counts"], indent=2, sort_keys=True),
        "```",
        "",
        "## Errors",
        "",
        *[f"- {error}" for error in summary["errors"]],
        "",
        "## Warnings",
        "",
        *[f"- {warning}" for warning in summary["warnings"]],
        "",
        "## Safety Note",
        "",
        str(summary["safety_note"]),
        "",
        "## Review Sources",
        "",
        "```json",
        json.dumps(summary["review_source_counts"], indent=2, sort_keys=True),
        "```",
        "",
        "## Approval Reviews",
        "",
        _table(details["approval_reviews"]),
        "",
        "## Risky Promotion Reviews",
        "",
        _table(details["risky_promotion_reviews"]),
        "",
        "## Unknown Candidate Reviews",
        "",
        _table(details["unknown_candidate_reviews"]),
        "",
    ]

    return "\n".join(lines)


def _table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "_None._"

    lines = [
        "| Review | Candidate | Decision | Scope | Reviewer |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| `{review}` | `{candidate}` | `{decision}` | `{scope}` | `{reviewer}` |".format(
                review=row.get("review_id"),
                candidate=row.get("candidate_id"),
                decision=row.get("decision"),
                scope=row.get("promotion_scope"),
                reviewer=row.get("reviewer"),
            )
        )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
