from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CANDIDATE_ID_RE = re.compile(r"^cand_[0-9]{8}_[0-9]{4}$")
REVIEW_ID_RE = re.compile(r"^review_[0-9]{8}_[0-9]{4}$")

REVIEW_DECISIONS = {
    "approve",
    "reject",
    "edit_and_approve",
    "needs_more_examples",
    "duplicate",
    "unsafe",
    "defer",
}

APPROVAL_DECISIONS = {"approve", "edit_and_approve"}

PROMOTION_SCOPES = {
    "none",
    "staged_only",
    "shadow",
    "advisory",
    "tiebreaker_candidate",
    "shortcut_candidate",
}

RISKY_PROMOTION_SCOPES = {
    "advisory",
    "tiebreaker_candidate",
    "shortcut_candidate",
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate and optionally ingest a Tri-Match review batch JSONL."
    )
    parser.add_argument(
        "--input",
        default="reports/trimatch/trimatch_review_batch_template.jsonl",
        help="Human-edited review batch JSONL to validate/import.",
    )
    parser.add_argument(
        "--reinforcement-root",
        default="data/trimatch/reinforcement",
    )
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Output JSONL path under reinforcement reviews. Defaults to a "
            "timestamped file in data/trimatch/reinforcement/reviews/generated/."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write valid rows into the reinforcement reviews directory.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow replacing an existing output file when --apply is used.",
    )
    parser.add_argument(
        "--allow-approval-decisions",
        action="store_true",
        help=(
            "Allow approve/edit_and_approve rows. Without this flag, approvals "
            "are rejected so templates cannot be ingested accidentally."
        ),
    )
    parser.add_argument(
        "--allow-risky-promotion-scope",
        action="store_true",
        help=(
            "Allow advisory/tiebreaker/shortcut promotion_scope values. "
            "Normally these are blocked for safety."
        ),
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    root = Path(args.reinforcement_root)
    output = Path(args.output) if args.output else _default_output(root)

    result = _validate_batch(
        input_path=input_path,
        reinforcement_root=root,
        output_path=output,
        apply=bool(args.apply),
        overwrite=bool(args.overwrite),
        allow_approval_decisions=bool(args.allow_approval_decisions),
        allow_risky_promotion_scope=bool(args.allow_risky_promotion_scope),
    )

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


def _validate_batch(
    *,
    input_path: Path,
    reinforcement_root: Path,
    output_path: Path,
    apply: bool,
    overwrite: bool,
    allow_approval_decisions: bool,
    allow_risky_promotion_scope: bool,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    if not input_path.exists():
        errors.append(f"missing input file: {input_path}")
        return _result(
            valid=False,
            input_path=input_path,
            output_path=output_path,
            apply=apply,
            rows=[],
            errors=errors,
            warnings=warnings,
        )

    candidates = _load_jsonl_many(reinforcement_root / "candidates")
    existing_reviews = _load_jsonl_many(reinforcement_root / "reviews")
    rows = _load_jsonl(input_path, errors)

    candidate_ids = {
        str(candidate.get("candidate_id"))
        for candidate in candidates
        if candidate.get("candidate_id")
    }
    existing_review_ids = {
        str(review.get("review_id")) for review in existing_reviews if review.get("review_id")
    }

    seen_review_ids: set[str] = set()
    seen_candidate_ids: set[str] = set()

    for index, row in enumerate(rows, start=1):
        _validate_review_row(
            index=index,
            row=row,
            candidate_ids=candidate_ids,
            existing_review_ids=existing_review_ids,
            seen_review_ids=seen_review_ids,
            seen_candidate_ids=seen_candidate_ids,
            allow_approval_decisions=allow_approval_decisions,
            allow_risky_promotion_scope=allow_risky_promotion_scope,
            errors=errors,
            warnings=warnings,
        )

    if apply:
        if errors:
            warnings.append("not writing output because validation failed")
        elif output_path.exists() and not overwrite:
            errors.append(f"output already exists; pass --overwrite to replace: {output_path}")
        else:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with output_path.open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, sort_keys=True) + "\n")

    return _result(
        valid=not errors,
        input_path=input_path,
        output_path=output_path,
        apply=apply,
        rows=rows,
        errors=errors,
        warnings=warnings,
    )


