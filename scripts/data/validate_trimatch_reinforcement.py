from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path("data/trimatch/reinforcement")

CANDIDATE_STATUSES = {
    "pending_human_review",
    "approved",
    "rejected",
    "edited_and_approved",
    "needs_more_examples",
    "duplicate",
    "unsafe",
    "deferred",
}
REVIEW_DECISIONS = {
    "approve",
    "reject",
    "edit_and_approve",
    "needs_more_examples",
    "duplicate",
    "unsafe",
    "defer",
}
CANDIDATE_ID_RE = re.compile(r"^cand_[0-9]{8}_[0-9]{4}$")
REVIEW_ID_RE = re.compile(r"^review_[0-9]{8}_[0-9]{4}$")


def main() -> int:
    errors: list[str] = []

    candidates = _load_jsonl_many(ROOT / "candidates", errors)
    reviews = _load_jsonl_many(ROOT / "reviews", errors)

    _require_file(ROOT / "candidates" / "schema.json", errors)
    _require_file(ROOT / "reviews" / "schema.json", errors)

    candidate_ids: set[str] = set()
    for index, candidate in enumerate(candidates, start=1):
        _validate_candidate(index, candidate, candidate_ids, errors)

    review_ids: set[str] = set()
    for index, review in enumerate(reviews, start=1):
        _validate_review(index, review, review_ids, candidate_ids, errors)

    result = {
        "valid": not errors,
        "candidate_count": len(candidates),
        "review_count": len(reviews),
        "errors": errors,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not errors else 1


def _load_jsonl_many(directory: Path, errors: list[str]) -> list[dict[str, Any]]:
    if not directory.exists():
        errors.append(f"missing directory: {directory}")
        return []

    rows: list[dict[str, Any]] = []
    for path in sorted(directory.rglob("*.jsonl")):
        rows.extend(_load_jsonl(path, errors))
    return rows


def _load_jsonl(path: Path, errors: list[str]) -> list[dict[str, Any]]:
    if not path.exists():
        errors.append(f"missing file: {path}")
        return []

    rows: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            loaded = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"{path}:{line_number}: invalid json: {exc}")
            continue
        if not isinstance(loaded, dict):
            errors.append(f"{path}:{line_number}: row must be an object")
            continue
        rows.append(loaded)
    return rows


def _validate_candidate(
    index: int,
    candidate: dict[str, Any],
    seen_ids: set[str],
    errors: list[str],
) -> None:
    prefix = f"candidate[{index}]"
    candidate_id = _string(candidate.get("candidate_id"))
    if not candidate_id or not CANDIDATE_ID_RE.match(candidate_id):
        errors.append(f"{prefix}: invalid candidate_id")
    elif candidate_id in seen_ids:
        errors.append(f"{prefix}: duplicate candidate_id {candidate_id}")
    else:
        seen_ids.add(candidate_id)

    status = _string(candidate.get("status"))
    if status not in CANDIDATE_STATUSES:
        errors.append(f"{prefix}: invalid status {status}")

    positive_examples = candidate.get("positive_examples")
    negative_examples = candidate.get("negative_examples")
    if not isinstance(positive_examples, list) or len(positive_examples) < 3:
        errors.append(f"{prefix}: needs at least 3 positive_examples")
    if not isinstance(negative_examples, list) or len(negative_examples) < 3:
        errors.append(f"{prefix}: needs at least 3 negative_examples")

    risk_note = _string(candidate.get("risk_note"))
    if len(risk_note) < 10:
        errors.append(f"{prefix}: risk_note too short")

    suggested_weight = candidate.get("suggested_weight")
    if not isinstance(suggested_weight, int | float) or not 0 <= float(suggested_weight) <= 1:
        errors.append(f"{prefix}: suggested_weight must be 0..1")

    _validate_datetime(prefix, candidate.get("created_at"), "created_at", errors)

    source = candidate.get("source")
    if not isinstance(source, dict) or not _string(source.get("source_id")):
        errors.append(f"{prefix}: source.source_id required")

    proposal = candidate.get("proposal")
    if not isinstance(proposal, dict) or not _string(proposal.get("value")):
        errors.append(f"{prefix}: proposal.value required")


def _validate_review(
    index: int,
    review: dict[str, Any],
    seen_ids: set[str],
    candidate_ids: set[str],
    errors: list[str],
) -> None:
    prefix = f"review[{index}]"
    review_id = _string(review.get("review_id"))
    if not review_id or not REVIEW_ID_RE.match(review_id):
        errors.append(f"{prefix}: invalid review_id")
    elif review_id in seen_ids:
        errors.append(f"{prefix}: duplicate review_id {review_id}")
    else:
        seen_ids.add(review_id)

    candidate_id = _string(review.get("candidate_id"))
    if candidate_id not in candidate_ids:
        errors.append(f"{prefix}: unknown candidate_id {candidate_id}")

    decision = _string(review.get("decision"))
    if decision not in REVIEW_DECISIONS:
        errors.append(f"{prefix}: invalid decision {decision}")

    reason = _string(review.get("reason"))
    if len(reason) < 10:
        errors.append(f"{prefix}: reason too short")

    _validate_datetime(prefix, review.get("reviewed_at"), "reviewed_at", errors)


def _validate_datetime(prefix: str, value: object, field: str, errors: list[str]) -> None:
    text = _string(value)
    if not text:
        errors.append(f"{prefix}: {field} required")
        return
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{prefix}: {field} must be ISO datetime")


def _require_file(path: Path, errors: list[str]) -> None:
    if not path.exists():
        errors.append(f"missing file: {path}")


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


if __name__ == "__main__":
    raise SystemExit(main())
