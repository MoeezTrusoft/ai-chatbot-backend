from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a Tri-Match human-review queue from candidates and reviews."
    )
    parser.add_argument(
        "--reinforcement-root",
        default="data/trimatch/reinforcement",
    )
    parser.add_argument(
        "--output-dir",
        default="reports/trimatch",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional max queue items. 0 means no limit.",
    )
    parser.add_argument(
        "--include-reviewed",
        action="store_true",
        help="Include candidates that already have at least one review.",
    )
    args = parser.parse_args()

    report = _build_report(args)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "trimatch_human_review_queue.json"
    md_path = output_dir / "trimatch_human_review_queue.md"

    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(_markdown(report), encoding="utf-8")

    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(f"json_report={json_path}")
    print(f"markdown_report={md_path}")
    return 0


def _build_report(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.reinforcement_root)
    candidates = _load_jsonl_many(root / "candidates")
    reviews = _load_jsonl_many(root / "reviews")

    reviews_by_candidate: dict[str, list[dict[str, Any]]] = {}
    for review in reviews:
        candidate_id = str(review.get("candidate_id") or "")
        if not candidate_id:
            continue
        reviews_by_candidate.setdefault(candidate_id, []).append(review)

    queue: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id") or "")
        if not candidate_id:
            continue

        existing_reviews = reviews_by_candidate.get(candidate_id, [])
        if existing_reviews and not args.include_reviewed:
            continue

        status = str(candidate.get("status") or "")
        if status != "pending_human_review" and not args.include_reviewed:
            continue

        queue.append(_queue_item(candidate, existing_reviews))

    queue.sort(
        key=lambda item: (
            str(item.get("target_dimension")),
            str(item.get("target_label")),
            str(item.get("candidate_id")),
        )
    )

    if args.limit > 0:
        queue = queue[: args.limit]

    status_counts = Counter(str(item.get("status") or "") for item in candidates)
    decision_counts = Counter(str(item.get("decision") or "") for item in reviews)

    summary = {
        "valid": True,
        "generated_at": datetime.now().astimezone().isoformat(),
        "candidate_count": len(candidates),
        "review_count": len(reviews),
        "queue_count": len(queue),
        "include_reviewed": bool(args.include_reviewed),
        "candidate_status_counts": dict(sorted(status_counts.items())),
        "review_decision_counts": dict(sorted(decision_counts.items())),
        "safety_note": (
            "This report is observational only. It does not approve, compile, "
            "or activate any Tri-Match rules."
        ),
    }

    return {
        "schema_version": 1,
        "summary": summary,
        "queue": queue,
    }


def _queue_item(
    candidate: dict[str, Any],
    existing_reviews: list[dict[str, Any]],
) -> dict[str, Any]:
    latest_review = existing_reviews[-1] if existing_reviews else None
    proposal = candidate.get("proposal")
    source = candidate.get("source")

    return {
        "candidate_id": candidate.get("candidate_id"),
        "candidate_type": candidate.get("candidate_type"),
        "target_layer": candidate.get("target_layer"),
        "target_dimension": candidate.get("target_dimension"),
        "target_label": candidate.get("target_label"),
        "status": candidate.get("status"),
        "suggested_weight": candidate.get("suggested_weight"),
        "suggested_by": candidate.get("suggested_by"),
        "proposal": proposal if isinstance(proposal, dict) else {},
        "source": source if isinstance(source, dict) else {},
        "positive_examples": candidate.get("positive_examples", []),
        "negative_examples": candidate.get("negative_examples", []),
        "risk_note": candidate.get("risk_note"),
        "existing_review_count": len(existing_reviews),
        "latest_review_decision": latest_review.get("decision") if latest_review else None,
        "latest_review_id": latest_review.get("review_id") if latest_review else None,
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
        rows.append(loaded)
    return rows


def _markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]

    lines = [
        "# Tri-Match Human Review Queue",
        "",
        "## Summary",
        "",
        f"- Generated at: `{summary['generated_at']}`",
        f"- Candidates: `{summary['candidate_count']}`",
        f"- Reviews: `{summary['review_count']}`",
        f"- Queue items: `{summary['queue_count']}`",
        f"- Include reviewed: `{summary['include_reviewed']}`",
        "",
        "## Safety Note",
        "",
        str(summary["safety_note"]),
        "",
        "## Queue",
        "",
        "| # | Candidate | Type | Target | Proposal | Reviews |",
        "|---:|---|---|---|---|---:|",
    ]

    for index, item in enumerate(report["queue"], start=1):
        proposal = item.get("proposal", {})
        proposal_value = ""
        if isinstance(proposal, dict):
            proposal_value = str(proposal.get("value") or "")

        target = "{dimension}:{label}".format(
            dimension=item.get("target_dimension"),
            label=item.get("target_label"),
        )

        lines.append(
            "| {index} | `{candidate_id}` | `{candidate_type}` | `{target}` | "
            "{proposal} | `{review_count}` |".format(
                index=index,
                candidate_id=item.get("candidate_id"),
                candidate_type=item.get("candidate_type"),
                target=target,
                proposal=_escape_table(proposal_value[:80]),
                review_count=item.get("existing_review_count"),
            )
        )

    lines.append("")
    return "\n".join(lines)


def _escape_table(value: str) -> str:
    return value.replace("|", "\\|")


if __name__ == "__main__":
    raise SystemExit(main())
