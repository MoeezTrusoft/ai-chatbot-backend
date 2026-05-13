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
class TiebreakerAuditCase:
    name: str
    message: str
    expected_extra_query: str | None = None
    expected_extra_service: str | None = None
    expected_final_query: str | None = None
    expected_final_service: str | None = None
    expected_pricing_sensitive: bool = False
    expected_document_sensitive: bool = False
    expected_portfolio_sensitive: bool = False


AUDIT_CASES: tuple[TiebreakerAuditCase, ...] = (
    TiebreakerAuditCase(
        name="service_recommendation_considered_not_applied",
        message=("I need proofreading help for my manuscript. rare tiebreaker video marker"),
        expected_extra_service="video_trailer",
        expected_final_service="editing_proofreading",
    ),
    TiebreakerAuditCase(
        name="pricing_recommendation_blocked",
        message="rare tiebreaker numbers marker. What does BookCraft do for authors?",
        expected_extra_query="pricing_question",
        expected_final_query="service_question",
        expected_pricing_sensitive=True,
    ),
    TiebreakerAuditCase(
        name="agreement_recommendation_blocked",
        message="rare tiebreaker alpha marker. What does BookCraft do for authors?",
        expected_extra_query="agreement_request",
        expected_final_query="service_question",
        expected_document_sensitive=True,
    ),
    TiebreakerAuditCase(
        name="portfolio_recommendation_blocked",
        message="rare tiebreaker gallery marker. What does BookCraft do for authors?",
        expected_extra_query="portfolio_request",
        expected_final_query="service_question",
        expected_portfolio_sensitive=True,
    ),
    TiebreakerAuditCase(
        name="no_recommendation_still_considered",
        message="What does BookCraft do for authors?",
        expected_final_query="service_question",
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a Tri-Match tiebreaker-candidate audit report."
    )
    parser.add_argument("--output-dir", default="reports/trimatch")
    parser.add_argument(
        "--runtime-extra-rule-dir",
        default="reports/trimatch/tiebreaker_runtime_extra_rules",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    runtime_extra_rule_dir = Path(args.runtime_extra_rule_dir)
    _prepare_runtime_extra_rules(runtime_extra_rule_dir)

    report = asyncio.run(_run_audit(runtime_extra_rule_dir))

    json_path = output_dir / "trimatch_tiebreaker_audit_report.json"
    md_path = output_dir / "trimatch_tiebreaker_audit_report.md"

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
        "version": "tiebreaker_audit_marker.v1",
        "rules": [
            _exact_rule(
                rule_id="tiebreaker_audit_video_marker",
                phrase="rare tiebreaker video marker",
                service_intent="video_trailer",
            ),
            _exact_rule(
                rule_id="tiebreaker_audit_pricing_marker",
                phrase="rare tiebreaker numbers marker",
                query_intent="pricing_question",
            ),
            _exact_rule(
                rule_id="tiebreaker_audit_agreement_marker",
                phrase="rare tiebreaker alpha marker",
                query_intent="agreement_request",
            ),
            _exact_rule(
                rule_id="tiebreaker_audit_portfolio_marker",
                phrase="rare tiebreaker gallery marker",
                query_intent="portfolio_request",
            ),
        ],
    }

    (runtime_dir / "tiebreaker_audit_marker.rulepack.json").write_text(
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
            trimatch_extra_mode="tiebreaker_candidate",
            trimatch_extra_rule_dir=str(runtime_extra_rule_dir),
            trimatch_extra_fuzzy_enabled=False,
        )
    )

    turns: list[dict[str, Any]] = []

    for index, case in enumerate(AUDIT_CASES, start=1):
        response = await service.handle_turn(ChatTurnRequest(message=case.message))
        events = service.threads[response.thread_id].events
        event_types = [str(event.get("event_type")) for event in events]
        payload = _last_event_payload(events, "trimatch.extra_tiebreaker_considered")

        actual = _actual_snapshot(response=response, tiebreaker_payload=payload)
        findings = _findings(
            case=case,
            actual=actual,
            event_types=event_types,
            tiebreaker_payload=payload,
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
                    "extra_query": case.expected_extra_query,
                    "extra_service": case.expected_extra_service,
                    "final_query": case.expected_final_query,
                    "final_service": case.expected_final_service,
                    "pricing_sensitive": case.expected_pricing_sensitive,
                    "document_sensitive": case.expected_document_sensitive,
                    "portfolio_sensitive": case.expected_portfolio_sensitive,
                },
            }
        )

    summary = _summary(turns)
    return {
        "schema_version": 1,
        "summary": summary,
        "turns": turns,
        "safety_note": (
            "This tiebreaker audit is observational only. It verifies that "
            "tiebreaker_candidate mode logs consideration events with applied=false "
            "and side_effects_allowed=false."
        ),
    }


