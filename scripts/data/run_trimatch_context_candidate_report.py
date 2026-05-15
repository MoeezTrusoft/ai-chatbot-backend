from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from bookcraft.components.preprocessor.schemas import ProcessedMessage, Span, TokenInfo
from bookcraft.components.trimatch import RuleRepository, TriMatchEngine, TriMatchMode
from bookcraft.domain.enums import QueryIntentType, ServiceCategory

DEFAULT_RULE_DIR = Path("data/trimatch/candidates/rules_army_v2_filtered/rules")
DEFAULT_ADVANCED_EVAL = Path(
    "data/trimatch/staged/rules_army_v2/eval_advanced/"
    "context_eval.requires_engine_upgrade.jsonl"
)
DEFAULT_REPORT_DIR = Path("reports/trimatch")


SERVICE_ALIASES: dict[str, ServiceCategory] = {
    "ghostwriting": ServiceCategory.GHOSTWRITING,
    "proofreading": ServiceCategory.EDITING_PROOFREADING,
    "editing": ServiceCategory.EDITING_PROOFREADING,
    "formatting": ServiceCategory.INTERIOR_FORMATTING,
    "interior formatting": ServiceCategory.INTERIOR_FORMATTING,
    "publishing": ServiceCategory.PUBLISHING_DISTRIBUTION,
    "marketing": ServiceCategory.MARKETING_PROMOTION,
    "book trailer": ServiceCategory.VIDEO_TRAILER,
    "video trailer": ServiceCategory.VIDEO_TRAILER,
    "audiobook": ServiceCategory.AUDIOBOOK_PRODUCTION,
}


QUERY_ALIASES: dict[str, QueryIntentType] = {
    "agreement": QueryIntentType.AGREEMENT_REQUEST,
    "service agreement": QueryIntentType.AGREEMENT_REQUEST,
    "pricing": QueryIntentType.PRICING_QUESTION,
    "price": QueryIntentType.PRICING_QUESTION,
    "portfolio": QueryIntentType.PORTFOLIO_REQUEST,
    "sample": QueryIntentType.PORTFOLIO_REQUEST,
    "services": QueryIntentType.SERVICE_QUESTION,
    "what file types": QueryIntentType.SERVICE_QUESTION,
}


def main() -> int:
    rule_dir = DEFAULT_RULE_DIR
    eval_path = DEFAULT_ADVANCED_EVAL
    report_dir = DEFAULT_REPORT_DIR
    report_dir.mkdir(parents=True, exist_ok=True)

    engine = TriMatchEngine(
        rule_pack=RuleRepository(rule_dir).load_active_rules(),
        mode=TriMatchMode.SHADOW,
        funnel_stage_weight=0.5,
    )

    examples = _load_jsonl(eval_path)
    rows: list[dict[str, Any]] = []

    for index, example in enumerate(examples, start=1):
        text = example["text"]
        expected = example.get("expected", {})
        processed = _processed_with_basic_context(text)
        result = engine.classify(processed)

        actual = {
            "query_primary": result.query_primary.value if result.query_primary else None,
            "service_primary": result.service_primary.value if result.service_primary else None,
            "service_secondary": [item.value for item in result.service_secondary],
            "negated_services": _negated_services(processed),
        }

        checks = _evaluate_expected(expected, actual)
        rows.append(
            {
                "index": index,
                "subset": example.get("subset", "default"),
                "text": text,
                "expected": expected,
                "actual": actual,
                "passed": all(item["passed"] for item in checks),
                "checks": checks,
                "evidence": [
                    {
                        "rule_id": item.rule_id,
                        "dimension": item.dimension.value,
                        "target": item.target,
                        "layer": item.layer.value,
                        "matched_text": item.matched_text,
                        "confidence": item.confidence,
                        "negated": item.negated,
                        "hedged": item.hedged,
                        "counterfactual": item.counterfactual,
                    }
                    for item in result.evidence
                ],
            }
        )

    summary = {
        "rule_dir": str(rule_dir),
        "eval_path": str(eval_path),
        "example_count": len(rows),
        "passed_count": sum(1 for row in rows if row["passed"]),
        "failed_count": sum(1 for row in rows if not row["passed"]),
        "valid_for_active_promotion": False,
        "note": (
            "Advanced context candidate report only. This does not activate "
            "Rules Army v2 and does not replace verify_trimatch_rules.py."
        ),
    }

    report = {"summary": summary, "rows": rows}

    json_path = report_dir / "trimatch_context_candidate_report.json"
    md_path = report_dir / "trimatch_context_candidate_report.md"

    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    md_path.write_text(_markdown(report))

    print(json.dumps(summary, indent=2, sort_keys=True))
    print("json_report=", json_path)
    print("markdown_report=", md_path)

    return 0


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _processed_with_basic_context(text: str) -> ProcessedMessage:
    tokens: list[TokenInfo] = []

    for match in re.finditer(r"\b[\w']+\b", text):
        word = match.group(0)
        tokens.append(
            TokenInfo(
                text=word,
                lemma=word.casefold(),
                start=match.start(),
                end=match.end(),
                negated=False,
                hedged=False,
                counterfactual=False,
            )
        )

    negation_spans = _basic_negation_spans(text)
    hedge_spans = _basic_hedge_spans(text)
    counterfactual_spans = _basic_counterfactual_spans(text)

    return ProcessedMessage(
        raw=text,
        normalized=text,
        tokens=tokens,
        negation_spans=negation_spans,
        hedge_spans=hedge_spans,
        counterfactual_spans=counterfactual_spans,
        deterministic_atoms={},
        embedding=[1.0],
        language="en",
        char_count=len(text),
    )


