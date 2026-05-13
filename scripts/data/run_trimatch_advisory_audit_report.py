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

SENSITIVE_QUERY_INTENTS = {
    "pricing_question",
    "timeline_question",
    "portfolio_request",
    "nda_request",
    "agreement_request",
}

SENSITIVE_SERVICE_INTENTS = set[str]()


@dataclass(frozen=True)
class AdvisoryAuditCase:
    name: str
    message: str
    expected_advisory_query: str | None = None
    expected_advisory_service: str | None = None
    expected_final_query: str | None = None
    expected_final_service: str | None = None
    sensitive_expected: bool = False


AUDIT_CASES: tuple[AdvisoryAuditCase, ...] = (
    AdvisoryAuditCase(
        name="advisory_service_matches_final",
        message=(
            "I need proofreading help for my completed manuscript. rare advisory editing marker"
        ),
        expected_advisory_service="editing_proofreading",
        expected_final_service="editing_proofreading",
    ),
    AdvisoryAuditCase(
        name="advisory_service_differs_from_final",
        message="I need proofreading help for my manuscript. rare advisory video marker",
        expected_advisory_service="video_trailer",
        expected_final_service="editing_proofreading",
    ),
    AdvisoryAuditCase(
        name="advisory_pricing_query_sensitive",
        message="rare advisory numbers marker. What does BookCraft do for authors?",
        expected_advisory_query="pricing_question",
        expected_final_query="service_question",
        sensitive_expected=True,
    ),
    AdvisoryAuditCase(
        name="advisory_nda_query_sensitive",
        message="rare advisory alpha marker. What does BookCraft do for authors?",
        expected_advisory_query="nda_request",
        expected_final_query="service_question",
        sensitive_expected=True,
    ),
    AdvisoryAuditCase(
        name="advisory_agreement_query_sensitive",
        message="rare advisory paperwork marker. What does BookCraft do for authors?",
        expected_advisory_query="agreement_request",
        expected_final_query="service_question",
        sensitive_expected=True,
    ),
    AdvisoryAuditCase(
        name="advisory_no_recommendation",
        message="What does BookCraft do for authors?",
        expected_final_query="service_question",
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a Tri-Match advisory-mode audit report.")
    parser.add_argument(
        "--output-dir",
        default="reports/trimatch",
    )
    parser.add_argument(
        "--runtime-extra-rule-dir",
        default="reports/trimatch/advisory_runtime_extra_rules",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    runtime_extra_rule_dir = Path(args.runtime_extra_rule_dir)
    _prepare_runtime_extra_rules(runtime_extra_rule_dir)

    report = asyncio.run(_run_audit(runtime_extra_rule_dir))

    json_path = output_dir / "trimatch_advisory_audit_report.json"
    md_path = output_dir / "trimatch_advisory_audit_report.md"

    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
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

    marker_pack = {
        "version": "advisory_audit_marker.v1",
        "rules": [
            _exact_rule(
                rule_id="advisory_audit_editing_marker",
                phrase="rare advisory editing marker",
                service_intent="editing_proofreading",
            ),
            _exact_rule(
                rule_id="advisory_audit_video_marker",
                phrase="rare advisory video marker",
                service_intent="video_trailer",
            ),
            _exact_rule(
                rule_id="advisory_audit_pricing_marker",
                phrase="rare advisory numbers marker",
                query_intent="pricing_question",
            ),
            _exact_rule(
                rule_id="advisory_audit_nda_marker",
                phrase="rare advisory alpha marker",
                query_intent="nda_request",
            ),
            _exact_rule(
                rule_id="advisory_audit_agreement_marker",
                phrase="rare advisory paperwork marker",
                query_intent="agreement_request",
            ),
        ],
    }

    (runtime_dir / "advisory_audit_marker.rulepack.json").write_text(
        json.dumps(marker_pack, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _exact_rule(
    *,
    rule_id: str,
    phrase: str,
    service_intent: str | None = None,
    query_intent: str | None = None,
    funnel_stage: str | None = None,
) -> dict[str, Any]:
    return {
        "id": rule_id,
        "layer": "exact",
        "target": {
            "service_intent": service_intent,
            "query_intent": query_intent,
            "funnel_stage": funnel_stage,
        },
        "phrases": [phrase],
        "regex": None,
        "pattern": [],
        "semantic_examples": [],
        "confidence": 0.99,
        "enabled": True,
        "shortcut_allowed": False,
    }


async def _run_audit(runtime_extra_rule_dir: Path) -> dict[str, Any]:
    service = build_chat_service(
        Settings(
            app_env="test",
            trimatch_extra_mode="advisory",
            trimatch_extra_rule_dir=str(runtime_extra_rule_dir),
            trimatch_extra_fuzzy_enabled=False,
        )
    )

    turns: list[dict[str, Any]] = []

    for index, case in enumerate(AUDIT_CASES, start=1):
        response = await service.handle_turn(ChatTurnRequest(message=case.message))
        events = service.threads[response.thread_id].events

        advisory_payload = _last_event_payload(
            events,
            "trimatch.extra_advisory_recommended",
        )
        event_types = [str(event.get("event_type")) for event in events]

        actual = _actual_snapshot(response=response, advisory_payload=advisory_payload)
        findings = _findings(
            case=case,
            actual=actual,
            event_types=event_types,
            advisory_payload=advisory_payload,
        )

        turns.append(
            {
                "index": index,
                "name": case.name,
                "message": case.message,
                "passed": not findings,
                "findings": findings,
                "actual": actual,
                "expected": {
                    "advisory_query": case.expected_advisory_query,
                    "advisory_service": case.expected_advisory_service,
                    "final_query": case.expected_final_query,
                    "final_service": case.expected_final_service,
                    "sensitive": case.sensitive_expected,
                },
            }
        )

    summary = _summary(turns)
    return {
        "schema_version": 1,
        "summary": summary,
        "turns": turns,
        "safety_note": (
            "This advisory audit is observational only. It does not activate "
            "Rules Army v2, tiebreakers, shortcuts, pricing, documents, portfolio, "
            "RAG routing, or response generation changes."
        ),
    }


def _actual_snapshot(
    *,
    response: Any,
    advisory_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    intent = response.intent
    extra = advisory_payload.get("extra_advisory") if isinstance(advisory_payload, dict) else None
    recommendation = (
        advisory_payload.get("recommendation") if isinstance(advisory_payload, dict) else None
    )

    extra_snapshot = extra if isinstance(extra, dict) else {}
    recommendation_snapshot = recommendation if isinstance(recommendation, dict) else {}

    advisory_query = _string_or_none(extra_snapshot.get("query_primary"))
    advisory_service = _string_or_none(extra_snapshot.get("service_primary"))
    advisory_funnel = _string_or_none(extra_snapshot.get("funnel_stage"))

    return {
        "final_query": intent.query_primary.value if intent else None,
        "final_service": (
            intent.service_primary.value if intent and intent.service_primary else None
        ),
        "final_funnel_stage": intent.funnel_stage.value if intent else None,
        "advisory_query": advisory_query,
        "advisory_service": advisory_service,
        "advisory_funnel_stage": advisory_funnel,
        "recommendation_dimension": recommendation_snapshot.get("dimension"),
        "recommendation_value": recommendation_snapshot.get("recommended_value"),
        "matches_final": bool(recommendation_snapshot.get("matches_final")),
        "advisory_applied": (
            advisory_payload.get("advisory_applied") if isinstance(advisory_payload, dict) else None
        ),
        "side_effects_allowed": (
            advisory_payload.get("side_effects_allowed")
            if isinstance(advisory_payload, dict)
            else None
        ),
        "is_sensitive_advisory": _is_sensitive_advisory(
            query=advisory_query,
            service=advisory_service,
        ),
    }


def _findings(
    *,
    case: AdvisoryAuditCase,
    actual: dict[str, Any],
    event_types: list[str],
    advisory_payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    if "trimatch.extra_advisory_recommended" not in event_types:
        findings.append({"type": "missing_advisory_event"})

    if "trimatch.extra_shadow_voted" in event_types:
        findings.append({"type": "shadow_event_logged_in_advisory_mode"})

    if advisory_payload is None:
        findings.append({"type": "missing_advisory_payload"})
        return findings

    if advisory_payload.get("advisory_applied") is not False:
        findings.append({"type": "advisory_applied_not_false"})

    if advisory_payload.get("side_effects_allowed") is not False:
        findings.append({"type": "side_effects_allowed_not_false"})

    _expect(
        findings,
        name="advisory_query",
        expected=case.expected_advisory_query,
        actual=actual["advisory_query"],
    )
    _expect(
        findings,
        name="advisory_service",
        expected=case.expected_advisory_service,
        actual=actual["advisory_service"],
    )
    _expect(
        findings,
        name="final_query",
        expected=case.expected_final_query,
        actual=actual["final_query"],
    )
    _expect(
        findings,
        name="final_service",
        expected=case.expected_final_service,
        actual=actual["final_service"],
    )

    if actual["is_sensitive_advisory"] != case.sensitive_expected:
        findings.append(
            {
                "type": "unexpected_sensitive_advisory_flag",
                "expected": case.sensitive_expected,
                "actual": actual["is_sensitive_advisory"],
            }
        )

    return findings


def _expect(
    findings: list[dict[str, Any]],
    *,
    name: str,
    expected: str | None,
    actual: str | None,
) -> None:
    if expected is None:
        return
    if actual != expected:
        findings.append(
            {
                "type": f"unexpected_{name}",
                "expected": expected,
                "actual": actual,
            }
        )


def _summary(turns: list[dict[str, Any]]) -> dict[str, Any]:
    failed_turns = sum(1 for turn in turns if not turn["passed"])
    advisory_event_count = len(turns)
    sensitive_count = sum(1 for turn in turns if bool(turn["actual"]["is_sensitive_advisory"]))
    matches_final_count = sum(1 for turn in turns if bool(turn["actual"]["matches_final"]))
    differs_from_final_count = sum(
        1
        for turn in turns
        if turn["actual"]["recommendation_value"] is not None
        and not bool(turn["actual"]["matches_final"])
    )

    return {
        "valid": failed_turns == 0,
        "generated_at": datetime.now(UTC).isoformat(),
        "total_turns": len(turns),
        "passed_turns": len(turns) - failed_turns,
        "failed_turns": failed_turns,
        "advisory_event_count": advisory_event_count,
        "matches_final_count": matches_final_count,
        "differs_from_final_count": differs_from_final_count,
        "sensitive_advisory_count": sensitive_count,
        "recommendation": (
            "advisory_audit_passed" if failed_turns == 0 else "continue_advisory_review"
        ),
    }


def _last_event_payload(
    events: list[dict[str, object]],
    event_type: str,
) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.get("event_type") == event_type:
            payload = event.get("payload")
            return payload if isinstance(payload, dict) else None
    return None


def _is_sensitive_advisory(
    *,
    query: str | None,
    service: str | None,
) -> bool:
    return query in SENSITIVE_QUERY_INTENTS or service in SENSITIVE_SERVICE_INTENTS


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Tri-Match Advisory Audit Report",
        "",
        "## Summary",
        "",
        f"- Generated at: `{summary['generated_at']}`",
        f"- Valid: `{summary['valid']}`",
        f"- Total turns: `{summary['total_turns']}`",
        f"- Passed turns: `{summary['passed_turns']}`",
        f"- Failed turns: `{summary['failed_turns']}`",
        f"- Advisory events: `{summary['advisory_event_count']}`",
        f"- Matches final: `{summary['matches_final_count']}`",
        f"- Differs from final: `{summary['differs_from_final_count']}`",
        f"- Sensitive advisory recommendations: `{summary['sensitive_advisory_count']}`",
        f"- Recommendation: `{summary['recommendation']}`",
        "",
        "## Cases",
        "",
        "| # | Case | Passed | Advisory | Final | Sensitive | Findings |",
        "|---:|---|---:|---|---|---:|---|",
    ]

    for turn in report["turns"]:
        actual = turn["actual"]
        advisory = "{query}/{service}/{stage}".format(
            query=actual["advisory_query"],
            service=actual["advisory_service"],
            stage=actual["advisory_funnel_stage"],
        )
        final = "{query}/{service}/{stage}".format(
            query=actual["final_query"],
            service=actual["final_service"],
            stage=actual["final_funnel_stage"],
        )
        findings = "; ".join(str(item.get("type")) for item in turn["findings"]) or "none"

        lines.append(
            "| {index} | `{name}` | `{passed}` | `{advisory}` | "
            "`{final}` | `{sensitive}` | {findings} |".format(
                index=turn["index"],
                name=turn["name"],
                passed=turn["passed"],
                advisory=advisory,
                final=final,
                sensitive=actual["is_sensitive_advisory"],
                findings=findings,
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