def _actual_snapshot(
    *,
    response: Any,
    tiebreaker_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    intent = response.intent
    extra = (
        tiebreaker_payload.get("extra_tiebreaker") if isinstance(tiebreaker_payload, dict) else None
    )
    decision = tiebreaker_payload.get("decision") if isinstance(tiebreaker_payload, dict) else None
    safety = tiebreaker_payload.get("safety") if isinstance(tiebreaker_payload, dict) else None

    extra_snapshot = extra if isinstance(extra, dict) else {}
    decision_snapshot = decision if isinstance(decision, dict) else {}
    safety_snapshot = safety if isinstance(safety, dict) else {}

    return {
        "final_query": intent.query_primary.value if intent else None,
        "final_service": (
            intent.service_primary.value if intent and intent.service_primary else None
        ),
        "final_funnel_stage": intent.funnel_stage.value if intent else None,
        "extra_query": _string_or_none(extra_snapshot.get("query_primary")),
        "extra_service": _string_or_none(extra_snapshot.get("service_primary")),
        "extra_funnel_stage": _string_or_none(extra_snapshot.get("funnel_stage")),
        "decision_eligible": bool(decision_snapshot.get("eligible")),
        "decision_applied": bool(decision_snapshot.get("applied")),
        "decision_dimension": decision_snapshot.get("dimension"),
        "decision_recommended_value": decision_snapshot.get("recommended_value"),
        "pricing_sensitive": bool(safety_snapshot.get("pricing_sensitive")),
        "document_sensitive": bool(safety_snapshot.get("document_sensitive")),
        "portfolio_sensitive": bool(safety_snapshot.get("portfolio_sensitive")),
        "negated": bool(safety_snapshot.get("negated")),
        "counterfactual": bool(safety_snapshot.get("counterfactual")),
        "side_effects_allowed": bool(safety_snapshot.get("side_effects_allowed")),
    }


def _findings(
    *,
    case: TiebreakerAuditCase,
    actual: dict[str, Any],
    event_types: list[str],
    tiebreaker_payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    if "trimatch.extra_tiebreaker_considered" not in event_types:
        findings.append({"type": "missing_tiebreaker_considered_event"})

    if "trimatch.extra_advisory_recommended" in event_types:
        findings.append({"type": "advisory_event_logged_in_tiebreaker_mode"})

    if "trimatch.extra_shadow_voted" in event_types:
        findings.append({"type": "shadow_event_logged_in_tiebreaker_mode"})

    if tiebreaker_payload is None:
        findings.append({"type": "missing_tiebreaker_payload"})
        return findings

    if actual["decision_eligible"] is not False:
        findings.append({"type": "tiebreaker_eligible_not_false"})

    if actual["decision_applied"] is not False:
        findings.append({"type": "tiebreaker_applied_not_false"})

    if actual["decision_dimension"] is not None:
        findings.append({"type": "tiebreaker_dimension_not_none"})

    if actual["decision_recommended_value"] is not None:
        findings.append({"type": "tiebreaker_recommended_value_not_none"})

    if actual["side_effects_allowed"] is not False:
        findings.append({"type": "side_effects_allowed_not_false"})

    _expect(findings, "extra_query", case.expected_extra_query, actual["extra_query"])
    _expect(
        findings,
        "extra_service",
        case.expected_extra_service,
        actual["extra_service"],
    )
    _expect(findings, "final_query", case.expected_final_query, actual["final_query"])
    _expect(
        findings,
        "final_service",
        case.expected_final_service,
        actual["final_service"],
    )
    _expect_bool(
        findings,
        "pricing_sensitive",
        case.expected_pricing_sensitive,
        actual["pricing_sensitive"],
    )
    _expect_bool(
        findings,
        "document_sensitive",
        case.expected_document_sensitive,
        actual["document_sensitive"],
    )
    _expect_bool(
        findings,
        "portfolio_sensitive",
        case.expected_portfolio_sensitive,
        actual["portfolio_sensitive"],
    )

    return findings


def _expect(
    findings: list[dict[str, Any]],
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


def _expect_bool(
    findings: list[dict[str, Any]],
    name: str,
    expected: bool,
    actual: bool,
) -> None:
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
    applied_count = sum(1 for turn in turns if bool(turn["actual"]["decision_applied"]))
    eligible_count = sum(1 for turn in turns if bool(turn["actual"]["decision_eligible"]))
    side_effects_allowed_count = sum(
        1 for turn in turns if bool(turn["actual"]["side_effects_allowed"])
    )

    return {
        "valid": failed_turns == 0,
        "generated_at": datetime.now(UTC).isoformat(),
        "total_turns": len(turns),
        "passed_turns": len(turns) - failed_turns,
        "failed_turns": failed_turns,
        "tiebreaker_event_count": len(turns),
        "eligible_count": eligible_count,
        "applied_count": applied_count,
        "side_effects_allowed_count": side_effects_allowed_count,
        "pricing_sensitive_count": sum(
            1 for turn in turns if bool(turn["actual"]["pricing_sensitive"])
        ),
        "document_sensitive_count": sum(
            1 for turn in turns if bool(turn["actual"]["document_sensitive"])
        ),
        "portfolio_sensitive_count": sum(
            1 for turn in turns if bool(turn["actual"]["portfolio_sensitive"])
        ),
        "recommendation": (
            "tiebreaker_audit_passed" if failed_turns == 0 else "continue_tiebreaker_review"
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


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Tri-Match Tiebreaker Audit Report",
        "",
        "## Summary",
        "",
        f"- Generated at: `{summary['generated_at']}`",
        f"- Valid: `{summary['valid']}`",
        f"- Total turns: `{summary['total_turns']}`",
        f"- Passed turns: `{summary['passed_turns']}`",
        f"- Failed turns: `{summary['failed_turns']}`",
        f"- Tiebreaker events: `{summary['tiebreaker_event_count']}`",
        f"- Eligible count: `{summary['eligible_count']}`",
        f"- Applied count: `{summary['applied_count']}`",
        f"- Side effects allowed count: `{summary['side_effects_allowed_count']}`",
        f"- Pricing-sensitive count: `{summary['pricing_sensitive_count']}`",
        f"- Document-sensitive count: `{summary['document_sensitive_count']}`",
        f"- Portfolio-sensitive count: `{summary['portfolio_sensitive_count']}`",
        f"- Recommendation: `{summary['recommendation']}`",
        "",
        "## Cases",
        "",
        "| # | Case | Passed | Extra | Final | Sensitive | Findings |",
        "|---:|---|---:|---|---|---|---|",
    ]

    for turn in report["turns"]:
        actual = turn["actual"]
        extra = "{query}/{service}/{stage}".format(
            query=actual["extra_query"],
            service=actual["extra_service"],
            stage=actual["extra_funnel_stage"],
        )
        final = "{query}/{service}/{stage}".format(
            query=actual["final_query"],
            service=actual["final_service"],
            stage=actual["final_funnel_stage"],
        )
        sensitive = "pricing={pricing}, document={document}, portfolio={portfolio}".format(
            pricing=actual["pricing_sensitive"],
            document=actual["document_sensitive"],
            portfolio=actual["portfolio_sensitive"],
        )
        findings = "; ".join(str(item.get("type")) for item in turn["findings"]) or "none"

        lines.append(
            "| {index} | `{name}` | `{passed}` | `{extra}` | `{final}` | "
            "{sensitive} | {findings} |".format(
                index=turn["index"],
                name=turn["name"],
                passed=turn["passed"],
                extra=extra,
                final=final,
                sensitive=sensitive,
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
