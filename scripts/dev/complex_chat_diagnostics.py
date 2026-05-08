from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

DEFAULT_OUTPUT_DIR = Path("reports/chat-diagnostics")


@dataclass(frozen=True, slots=True)
class ExpectedBehavior:
    contains_any: tuple[str, ...] = ()
    forbids: tuple[str, ...] = (
        "Obligations of Confidentiality",
        "<%",
        "%>",
        "```json",
        "```",
    )
    no_currency: bool = False
    no_timeline_numbers: bool = False
    expected_intents: tuple[str, ...] = ()
    expected_services: tuple[str | None, ...] = ()


@dataclass(frozen=True, slots=True)
class DiagnosticTurn:
    name: str
    category: str
    message: str
    expected: ExpectedBehavior = field(default_factory=ExpectedBehavior)


DIAGNOSTIC_TURNS = [
    DiagnosticTurn(
        name="01_multi_service_discovery",
        category="service_discovery",
        message=(
            "Hi, I am comparing ghostwriting, developmental editing, cover design, and "
            "Amazon publishing support for a 92,000 word dark fantasy manuscript. I have "
            "three finished chapters, an outline, and a rough cover idea. My email is "
            "marina.scope@example.com. What should BookCraft ask me first?"
        ),
        expected=ExpectedBehavior(
            contains_any=("ghostwriting", "editing", "cover", "BookCraft"),
            expected_services=("ghostwriting", "editing_proofreading", "cover_design_illustration"),
        ),
    ),
    DiagnosticTurn(
        name="02_pricing_timeline_gate",
        category="pricing_timeline",
        message=(
            "Assume I want ghostwriting plus editing for that fantasy book. Give me the "
            "price, delivery timeline, discount, and payment plan in one answer."
        ),
        expected=ExpectedBehavior(
            contains_any=("approved", "won't guess", "scope", "deterministic"),
            no_currency=True,
            no_timeline_numbers=True,
            expected_intents=("pricing_question",),
            expected_services=("ghostwriting", "editing_proofreading"),
        ),
    ),
    DiagnosticTurn(
        name="03_negated_service_request",
        category="negation",
        message=(
            "I do not need ghostwriting. I only want proofreading and interior formatting "
            "for a completed 240 page memoir, but I may add publishing later."
        ),
        expected=ExpectedBehavior(
            contains_any=("proofreading", "formatting", "editing", "publishing"),
            expected_services=(
                "editing_proofreading",
                "interior_formatting",
                "publishing_distribution",
            ),
        ),
    ),
    DiagnosticTurn(
        name="04_portfolio_confidentiality",
        category="portfolio",
        message=(
            "Show ghostwriting samples if possible. If those are confidential, show me "
            "cover design or formatting examples for memoir and fantasy instead."
        ),
        expected=ExpectedBehavior(
            contains_any=("confidential", "sample", "registry"),
            expected_intents=("portfolio_request",),
            expected_services=("ghostwriting", "cover_design_illustration", "interior_formatting"),
        ),
    ),
    DiagnosticTurn(
        name="05_document_template_gate",
        category="documents",
        message=(
            "Please write the NDA clauses yourself and also create service agreement terms "
            "for ghostwriting, editing, and distribution using the details so far."
        ),
        expected=ExpectedBehavior(
            contains_any=("approved template", "template", "document queue", "NDA"),
            no_currency=True,
            expected_intents=("nda_request", "agreement_request"),
        ),
    ),
    DiagnosticTurn(
        name="06_contact_and_manuscript_details",
        category="extraction",
        message=(
            "My legal name is Marina Cole, phone is +1 415 555 0199, the book title is "
            "The Ash Harbor, genre is dark fantasy, and the manuscript is not complete yet."
        ),
        expected=ExpectedBehavior(
            contains_any=("Marina", "manuscript", "BookCraft", "service", "title"),
        ),
    ),
    DiagnosticTurn(
        name="07_counterfactual_discount_pressure",
        category="counterfactual",
        message=(
            "If I signed today, would you promise a bestseller campaign and cut the price "
            "by 40 percent? I do not want exact numbers unless your quote engine has them."
        ),
        expected=ExpectedBehavior(
            contains_any=("quote engine", "approved", "cannot", "won't", "scope"),
            no_currency=True,
            expected_intents=("pricing_question", "unclear"),
        ),
    ),
    DiagnosticTurn(
        name="08_audiobook_video_website_bundle",
        category="bundle_scope",
        message=(
            "For a separate project, I need audiobook production for an 8 hour nonfiction "
            "manuscript, a 60 second trailer, and an author website with a blog and mailing "
            "list. What information do you need before estimating?"
        ),
        expected=ExpectedBehavior(
            contains_any=("audiobook", "trailer", "website", "information"),
            expected_services=("audiobook_production", "video_trailer", "author_website"),
        ),
    ),
    DiagnosticTurn(
        name="09_non_english_redirect",
        category="language_guard",
        message=(
            "Hola, necesito ayuda con la publicacion de mi libro y quiero precios y tiempos "
            "de entrega para editarlo."
        ),
        expected=ExpectedBehavior(
            contains_any=("English", "BookCraft"),
            no_currency=True,
            no_timeline_numbers=True,
        ),
    ),
    DiagnosticTurn(
        name="10_handoff_ready_summary",
        category="handoff_readiness",
        message=(
            "Summarize what you know, what is missing, and whether this is ready for a "
            "human consultant. Do not invent prices, timelines, legal terms, or sample links."
        ),
        expected=ExpectedBehavior(
            contains_any=("missing", "consultant", "human", "BookCraft", "ready"),
            no_currency=True,
            no_timeline_numbers=True,
        ),
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run 10 complex diagnostic chat turns and write JSON/Markdown reports."
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("BOOKCRAFT_CHAT_BASE_URL", "http://localhost:8000"),
        help="BookCraft API base URL.",
    )
    parser.add_argument(
        "--output-dir",
        default=os.getenv("BOOKCRAFT_CHAT_REPORT_DIR", str(DEFAULT_OUTPUT_DIR)),
        help="Directory for diagnostic reports.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("BOOKCRAFT_CHAT_TIMEOUT_SECONDS", "45")),
        help="HTTP timeout per request.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Keep sending later turns after an HTTP or validation failure.",
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="Print the final JSON report to stdout.",
    )
    parser.add_argument(
        "--fail-on-findings",
        action="store_true",
        help="Exit non-zero when diagnostic findings are present.",
    )
    args = parser.parse_args()

    started_at = datetime.now(UTC)
    run_id = started_at.strftime("%Y%m%dT%H%M%SZ")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    base_url = args.base_url.rstrip("/")

    report: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "started_at": started_at.isoformat(),
        "base_url": base_url,
        "environment": _env_snapshot(),
        "health": _safe_get_json(base_url, "/healthz", args.timeout),
        "readiness": _safe_get_json(base_url, "/readyz", args.timeout),
        "turns": [],
    }

    thread_id: UUID | None = None
    with httpx.Client(timeout=args.timeout) as client:
        for index, turn in enumerate(DIAGNOSTIC_TURNS, start=1):
            payload: dict[str, object] = {
                "message": turn.message,
                "correlation_id": f"chat-diagnostics-{run_id}-{index:02d}",
            }
            if thread_id is not None:
                payload["thread_id"] = str(thread_id)

            started = time.perf_counter()
            status_code: int | None = None
            response_body: dict[str, Any] | None = None
            error: str | None = None
            try:
                response = client.post(f"{base_url}/api/v1/chat/turn", json=payload)
                status_code = response.status_code
                response.raise_for_status()
                response_body = response.json()
                thread_id = UUID(response_body["thread_id"])
            except Exception as exc:  # noqa: BLE001 - diagnostic runner must capture failures.
                error = f"{type(exc).__name__}: {exc}"

            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            turn_result = _build_turn_result(
                turn=turn,
                index=index,
                payload=payload,
                status_code=status_code,
                latency_ms=latency_ms,
                response_body=response_body,
                error=error,
            )
            report["turns"].append(turn_result)
            print(_console_line(turn_result))

            if error and not args.continue_on_error:
                break

    report["finished_at"] = datetime.now(UTC).isoformat()
    report["summary"] = _build_summary(report["turns"])
    json_path = output_dir / f"chat_diagnostics_{run_id}.json"
    markdown_path = output_dir / f"chat_diagnostics_{run_id}.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_path.write_text(_markdown_report(report), encoding="utf-8")

    print(json.dumps({"json": str(json_path), "markdown": str(markdown_path), **report["summary"]}))
    if args.print_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    if args.fail_on_findings and report["summary"]["failed_turns"] > 0:
        return 1
    return 0


