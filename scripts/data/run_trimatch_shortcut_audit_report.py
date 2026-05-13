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
class ShortcutAuditCase:
    name: str
    message: str
    expected_extra_query: str | None = None
    expected_extra_service: str | None = None
    expected_final_query: str | None = None
    expected_final_service: str | None = None
    expected_pricing_sensitive: bool = False
    expected_document_sensitive: bool = False
    expected_portfolio_sensitive: bool = False


AUDIT_CASES: tuple[ShortcutAuditCase, ...] = (
    ShortcutAuditCase(
        name="service_shortcut_considered_not_applied",
        message="I need help with my book. rare shortcut editing marker",
        expected_extra_service="editing_proofreading",
    ),
    ShortcutAuditCase(
        name="pricing_shortcut_blocked",
        message="rare shortcut numbers marker. What does BookCraft do for authors?",
        expected_extra_query="pricing_question",
        expected_final_query="service_question",
        expected_pricing_sensitive=True,
    ),
    ShortcutAuditCase(
        name="agreement_shortcut_blocked",
        message="rare shortcut alpha marker. What does BookCraft do for authors?",
        expected_extra_query="agreement_request",
        expected_final_query="service_question",
        expected_document_sensitive=True,
    ),
    ShortcutAuditCase(
        name="portfolio_shortcut_blocked",
        message="rare shortcut gallery marker. What does BookCraft do for authors?",
        expected_extra_query="portfolio_request",
        expected_final_query="service_question",
        expected_portfolio_sensitive=True,
    ),
    ShortcutAuditCase(
        name="no_shortcut_recommendation_still_considered",
        message="What does BookCraft do for authors?",
        expected_final_query="service_question",
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a Tri-Match shortcut-candidate audit report.")
    parser.add_argument("--output-dir", default="reports/trimatch")
    parser.add_argument(
        "--runtime-extra-rule-dir",
        default="reports/trimatch/shortcut_runtime_extra_rules",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    runtime_extra_rule_dir = Path(args.runtime_extra_rule_dir)
    _prepare_runtime_extra_rules(runtime_extra_rule_dir)

    report = asyncio.run(_run_audit(runtime_extra_rule_dir))

    json_path = output_dir / "trimatch_shortcut_audit_report.json"
    md_path = output_dir / "trimatch_shortcut_audit_report.md"

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
        "version": "shortcut_audit_marker.v1",
        "rules": [
            _exact_rule(
                rule_id="shortcut_audit_editing_marker",
                phrase="rare shortcut editing marker",
                service_intent="editing_proofreading",
            ),
            _exact_rule(
                rule_id="shortcut_audit_pricing_marker",
                phrase="rare shortcut numbers marker",
                query_intent="pricing_question",
            ),
            _exact_rule(
                rule_id="shortcut_audit_agreement_marker",
                phrase="rare shortcut alpha marker",
                query_intent="agreement_request",
            ),
            _exact_rule(
                rule_id="shortcut_audit_portfolio_marker",
                phrase="rare shortcut gallery marker",
                query_intent="portfolio_request",
            ),
        ],
    }

    (runtime_dir / "shortcut_audit_marker.rulepack.json").write_text(
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
        "shortcut_allowed": True,
    }


async def _run_audit(runtime_extra_rule_dir: Path) -> dict[str, Any]:
    service = build_chat_service(
        Settings(
            app_env="test",
            trimatch_extra_mode="shortcut_candidate",
            trimatch_extra_rule_dir=str(runtime_extra_rule_dir),
            trimatch_extra_fuzzy_enabled=False,
        )
    )

    turns: list[dict[str, Any]] = []

    for index, case in enumerate(AUDIT_CASES, start=1):
        response = await service.handle_turn(ChatTurnRequest(message=case.message))
        events = service.threads[response.thread_id].events
        event_types = [str(event.get("event_type")) for event in events]
        payload = _last_event_payload(events, "trimatch.extra_shortcut_considered")

        actual = _actual_snapshot(response=response, shortcut_payload=payload)
        findings = _findings(
            case=case,
            actual=actual,
            event_types=event_types,
            shortcut_payload=payload,
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
            "This shortcut audit is observational only. It verifies that "
            "shortcut_candidate mode logs consideration events with applied=false "
            "and side_effects_allowed=false."
        ),
    }


def _actual_snapshot(
    *,
    response: Any,
    shortcut_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    intent = response.intent
    extra = shortcut_payload.get("extra_shortcut") if isinstance(shortcut_payload, dict) else None
    shortcut = shortcut_payload.get("shortcut") if isinstance(shortcut_payload, dict) else None
    safety = shortcut_payload.get("safety") if isinstance(shortcut_payload, dict) else None

    extra_snapshot = extra if isinstance(extra, dict) else {}
    shortcut_snapshot = shortcut if isinstance(shortcut, dict) else {}
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
        "shortcut_eligible": bool(shortcut_snapshot.get("eligible")),
        "shortcut_applied": bool(shortcut_snapshot.get("applied")),
        "shortcut_dimension": shortcut_snapshot.get("dimension"),
        "shortcut_recommended_value": shortcut_snapshot.get("recommended_value"),
        "shortcut_rule_id": shortcut_snapshot.get("rule_id"),
        "shortcut_reason": shortcut_snapshot.get("reason"),
        "blocked_reasons": _string_list(shortcut_snapshot.get("blocked_reasons")),
        "pricing_sensitive": bool(safety_snapshot.get("pricing_sensitive")),
        "document_sensitive": bool(safety_snapshot.get("document_sensitive")),
        "portfolio_sensitive": bool(safety_snapshot.get("portfolio_sensitive")),
        "negated": bool(safety_snapshot.get("negated")),
        "counterfactual": bool(safety_snapshot.get("counterfactual")),
        "side_effects_allowed": bool(safety_snapshot.get("side_effects_allowed")),
    }


def _findings(
    *,
    case: ShortcutAuditCase,
    actual: dict[str, Any],
    event_types: list[str],
    shortcut_payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    if "trimatch.extra_shortcut_considered" not in event_types:
        findings.append({"type": "missing_shortcut_considered_event"})

    if "trimatch.extra_tiebreaker_considered" in event_types:
        findings.append({"type": "tiebreaker_event_logged_in_shortcut_mode"})

    if "trimatch.extra_advisory_recommended" in event_types:
        findings.append({"type": "advisory_event_logged_in_shortcut_mode"})

    if "trimatch.extra_shadow_voted" in event_types:
        findings.append({"type": "shadow_event_logged_in_shortcut_mode"})

    if shortcut_payload is None:
        findings.append({"type": "missing_shortcut_payload"})
        return findings

    if actual["shortcut_applied"] is not False:
        findings.append({"type": "shortcut_applied_not_false"})

    if actual["shortcut_applied"] is True and actual["shortcut_dimension"] is None:
        findings.append({"type": "applied_shortcut_missing_dimension"})

    if actual["shortcut_applied"] is True and actual["shortcut_recommended_value"] is None:
        findings.append({"type": "applied_shortcut_missing_recommended_value"})

    if actual["shortcut_applied"] is True and actual["shortcut_rule_id"] is None:
        findings.append({"type": "applied_shortcut_missing_rule_id"})

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
    eligible_count = sum(1 for turn in turns if bool(turn["actual"]["shortcut_eligible"]))
    applied_count = sum(1 for turn in turns if bool(turn["actual"]["shortcut_applied"]))
    eligible_not_applied_count = sum(
        1
        for turn in turns
        if bool(turn["actual"]["shortcut_eligible"])
        and not bool(turn["actual"]["shortcut_applied"])
    )
    side_effects_allowed_count = sum(
        1 for turn in turns if bool(turn["actual"]["side_effects_allowed"])
    )
    sensitive_block_count = sum(
        1
        for turn in turns
        if (
            bool(turn["actual"]["pricing_sensitive"])
            or bool(turn["actual"]["document_sensitive"])
            or bool(turn["actual"]["portfolio_sensitive"])
        )
        and not bool(turn["actual"]["shortcut_applied"])
    )
    blocked_reason_counts = _blocked_reason_counts(turns)

    return {
        "valid": failed_turns == 0,
        "generated_at": datetime.now(UTC).isoformat(),
        "total_turns": len(turns),
        "passed_turns": len(turns) - failed_turns,
        "failed_turns": failed_turns,
        "shortcut_event_count": len(turns),
        "eligible_count": eligible_count,
        "eligible_not_applied_count": eligible_not_applied_count,
        "applied_count": applied_count,
        "side_effects_allowed_count": side_effects_allowed_count,
        "sensitive_block_count": sensitive_block_count,
        "applied_dimension_counts": _applied_shortcut_field_counts(
            turns,
            "shortcut_dimension",
        ),
        "applied_value_counts": _applied_shortcut_field_counts(
            turns,
            "shortcut_recommended_value",
        ),
        "applied_rule_id_counts": _applied_shortcut_field_counts(
            turns,
            "shortcut_rule_id",
        ),
        "pricing_sensitive_count": sum(
            1 for turn in turns if bool(turn["actual"]["pricing_sensitive"])
        ),
        "document_sensitive_count": sum(
            1 for turn in turns if bool(turn["actual"]["document_sensitive"])
        ),
        "portfolio_sensitive_count": sum(
            1 for turn in turns if bool(turn["actual"]["portfolio_sensitive"])
        ),
        "blocked_reason_counts": blocked_reason_counts,
        "top_blocked_reasons": dict(
            sorted(blocked_reason_counts.items(), key=lambda item: (-item[1], item[0]))[:10]
        ),
        "recommendation": (
            "shortcut_audit_passed" if failed_turns == 0 else "continue_shortcut_review"
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


def _blocked_reason_counts(turns: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for turn in turns:
        for reason in turn["actual"].get("blocked_reasons", []):
            if not isinstance(reason, str):
                continue
            counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def _applied_shortcut_field_counts(
    turns: list[dict[str, Any]],
    field: str,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for turn in turns:
        if not bool(turn["actual"]["shortcut_applied"]):
            continue
        value = turn["actual"].get(field)
        if not isinstance(value, str):
            continue
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Tri-Match Shortcut Audit Report",
        "",
        "## Summary",
        "",
        f"- Generated at: `{summary['generated_at']}`",
        f"- Valid: `{summary['valid']}`",
        f"- Total turns: `{summary['total_turns']}`",
        f"- Passed turns: `{summary['passed_turns']}`",
        f"- Failed turns: `{summary['failed_turns']}`",
        f"- Shortcut events: `{summary['shortcut_event_count']}`",
        f"- Eligible count: `{summary['eligible_count']}`",
        f"- Eligible not applied count: `{summary['eligible_not_applied_count']}`",
        f"- Applied count: `{summary['applied_count']}`",
        f"- Side effects allowed count: `{summary['side_effects_allowed_count']}`",
        f"- Sensitive block count: `{summary['sensitive_block_count']}`",
        f"- Applied dimensions: `{summary['applied_dimension_counts']}`",
        f"- Applied values: `{summary['applied_value_counts']}`",
        f"- Applied rule IDs: `{summary['applied_rule_id_counts']}`",
        f"- Blocked reasons: `{len(summary['blocked_reason_counts'])}`",
        f"- Pricing-sensitive count: `{summary['pricing_sensitive_count']}`",
        f"- Document-sensitive count: `{summary['document_sensitive_count']}`",
        f"- Portfolio-sensitive count: `{summary['portfolio_sensitive_count']}`",
        f"- Recommendation: `{summary['recommendation']}`",
        "",
        "## Top Blocked Reasons",
        "",
        "```json",
        json.dumps(summary["top_blocked_reasons"], indent=2, sort_keys=True),
        "```",
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
