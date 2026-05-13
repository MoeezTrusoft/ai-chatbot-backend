from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SAFE_TEMPLATE_DECISIONS = {
    "defer",
    "needs_more_examples",
    "reject",
    "duplicate",
    "unsafe",
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a safe Tri-Match human-review batch template."
    )
    parser.add_argument(
        "--queue-report",
        default="reports/trimatch/trimatch_human_review_queue.json",
    )
    parser.add_argument(
        "--output",
        default="reports/trimatch/trimatch_review_batch_template.jsonl",
    )
    parser.add_argument("--reviewer", default="human_reviewer")
    parser.add_argument(
        "--decision",
        default="defer",
        choices=sorted(SAFE_TEMPLATE_DECISIONS),
        help="Safe non-approval decision for generated template rows.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=25,
    )
    parser.add_argument(
        "--id-start",
        type=int,
        default=9901,
    )
    args = parser.parse_args()

    queue_report = Path(args.queue_report)
    if not queue_report.exists():
        raise SystemExit(f"missing queue report: {queue_report}")

    loaded = json.loads(queue_report.read_text(encoding="utf-8"))
    queue = loaded.get("queue", [])
    if not isinstance(queue, list):
        raise SystemExit("invalid queue report: queue must be a list")

    rows = _build_review_rows(queue, args)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    result = {
        "valid": True,
        "output": str(output),
        "template_review_count": len(rows),
        "decision": args.decision,
        "safety_note": (
            "Template rows use non-approval decisions only. Human reviewers must "
            "manually edit decisions before any approval can be compiled."
        ),
    }

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _build_review_rows(
    queue: list[object],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    now = datetime.now(UTC)
    reviewed_at = now.isoformat().replace("+00:00", "Z")
    date_token = now.strftime("%Y%m%d")

    for offset, item in enumerate(queue[: args.limit]):
        if not isinstance(item, dict):
            continue

        candidate_id = str(item.get("candidate_id") or "")
        if not candidate_id:
            continue

        review_id = f"review_{date_token}_{args.id_start + offset:04d}"

        rows.append(
            {
                "review_id": review_id,
                "candidate_id": candidate_id,
                "decision": args.decision,
                "reviewer": args.reviewer,
                "reviewed_at": reviewed_at,
                "human_label": _human_label(item),
                "edited_proposal": None,
                "reason": _reason(item, args.decision),
                "promotion_scope": "none",
                "required_followups": _required_followups(item),
            }
        )

    return rows


def _human_label(item: dict[str, Any]) -> dict[str, Any]:
    dimension = str(item.get("target_dimension") or "")
    label = str(item.get("target_label") or "")

    service_primary = None
    query_primary = None
    funnel_stage = None

    if dimension == "service_intent":
        service_primary = label
    elif dimension == "query_intent":
        query_primary = label
    elif dimension == "funnel_stage":
        funnel_stage = label

    return {
        "service_primary": service_primary,
        "query_primary": query_primary,
        "funnel_stage": funnel_stage,
        "negated_services": [],
    }


def _reason(item: dict[str, Any], decision: str) -> str:
    target = "{dimension}:{label}".format(
        dimension=item.get("target_dimension"),
        label=item.get("target_label"),
    )
    return (
        f"Batch template marked {decision} for {target}; human reviewer must "
        "inspect examples, negation, hedging, counterfactual, pricing, document, "
        "and portfolio safety before approval."
    )


def _required_followups(item: dict[str, Any]) -> list[str]:
    risk_note = str(item.get("risk_note") or "").strip()
    followups = [
        "Confirm positive examples are precise.",
        "Confirm negative examples block overmatching.",
        "Confirm no pricing/document/portfolio safety bypass.",
    ]
    if risk_note:
        followups.append(risk_note)
    return followups


if __name__ == "__main__":
    raise SystemExit(main())