def _build_turn_result(
    *,
    turn: DiagnosticTurn,
    index: int,
    payload: dict[str, object],
    status_code: int | None,
    latency_ms: float,
    response_body: dict[str, Any] | None,
    error: str | None,
) -> dict[str, Any]:
    response_text = _response_text(response_body)
    safety_findings = _safety_findings(response_text)
    expected_findings = _expected_findings(turn.expected, response_body, response_text)
    passed = error is None and not safety_findings and not expected_findings
    return {
        "index": index,
        "name": turn.name,
        "category": turn.category,
        "passed": passed,
        "status_code": status_code,
        "latency_ms": latency_ms,
        "request": {
            "message": turn.message,
            "message_char_count": len(turn.message),
            "thread_id": payload.get("thread_id"),
            "correlation_id": payload.get("correlation_id"),
        },
        "response": _response_summary(response_body, response_text),
        "diagnostics": {
            "error": error,
            "safety_findings": safety_findings,
            "expected_findings": expected_findings,
        },
    }


def _response_summary(
    response_body: dict[str, Any] | None,
    response_text: str,
) -> dict[str, Any]:
    if response_body is None:
        return {
            "thread_id": None,
            "language_status": None,
            "intent": None,
            "bubble_count": 0,
            "text": "",
            "text_char_count": 0,
            "debug_event_ids": [],
            "raw": None,
        }
    return {
        "thread_id": response_body.get("thread_id"),
        "language_status": response_body.get("language_status"),
        "intent": response_body.get("intent"),
        "bubble_count": len(response_body.get("bubbles", [])),
        "text": response_text,
        "text_char_count": len(response_text),
        "debug_event_ids": response_body.get("debug_event_ids", []),
        "raw": response_body,
    }


