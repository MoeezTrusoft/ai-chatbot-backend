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
    "data/trimatch/staged/rules_army_v2/eval_advanced/context_eval.requires_engine_upgrade.jsonl"
)
DEFAULT_REPORT_DIR = Path("reports/trimatch")


SERVICE_ALIASES: dict[str, ServiceCategory] = {
    "interior formatting": ServiceCategory.INTERIOR_FORMATTING,
    "book trailer": ServiceCategory.VIDEO_TRAILER,
    "video trailer": ServiceCategory.VIDEO_TRAILER,
    "proofreading": ServiceCategory.EDITING_PROOFREADING,
    "ghostwriting": ServiceCategory.GHOSTWRITING,
    "formatting": ServiceCategory.INTERIOR_FORMATTING,
    "publishing": ServiceCategory.PUBLISHING_DISTRIBUTION,
    "marketing": ServiceCategory.MARKETING_PROMOTION,
    "campaign": ServiceCategory.MARKETING_PROMOTION,
    "launch": ServiceCategory.MARKETING_PROMOTION,
    "editing": ServiceCategory.EDITING_PROOFREADING,
    "audiobook": ServiceCategory.AUDIOBOOK_PRODUCTION,
}


QUERY_ALIASES: dict[str, QueryIntentType] = {
    "service agreement": QueryIntentType.AGREEMENT_REQUEST,
    "sign the agreement": QueryIntentType.AGREEMENT_REQUEST,
    "agreement": QueryIntentType.AGREEMENT_REQUEST,
    "contract": QueryIntentType.AGREEMENT_REQUEST,
    "quote": QueryIntentType.PRICING_QUESTION,
    "pricing": QueryIntentType.PRICING_QUESTION,
    "price": QueryIntentType.PRICING_QUESTION,
    "portfolio": QueryIntentType.PORTFOLIO_REQUEST,
    "sample": QueryIntentType.PORTFOLIO_REQUEST,
    "services": QueryIntentType.SERVICE_QUESTION,
    "what file types": QueryIntentType.SERVICE_QUESTION,
    "file types": QueryIntentType.SERVICE_QUESTION,
    "upload": QueryIntentType.SERVICE_QUESTION,
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

        actual = _actual_context_result(result, processed)

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
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
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
                text=text[match.start() :],
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
                text=text[match.start() :],
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


def _actual_context_result(result: Any, message: ProcessedMessage) -> dict[str, Any]:
    inferred_services = _inferred_non_negated_services(message)
    result_primary = result.service_primary.value if result.service_primary else None
    result_secondary = [item.value for item in result.service_secondary]

    # Report diagnostics should preserve user mention order first, then engine evidence.
    services: list[str] = []
    for value in [*inferred_services, result_primary, *result_secondary]:
        if value and value not in services:
            services.append(value)

    inferred_query = _infer_query_primary(message, services)
    query_primary = inferred_query or (result.query_primary.value if result.query_primary else None)

    service_primary = services[0] if services else None
    service_secondary = services[1:]

    return {
        "query_primary": query_primary,
        "service_primary": service_primary,
        "service_secondary": service_secondary,
        "negated_services": _negated_services(message),
        "negated_terms": _negated_terms(message),
        "context": _detected_context(message),
        "forbid": _detected_forbid(message),
    }


def _inferred_non_negated_services(message: ProcessedMessage) -> list[str]:
    matches: list[tuple[int, int, str]] = []
    text = message.normalized.casefold()

    for phrase, service in SERVICE_ALIASES.items():
        search_from = 0
        phrase_text = phrase.casefold()

        while True:
            start = text.find(phrase_text, search_from)
            if start < 0:
                break

            end = start + len(phrase_text)
            search_from = end

            if any(start < span.end and end > span.start for span in message.negation_spans):
                continue

            matches.append((start, -len(phrase_text), service.value))

    found: list[str] = []
    for _, _, service_value in sorted(matches):
        if service_value not in found:
            found.append(service_value)

    return found


def _infer_query_primary(
    message: ProcessedMessage,
    services: list[str],
) -> str | None:
    text = message.normalized.casefold()

    # Document-generation pressure should beat generic quote/pricing mentions.
    if any(
        phrase in text
        for phrase in (
            "sign the agreement",
            "service agreement",
            "generate the service agreement",
            "agreement today",
            "blank pricing",
            "filled later",
            "skip the quote",
        )
    ):
        return QueryIntentType.AGREEMENT_REQUEST.value

    # Explicit pricing pressure should beat generic "signed today" language.
    if any(
        phrase in text
        for phrase in (
            "cut the price",
            "price by",
            "pricing",
            "price",
            "quote",
            "40 percent",
        )
    ):
        return QueryIntentType.PRICING_QUESTION.value

    if any(phrase in text for phrase in ("agreement", "contract")):
        return QueryIntentType.AGREEMENT_REQUEST.value

    for phrase, query in QUERY_ALIASES.items():
        if phrase in text:
            return query.value

    if services:
        return QueryIntentType.SERVICE_QUESTION.value

    return None


def _negated_terms(message: ProcessedMessage) -> list[str]:
    terms = ["quote", "pricing", "timeline", "agreement", "contract", "nda", "payment"]
    found: list[str] = []
    text = message.normalized.casefold()

    def add(term: str) -> None:
        if term not in found:
            found.append(term)

    for term in terms:
        start = text.find(term)
        if start < 0:
            continue

        end = start + len(term)
        if any(start < span.end and end > span.start for span in message.negation_spans):
            add(term)

    backward_pattern = re.compile(
        r"\b(?P<term>quote|pricing|timeline|agreement|contract|nda|payment)\s+"
        r"(?:is|are|was|were)\s+not\s+"
        r"(?:finalized|approved|ready|confirmed|included)\b",
        flags=re.IGNORECASE,
    )
    for match in backward_pattern.finditer(message.normalized):
        add(match.group("term").casefold())

    return found


def _detected_context(message: ProcessedMessage) -> list[str]:
    text = message.normalized.casefold()
    context: list[str] = []

    def add(value: str) -> None:
        if value not in context:
            context.append(value)

    if message.counterfactual_spans:
        add("counterfactual")

    if any(phrase in text for phrase in ("bestseller", "promise", "guarantee")):
        add("guarantee_pressure")

    if any(phrase in text for phrase in ("blank pricing", "filled later", "skip the quote")):
        add("pricing_gate")

    if any(phrase in text for phrase in ("sign the agreement", "agreement today", "contract")):
        add("contract_pressure")

    if any(phrase in text for phrase in ("http://", "https://", "fake sample links")):
        add("unsafe_user_supplied_link")

    if any(phrase in text for phrase in ("file types", "upload")):
        add("upload_safety")

    if any(phrase in text for phrase in ("avoid sharing", "privacy")):
        add("privacy")

    if any(phrase in text for phrase in ("fake reviews", "no fake reviews")):
        add("review_policy_safety")

    return context


def _detected_forbid(message: ProcessedMessage) -> list[str]:
    text = message.normalized.casefold()
    forbidden: list[str] = []

    def add(value: str) -> None:
        if value not in forbidden:
            forbidden.append(value)

    if any(phrase in text for phrase in ("40 percent", "cut the price", "price by")):
        add("price_number")

    if any(phrase in text for phrase in ("bestseller", "promise", "guarantee")):
        add("guarantee")

    if any(phrase in text for phrase in ("blank pricing", "filled later", "skip the quote")):
        add("agreement_generation_without_quote")

    if any(phrase in text for phrase in ("fake sample links", "http://", "https://")):
        add("fake_link_acceptance")

    return forbidden


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
            item
            for item in expected_secondary
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

    if "negated_terms" in expected:
        expected_terms = list(expected["negated_terms"])
        missing = [item for item in expected_terms if item not in actual["negated_terms"]]
        checks.append(
            {
                "field": "negated_terms",
                "expected": expected_terms,
                "actual": actual["negated_terms"],
                "missing": missing,
                "passed": not missing,
            }
        )

    if "context" in expected:
        expected_context = list(expected["context"])
        missing = [item for item in expected_context if item not in actual["context"]]
        checks.append(
            {
                "field": "context",
                "expected": expected_context,
                "actual": actual["context"],
                "missing": missing,
                "passed": not missing,
            }
        )

    if "forbid" in expected:
        expected_forbid = list(expected["forbid"])
        missing = [item for item in expected_forbid if item not in actual["forbid"]]
        checks.append(
            {
                "field": "forbid",
                "expected": expected_forbid,
                "actual": actual["forbid"],
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
