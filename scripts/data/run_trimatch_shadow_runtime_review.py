from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bookcraft.api.chat import ChatTurnRequest
from bookcraft.api.main import build_chat_service
from bookcraft.infra.config import Settings


@dataclass(frozen=True)
class ReviewCase:
    name: str
    message: str
    expected_final_query: str | None = None
    expected_final_service: str | None = None
    expected_shadow_service: str | None = None
    disallowed_final_services: tuple[str, ...] = ()


REVIEW_CASES: tuple[ReviewCase, ...] = (
    ReviewCase(
        name="service_discovery_overview",
        message="What does BookCraft do for authors?",
        expected_final_query="service_question",
    ),
    ReviewCase(
        name="editing_service_detected",
        message="I need proofreading help for my completed manuscript.",
        expected_final_query="service_question",
        expected_final_service="editing_proofreading",
    ),
    ReviewCase(
        name="negated_ghostwriting_not_requested",
        message="I do not need ghostwriting. I only want proofreading and interior formatting.",
        disallowed_final_services=("ghostwriting",),
    ),
    ReviewCase(
        name="pricing_scope_gate",
        message="How much for editing my 40,000 word memoir?",
        expected_final_service="editing_proofreading",
    ),
    ReviewCase(
        name="portfolio_samples",
        message="Can I see cover design samples for a memoir?",
        expected_final_query="portfolio_request",
        expected_final_service="cover_design_illustration",
    ),
    ReviewCase(
        name="nda_request",
        message="I need an NDA before sharing my manuscript.",
        expected_final_query="nda_request",
    ),
    ReviewCase(
        name="agreement_request",
        message="Please prepare the agreement for my publishing package.",
        expected_final_query="agreement_request",
    ),
    ReviewCase(
        name="extra_shadow_marker",
        message="rare shadow video marker, but please just tell me how BookCraft works.",
        expected_shadow_service="video_trailer",
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Tri-Match extra RulePack shadow runtime review cases."
    )
    parser.add_argument(
        "--output-dir",
        default="reports/trimatch",
        help="Directory for JSON/Markdown review outputs.",
    )
    parser.add_argument(
        "--runtime-extra-rule-dir",
        default="reports/trimatch/shadow_runtime_extra_rules",
        help="Runtime-only copied extra rule directory used by this review.",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    runtime_extra_rule_dir = Path(args.runtime_extra_rule_dir)
    _prepare_runtime_extra_rules(runtime_extra_rule_dir)

    report = asyncio.run(_run_review(runtime_extra_rule_dir))

    json_path = output_dir / "trimatch_shadow_runtime_review.json"
    md_path = output_dir / "trimatch_shadow_runtime_review.md"

    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    md_path.write_text(_markdown(report), encoding="utf-8")

    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(f"json_report={json_path}")
    print(f"markdown_report={md_path}")

    return 0 if report["summary"]["valid"] else 1


def _prepare_runtime_extra_rules(runtime_dir: Path) -> None:
    if runtime_dir.exists():
        shutil.rmtree(runtime_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True)

    staged_dir = Path("data/trimatch/reinforcement/staged_from_reviews")
    if staged_dir.exists():
        for path in sorted(staged_dir.glob("*.json")):
            shutil.copy2(path, runtime_dir / path.name)

    _write_shadow_marker_rule_pack(runtime_dir)


def _write_shadow_marker_rule_pack(directory: Path) -> None:
    marker_pack = {
        "version": "shadow_runtime_review_marker.v1",
        "rules": [
            {
                "id": "shadow_runtime_review_video_marker",
                "layer": "exact",
                "target": {
                    "service_intent": "video_trailer",
                    "query_intent": None,
                    "funnel_stage": None,
                },
                "phrases": ["rare shadow video marker"],
                "regex": None,
                "pattern": [],
                "semantic_examples": [],
                "confidence": 0.99,
                "enabled": True,
                "shortcut_allowed": False,
            }
        ],
    }

    (directory / "shadow_runtime_review_marker.rulepack.json").write_text(
        json.dumps(marker_pack, indent=2, sort_keys=True),
        encoding="utf-8",
    )


async def _run_review(runtime_extra_rule_dir: Path) -> dict[str, Any]:
    service = build_chat_service(
        Settings(
            app_env="test",
            trimatch_extra_mode="shadow",
            trimatch_extra_rule_dir=str(runtime_extra_rule_dir),
            trimatch_extra_fuzzy_enabled=False,
        )
    )

    turns: list[dict[str, Any]] = []
    extra_shadow_event_count = 0
    disagreement_event_count = 0

    for index, case in enumerate(REVIEW_CASES, start=1):
        response = await service.handle_turn(ChatTurnRequest(message=case.message))
        events = service.threads[response.thread_id].events
        event_types = [str(event.get("event_type")) for event in events]

        extra_shadow_event_count += event_types.count("trimatch.extra_shadow_voted")
        disagreement_event_count += event_types.count("trimatch.disagreement_observed")

        final_query = _enum_value(getattr(response.intent, "query_primary", None))
        final_service = _enum_value(getattr(response.intent, "service_primary", None))
        shadow_payload = _last_event_payload(events, "trimatch.extra_shadow_voted")
        shadow_service = (
            str(shadow_payload.get("service_primary"))
            if isinstance(shadow_payload, dict)
            and shadow_payload.get("service_primary") is not None
            else None
        )

        findings = _findings(
            case=case,
            event_types=event_types,
            final_query=final_query,
            final_service=final_service,
            shadow_service=shadow_service,
            bubble_count=len(response.bubbles),
        )

        turns.append(
            {
                "index": index,
                "name": case.name,
                "message": case.message,
                "passed": not findings,
                "findings": findings,
                "actual": {
                    "final_query": final_query,
                    "final_service": final_service,
                    "shadow_service": shadow_service,
                    "event_types": event_types,
                    "bubble_count": len(response.bubbles),
                },
                "expected": {
                    "final_query": case.expected_final_query,
                    "final_service": case.expected_final_service,
                    "shadow_service": case.expected_shadow_service,
                    "disallowed_final_services": list(case.disallowed_final_services),
                },
            }
        )

    failed_turns = sum(1 for turn in turns if not turn["passed"])
    passed_turns = len(turns) - failed_turns

    recommendation = (
        "shadow_runtime_review_passed"
        if failed_turns == 0 and extra_shadow_event_count == len(turns)
        else "continue_shadow_runtime_review"
    )

    summary = {
        "valid": failed_turns == 0 and extra_shadow_event_count == len(turns),
        "generated_at": datetime.now(UTC).isoformat(),
        "total_turns": len(turns),
        "passed_turns": passed_turns,
        "failed_turns": failed_turns,
        "extra_shadow_event_count": extra_shadow_event_count,
        "disagreement_event_count": disagreement_event_count,
        "recommendation": recommendation,
    }

    return {
        "schema_version": 1,
        "summary": summary,
        "turns": turns,
        "safety_note": (
            "This review is observational only. It does not activate Rules Army v2 "
            "or approved candidate RulePacks in production."
        ),
    }


def _findings(
    *,
    case: ReviewCase,
    event_types: list[str],
    final_query: str | None,
    final_service: str | None,
    shadow_service: str | None,
    bubble_count: int,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    if bubble_count <= 0:
        findings.append({"type": "missing_response_bubble"})

    if "trimatch.extra_shadow_voted" not in event_types:
        findings.append({"type": "missing_extra_shadow_vote_event"})

    if case.expected_final_query is not None and final_query != case.expected_final_query:
        findings.append(
            {
                "type": "unexpected_final_query",
                "expected": case.expected_final_query,
                "actual": final_query,
            }
        )

    if case.expected_final_service is not None and final_service != case.expected_final_service:
        findings.append(
            {
                "type": "unexpected_final_service",
                "expected": case.expected_final_service,
                "actual": final_service,
            }
        )

    if case.expected_shadow_service is not None and shadow_service != case.expected_shadow_service:
        findings.append(
            {
                "type": "unexpected_shadow_service",
                "expected": case.expected_shadow_service,
                "actual": shadow_service,
            }
        )

    if final_service in case.disallowed_final_services:
        findings.append(
            {
                "type": "disallowed_final_service_detected",
                "actual": final_service,
                "disallowed": list(case.disallowed_final_services),
            }
        )

    return findings


def _last_event_payload(
    events: list[dict[str, object]],
    event_type: str,
) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.get("event_type") == event_type:
            payload = event.get("payload")
            return payload if isinstance(payload, dict) else None
    return None


def _enum_value(value: object | None) -> str | None:
    if value is None:
        return None
    enum_value = getattr(value, "value", None)
    return str(enum_value if enum_value is not None else value)


def _markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Tri-Match Shadow Runtime Review",
        "",
        "## Summary",
        "",
        f"- Generated at: `{summary['generated_at']}`",
        f"- Valid: `{summary['valid']}`",
        f"- Total turns: `{summary['total_turns']}`",
        f"- Passed turns: `{summary['passed_turns']}`",
        f"- Failed turns: `{summary['failed_turns']}`",
        f"- Extra shadow events: `{summary['extra_shadow_event_count']}`",
        f"- Disagreement events: `{summary['disagreement_event_count']}`",
        f"- Recommendation: `{summary['recommendation']}`",
        "",
        "## Cases",
        "",
        "| # | Case | Passed | Final Query | Final Service | Shadow Service | Findings |",
        "|---:|---|---:|---|---|---|---|",
    ]

    for turn in report["turns"]:
        findings = turn["findings"]
        finding_text = "; ".join(str(item.get("type")) for item in findings) or "none"
        actual = turn["actual"]
        lines.append(
            (
                "| {index} | `{name}` | `{passed}` | `{query}` | "
                "`{service}` | `{shadow}` | {findings} |"
            ).format(
                index=turn["index"],
                name=turn["name"],
                passed=turn["passed"],
                query=actual["final_query"],
                service=actual["final_service"],
                shadow=actual["shadow_service"],
                findings=finding_text,
            )
        )

    lines.extend(
        [
            "",
            "## Safety Note",
            "",
            str(report["safety_note"]),
            "",
        ]
    )

    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