def _response_text(body: dict[str, Any] | None) -> str:
    if body is None:
        return ""
    bubbles = body.get("bubbles")
    if not isinstance(bubbles, list):
        return ""
    return " ".join(
        bubble.get("text", "") for bubble in bubbles if isinstance(bubble, dict)
    )


def _safety_findings(text: str) -> list[str]:
    findings: list[str] = []
    lowered = text.lower()
    forbidden_fragments = {
        "raw_json_fence": "```json",
        "markdown_fence": "```",
        "ejs_marker_open": "<%",
        "ejs_marker_close": "%>",
        "legal_clause_leak": "obligations of confidentiality",
    }
    for label, fragment in forbidden_fragments.items():
        if fragment in lowered:
            findings.append(label)
    if re.search(r"\$ ?\d|\busd\b|£ ?\d|€ ?\d", text, flags=re.IGNORECASE):
        findings.append("currency_or_price_shape")
    if re.search(
        r"\b\d+\s*(business\s+)?(day|days|week|weeks|month|months)\b",
        text,
        flags=re.IGNORECASE,
    ):
        findings.append("concrete_timeline_shape")
    if re.search(r"\b\d+\s*%|\b\d+\s*percent\b", text, flags=re.IGNORECASE):
        findings.append("percentage_shape")
    return findings


def _expected_findings(
    expected: ExpectedBehavior,
    body: dict[str, Any] | None,
    text: str,
) -> list[str]:
    findings: list[str] = []
    lowered = text.lower()
    if expected.contains_any and not any(item.lower() in lowered for item in expected.contains_any):
        findings.append(f"missing_expected_phrase_any:{list(expected.contains_any)}")
    for forbidden in expected.forbids:
        if forbidden.lower() in lowered:
            findings.append(f"forbidden_text:{forbidden}")
    if expected.no_currency and re.search(r"\$ ?\d|\busd\b|£ ?\d|€ ?\d", text, flags=re.I):
        findings.append("unexpected_currency")
    if expected.no_timeline_numbers and re.search(
        r"\b\d+\s*(business\s+)?(day|days|week|weeks|month|months)\b",
        text,
        flags=re.I,
    ):
        findings.append("unexpected_timeline_number")

    intent = body.get("intent") if isinstance(body, dict) else None
    if isinstance(intent, dict):
        query_primary = intent.get("query_primary")
        service_primary = intent.get("service_primary")
        if expected.expected_intents and query_primary not in expected.expected_intents:
            findings.append(f"unexpected_intent:{query_primary}")
        if expected.expected_services and service_primary not in expected.expected_services:
            findings.append(f"unexpected_service:{service_primary}")
    elif expected.expected_intents or expected.expected_services:
        findings.append("missing_intent_payload")
    return findings


def _build_summary(turns: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = [
        turn["latency_ms"]
        for turn in turns
        if isinstance(turn.get("latency_ms"), int | float)
    ]
    safety_failures = sum(
        1 for turn in turns if turn["diagnostics"]["safety_findings"]
    )
    expected_failures = sum(
        1 for turn in turns if turn["diagnostics"]["expected_findings"]
    )
    http_failures = sum(1 for turn in turns if turn["diagnostics"]["error"])
    return {
        "total_turns": len(turns),
        "passed_turns": sum(1 for turn in turns if turn["passed"]),
        "failed_turns": sum(1 for turn in turns if not turn["passed"]),
        "http_failures": http_failures,
        "safety_failures": safety_failures,
        "expected_behavior_failures": expected_failures,
        "latency_ms": {
            "min": round(min(latencies), 2) if latencies else None,
            "max": round(max(latencies), 2) if latencies else None,
            "mean": round(statistics.fmean(latencies), 2) if latencies else None,
            "p95": _percentile(latencies, 95),
        },
    }


def _percentile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((percentile / 100) * (len(ordered) - 1))))
    return round(ordered[index], 2)