def _validate_review_row(
    *,
    index: int,
    row: dict[str, Any],
    candidate_ids: set[str],
    existing_review_ids: set[str],
    seen_review_ids: set[str],
    seen_candidate_ids: set[str],
    allow_approval_decisions: bool,
    allow_risky_promotion_scope: bool,
    errors: list[str],
    warnings: list[str],
) -> None:
    prefix = f"row[{index}]"

    review_id = _string(row.get("review_id"))
    if not REVIEW_ID_RE.match(review_id):
        errors.append(f"{prefix}: invalid review_id")
    elif review_id in existing_review_ids:
        errors.append(f"{prefix}: review_id already exists in reinforcement reviews")
    elif review_id in seen_review_ids:
        errors.append(f"{prefix}: duplicate review_id in input")
    else:
        seen_review_ids.add(review_id)

    candidate_id = _string(row.get("candidate_id"))
    if not CANDIDATE_ID_RE.match(candidate_id):
        errors.append(f"{prefix}: invalid candidate_id")
    elif candidate_id not in candidate_ids:
        errors.append(f"{prefix}: unknown candidate_id {candidate_id}")
    elif candidate_id in seen_candidate_ids:
        warnings.append(f"{prefix}: candidate_id appears more than once in input")
    else:
        seen_candidate_ids.add(candidate_id)

    decision = _string(row.get("decision"))
    if decision not in REVIEW_DECISIONS:
        errors.append(f"{prefix}: invalid decision {decision}")

    if decision in APPROVAL_DECISIONS and not allow_approval_decisions:
        errors.append(f"{prefix}: approval decision requires --allow-approval-decisions")

    reviewer = _string(row.get("reviewer"))
    if len(reviewer) < 2:
        errors.append(f"{prefix}: reviewer must be at least 2 characters")

    _validate_datetime(prefix, row.get("reviewed_at"), "reviewed_at", errors)

    human_label = row.get("human_label")
    if not isinstance(human_label, dict):
        errors.append(f"{prefix}: human_label must be an object")
    else:
        negated_services = human_label.get("negated_services")
        if not isinstance(negated_services, list):
            errors.append(f"{prefix}: human_label.negated_services must be an array")

    edited_proposal = row.get("edited_proposal")
    if edited_proposal is not None and not isinstance(edited_proposal, dict):
        errors.append(f"{prefix}: edited_proposal must be object or null")

    reason = _string(row.get("reason"))
    if len(reason) < 10:
        errors.append(f"{prefix}: reason must be at least 10 characters")

    promotion_scope = _string(row.get("promotion_scope"))
    if promotion_scope not in PROMOTION_SCOPES:
        errors.append(f"{prefix}: invalid promotion_scope {promotion_scope}")

    if promotion_scope in RISKY_PROMOTION_SCOPES and not allow_risky_promotion_scope:
        errors.append(f"{prefix}: risky promotion_scope requires --allow-risky-promotion-scope")

    followups = row.get("required_followups", [])
    if followups is not None and not isinstance(followups, list):
        errors.append(f"{prefix}: required_followups must be an array")


def _validate_datetime(
    prefix: str,
    value: object,
    field: str,
    errors: list[str],
) -> None:
    text = _string(value)
    if not text:
        errors.append(f"{prefix}: {field} is required")
        return

    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{prefix}: {field} must be ISO datetime")


def _result(
    *,
    valid: bool,
    input_path: Path,
    output_path: Path,
    apply: bool,
    rows: list[dict[str, Any]],
    errors: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    approval_count = sum(1 for row in rows if row.get("decision") in APPROVAL_DECISIONS)

    return {
        "valid": valid,
        "apply": apply,
        "input": str(input_path),
        "output": str(output_path),
        "review_count": len(rows),
        "approval_decision_count": approval_count,
        "errors": errors,
        "warnings": warnings,
        "safety_note": (
            "This tool validates and optionally copies human-edited reviews. "
            "It does not generate approvals, compile rules, or activate runtime behavior."
        ),
    }


def _default_output(root: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return root / "reviews" / "generated" / f"reviews.ingested.{stamp}.jsonl"


def _load_jsonl_many(directory: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not directory.exists():
        return rows

    for path in sorted(directory.rglob("*.jsonl")):
        rows.extend(_load_jsonl(path, []))

    return rows


def _load_jsonl(path: Path, errors: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line:
            continue
        try:
            loaded = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"{path}:{line_number}: invalid JSON: {exc}")
            continue
        if not isinstance(loaded, dict):
            errors.append(f"{path}:{line_number}: row must be a JSON object")
            continue
        rows.append(loaded)
    return rows


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


if __name__ == "__main__":
    raise SystemExit(main())