def _basic_negation_spans(text: str) -> list[Span]:
    spans: list[Span] = []
    terminators = [".", "?", "!", ";", " but ", " however ", " instead ", " rather "]

    for match in re.finditer(r"\b(no|not|without|do not|don't)\b", text, flags=re.IGNORECASE):
        start = match.start()
        end = len(text)

        for terminator in terminators:
            index = text.find(terminator, match.end())
            if index >= 0:
                end = min(end, index + (0 if terminator.startswith(" ") else 1))

        spans.append(Span(start=start, end=end, text=text[start:end], cue=match.group(0)))

    return spans


def _basic_hedge_spans(text: str) -> list[Span]:
    spans: list[Span] = []
    for match in re.finditer(r"\b(may|might|maybe|could|considering)\b", text, flags=re.IGNORECASE):
        spans.append(
            Span(
                start=match.start(),
                end=len(text),
                text=text[match.start():],
                cue=match.group(0),
            )
        )
    return spans


def _basic_counterfactual_spans(text: str) -> list[Span]:
    spans: list[Span] = []
    for match in re.finditer(r"\b(if|would|hypothetically)\b", text, flags=re.IGNORECASE):
        spans.append(
            Span(
                start=match.start(),
                end=len(text),
                text=text[match.start():],
                cue=match.group(0),
            )
        )
    return spans


def _negated_services(message: ProcessedMessage) -> list[str]:
    found: list[str] = []
    for phrase, service in SERVICE_ALIASES.items():
        start = message.normalized.casefold().find(phrase)
        if start < 0:
            continue
        end = start + len(phrase)
        if any(start < span.end and end > span.start for span in message.negation_spans):
            if service.value not in found:
                found.append(service.value)
    return found


def _evaluate_expected(expected: dict[str, Any], actual: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    for key in ["query_primary", "service_primary"]:
        if key not in expected:
            continue

        checks.append(
            {
                "field": key,
                "expected": expected[key],
                "actual": actual.get(key),
                "passed": actual.get(key) == expected[key],
            }
        )

    if "service_secondary" in expected:
        expected_secondary = list(expected["service_secondary"])
        actual_services = []

        if actual.get("service_primary"):
            actual_services.append(actual["service_primary"])
        actual_services.extend(actual.get("service_secondary", []))

        missing = [
            item for item in expected_secondary
            if item not in actual_services and item not in actual.get("service_secondary", [])
        ]

        checks.append(
            {
                "field": "service_secondary",
                "expected": expected_secondary,
                "actual": actual.get("service_secondary", []),
                "missing": missing,
                "passed": not missing,
            }
        )

    if "negated_services" in expected:
        expected_negated = list(expected["negated_services"])
        missing = [item for item in expected_negated if item not in actual["negated_services"]]
        checks.append(
            {
                "field": "negated_services",
                "expected": expected_negated,
                "actual": actual["negated_services"],
                "missing": missing,
                "passed": not missing,
            }
        )

    return checks


def _markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Tri-Match Context Candidate Report",
        "",
        f"- Rule dir: `{summary['rule_dir']}`",
        f"- Eval path: `{summary['eval_path']}`",
        f"- Examples: `{summary['example_count']}`",
        f"- Passed: `{summary['passed_count']}`",
        f"- Failed: `{summary['failed_count']}`",
        f"- Valid for active promotion: `{summary['valid_for_active_promotion']}`",
        "",
        "## Rows",
        "",
    ]

    for row in report["rows"]:
        status = "PASS" if row["passed"] else "FAIL"
        lines.extend(
            [
                f"### {row['index']}. {row['subset']} — {status}",
                "",
                f"Message: `{row['text']}`",
                "",
                f"Expected: `{json.dumps(row['expected'], sort_keys=True)}`",
                "",
                f"Actual: `{json.dumps(row['actual'], sort_keys=True)}`",
                "",
            ]
        )

    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