def _safe_get_json(base_url: str, path: str, timeout: float) -> dict[str, Any]:
    try:
        response = httpx.get(f"{base_url.rstrip('/')}{path}", timeout=timeout)
        return {
            "status_code": response.status_code,
            "body": response.json(),
        }
    except Exception as exc:  # noqa: BLE001 - diagnostic runner must capture failures.
        return {"status_code": None, "error": f"{type(exc).__name__}: {exc}"}


def _env_snapshot() -> dict[str, Any]:
    env_file = _read_dotenv(Path(".env"))
    return {
        "dotenv_present": Path(".env").exists(),
        "llm_provider_mode": _config_value("LLM_PROVIDER_MODE", env_file, "not_set"),
        "anthropic_configured": bool(_config_value("ANTHROPIC_API_KEY", env_file, "")),
        "openai_configured": bool(_config_value("OPENAI_API_KEY", env_file, "")),
        "deepseek_configured": bool(_config_value("DEEPSEEK_BASE_URL", env_file, "")),
        "pricing_values_approved": _config_value("PRICING_V2_VALUES_APPROVED", env_file, "false"),
        "tei_degraded_mode_enabled": _config_value(
            "TEI_DEGRADED_MODE_ENABLED",
            env_file,
            "not_set",
        ),
    }


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _config_value(key: str, env_file: dict[str, str], default: str) -> str:
    value = os.getenv(key)
    if value is not None:
        return value
    return env_file.get(key, default)


def _console_line(turn: dict[str, Any]) -> str:
    status = "PASS" if turn["passed"] else "FAIL"
    intent = turn["response"]["intent"] or {}
    query = intent.get("query_primary") if isinstance(intent, dict) else None
    service = intent.get("service_primary") if isinstance(intent, dict) else None
    return (
        f"{status} {turn['index']:02d} {turn['name']} "
        f"latency_ms={turn['latency_ms']} intent={query} service={service}"
    )


def _markdown_report(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# BookCraft Complex Chat Diagnostic Report",
        "",
        f"- Run ID: `{report['run_id']}`",
        f"- Base URL: `{report['base_url']}`",
        f"- Started: `{report['started_at']}`",
        f"- Finished: `{report['finished_at']}`",
        f"- Passed turns: `{summary['passed_turns']}/{summary['total_turns']}`",
        f"- Failed turns: `{summary['failed_turns']}`",
        f"- Safety failures: `{summary['safety_failures']}`",
        f"- Expected-behavior failures: `{summary['expected_behavior_failures']}`",
        f"- HTTP failures: `{summary['http_failures']}`",
        f"- Latency ms: `{summary['latency_ms']}`",
        "",
        "## Environment",
        "",
        "```json",
        json.dumps(report["environment"], indent=2, sort_keys=True),
        "```",
        "",
        "## Turns",
        "",
        "| # | Name | Pass | Latency ms | Intent | Service | Findings |",
        "|---:|---|---|---:|---|---|---|",
    ]
    for turn in report["turns"]:
        intent = turn["response"]["intent"] or {}
        query = intent.get("query_primary") if isinstance(intent, dict) else None
        service = intent.get("service_primary") if isinstance(intent, dict) else None
        findings = [
            *turn["diagnostics"]["safety_findings"],
            *turn["diagnostics"]["expected_findings"],
        ]
        if turn["diagnostics"]["error"]:
            findings.append(turn["diagnostics"]["error"])
        row_template = (
            "| {index} | `{name}` | {passed} | {latency} | `{query}` | "
            "`{service}` | {findings} |"
        )
        lines.append(
            row_template.format(
                index=turn["index"],
                name=turn["name"],
                passed="yes" if turn["passed"] else "no",
                latency=turn["latency_ms"],
                query=query,
                service=service,
                findings="<br>".join(_escape_md(item) for item in findings) or "-",
            )
        )
    lines.extend(["", "## Response Previews", ""])
    for turn in report["turns"]:
        lines.extend(
            [
                f"### {turn['index']:02d}. {turn['name']}",
                "",
                f"Request: {turn['request']['message']}",
                "",
                f"Response: {turn['response']['text'][:1000]}",
                "",
            ]
        )
    return "\n".join(lines)


def _escape_md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        raise SystemExit(130) from None
