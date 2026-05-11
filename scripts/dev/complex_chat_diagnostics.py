from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html import escape
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
    DiagnosticTurn(
        name="11_corrected_contact_identity",
        category="state_correction",
        message=(
            "Correction: the author name should be Marina C. Vale, not Marina Cole. Use "
            "marina.vale@example.com as the primary email, but keep the same phone number."
        ),
        expected=ExpectedBehavior(
            contains_any=("Marina", "email", "phone", "BookCraft"),
            expected_intents=("contact_info_provided", "service_question", "unclear"),
        ),
    ),
    DiagnosticTurn(
        name="12_rush_scope_without_numbers",
        category="pricing_timeline",
        message=(
            "The launch date moved up. I want rush editing, formatting, and publishing, but "
            "do not give exact delivery dates unless the deterministic engine approves them."
        ),
        expected=ExpectedBehavior(
            contains_any=("approved", "scope", "deterministic", "engine", "information"),
            no_currency=True,
            no_timeline_numbers=True,
            expected_intents=("pricing_question", "service_question"),
        ),
    ),
    DiagnosticTurn(
        name="13_platform_distribution_specifics",
        category="publishing_distribution",
        message=(
            "For distribution, I need Amazon KDP, IngramSpark, ebook, paperback, metadata, "
            "categories, keywords, and ISBN guidance. Which of these can BookCraft handle?"
        ),
        expected=ExpectedBehavior(
            contains_any=("publishing", "distribution", "Amazon", "metadata", "BookCraft"),
            expected_services=("publishing_distribution",),
        ),
    ),
    DiagnosticTurn(
        name="14_marketing_guarantee_refusal",
        category="marketing_safety",
        message=(
            "Can BookCraft guarantee bestseller rank, verified reviews, and media coverage "
            "if I buy a marketing campaign? Be direct and do not overpromise."
        ),
        expected=ExpectedBehavior(
            contains_any=("guarantee", "cannot", "marketing", "overpromise", "BookCraft"),
            no_currency=True,
            expected_services=("marketing_promotion",),
        ),
    ),
    DiagnosticTurn(
        name="15_website_feature_scope",
        category="author_website",
        message=(
            "I need an author website with homepage, book pages, blog, newsletter signup, "
            "lead magnet download, events page, and maybe ecommerce later."
        ),
        expected=ExpectedBehavior(
            contains_any=("website", "blog", "newsletter", "features", "BookCraft"),
            expected_services=("author_website",),
        ),
    ),
    DiagnosticTurn(
        name="16_audiobook_rights_and_narration",
        category="audiobook",
        message=(
            "For audiobook production, I have narrator auditions, need ACX-style mastering, "
            "chapter files, and I am unsure about music rights for intro and outro."
        ),
        expected=ExpectedBehavior(
            contains_any=("audiobook", "narrator", "rights", "mastering", "BookCraft"),
            expected_services=("audiobook_production",),
        ),
    ),
    DiagnosticTurn(
        name="17_video_trailer_style",
        category="video_trailer",
        message=(
            "For the trailer, I want cinematic motion graphics, voiceover, licensed music, "
            "subtitles, and square plus vertical cuts. What details matter?"
        ),
        expected=ExpectedBehavior(
            contains_any=("trailer", "video", "voiceover", "music", "details"),
            expected_services=("video_trailer",),
        ),
    ),
    DiagnosticTurn(
        name="18_illustration_complexity",
        category="cover_illustration",
        message=(
            "The cover might need a full illustration: two characters, a harbor scene, custom "
            "typography, and print plus ebook layout. I do not need interior art."
        ),
        expected=ExpectedBehavior(
            contains_any=("cover", "illustration", "typography", "ebook", "print"),
            expected_services=("cover_design_illustration",),
        ),
    ),
    DiagnosticTurn(
        name="19_revision_expectations",
        category="scope_control",
        message=(
            "I expect unlimited revisions, daily calls, and for you to rewrite until my beta "
            "readers are happy. Is that part of the service scope?"
        ),
        expected=ExpectedBehavior(
            contains_any=("scope", "revision", "service", "expectations", "BookCraft"),
            no_currency=True,
        ),
    ),
    DiagnosticTurn(
        name="20_mixed_language_with_english",
        category="language_guard",
        message=(
            "I can write in English, but necesito ayuda tambien. For now, answer in English: "
            "which editing service fits a rough translated manuscript?"
        ),
        expected=ExpectedBehavior(
            contains_any=("English", "editing", "manuscript", "BookCraft"),
            expected_services=("editing_proofreading",),
        ),
    ),
    DiagnosticTurn(
        name="21_short_ambiguous_price",
        category="ambiguity",
        message="Price?",
        expected=ExpectedBehavior(
            contains_any=("service", "price", "which", "BookCraft"),
            no_currency=True,
            no_timeline_numbers=True,
            expected_intents=("pricing_question", "unclear"),
        ),
    ),
    DiagnosticTurn(
        name="22_privacy_and_confidentiality",
        category="policy",
        message=(
            "Before I upload chapters, explain how confidentiality works. I may need an NDA, "
            "but do not draft legal text inside chat."
        ),
        expected=ExpectedBehavior(
            contains_any=("confidential", "NDA", "template", "legal", "BookCraft"),
            no_currency=True,
            expected_intents=("nda_request", "policy_question", "service_question"),
        ),
    ),
    DiagnosticTurn(
        name="23_agreement_requires_quote",
        category="documents",
        message=(
            "Generate the service agreement now for ghostwriting, proofreading, and marketing, "
            "even if the quote is not finalized."
        ),
        expected=ExpectedBehavior(
            contains_any=(
                "agreement",
                "deterministic quote",
                "approved template",
                "document queue",
            ),
            no_currency=True,
            expected_intents=("agreement_request",),
        ),
    ),
    DiagnosticTurn(
        name="24_portfolio_no_hallucinated_links",
        category="portfolio",
        message=(
            "Give me three exact sample links for marketing, formatting, and publishing. If "
            "the registry does not have them, say so instead of inventing URLs."
        ),
        expected=ExpectedBehavior(
            contains_any=("sample", "registry", "marketing", "formatting", "publishing"),
            expected_intents=("portfolio_request",),
        ),
    ),
    DiagnosticTurn(
        name="25_final_consultant_handoff",
        category="handoff_readiness",
        message=(
            "Based on everything above, what should a human consultant review first, and what "
            "fields are still missing before pricing, NDA, agreement, and production planning?"
        ),
        expected=ExpectedBehavior(
            contains_any=("consultant", "missing", "pricing", "NDA", "agreement"),
            no_currency=True,
            no_timeline_numbers=True,
        ),
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run 25 complex diagnostic chat turns and write JSON, Markdown, and Word reports."
        )
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
    docx_path = output_dir / f"chat_diagnostics_{run_id}.docx"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_path.write_text(_markdown_report(report), encoding="utf-8")
    _write_docx_report(report, docx_path)

    print(
        json.dumps(
            {
                "json": str(json_path),
                "markdown": str(markdown_path),
                "docx": str(docx_path),
                **report["summary"],
            }
        )
    )
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


def _write_docx_report(report: dict[str, Any], path: Path) -> None:
    document_xml = _docx_document_xml(report)
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.'
        'main+xml"/></Types>'
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
        'officeDocument" Target="word/document.xml"/></Relationships>'
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("word/document.xml", document_xml)


def _docx_document_xml(report: dict[str, Any]) -> str:
    summary = report["summary"]
    parts: list[str] = [
        _docx_heading("BookCraft Complex Chat Diagnostic Report", level=1),
        _docx_table(
            rows=[
                ("Run ID", report["run_id"]),
                ("Base URL", report["base_url"]),
                ("Started", report["started_at"]),
                ("Finished", report["finished_at"]),
                ("Passed turns", f"{summary['passed_turns']}/{summary['total_turns']}"),
                ("Failed turns", summary["failed_turns"]),
                ("Safety failures", summary["safety_failures"]),
                ("Expected-behavior failures", summary["expected_behavior_failures"]),
                ("HTTP failures", summary["http_failures"]),
                ("Latency ms", json.dumps(summary["latency_ms"], sort_keys=True)),
            ],
            headers=("Metric", "Value"),
        ),
        _docx_heading("Environment", level=2),
        _docx_table(
            rows=[(key, value) for key, value in sorted(report["environment"].items())],
            headers=("Setting", "Value"),
        ),
        _docx_heading("Turn Overview", level=2),
        _docx_table(
            rows=[_docx_turn_row(turn) for turn in report["turns"]],
            headers=(
                "#",
                "Name",
                "Category",
                "Pass",
                "Latency ms",
                "Intent",
                "Service",
                "Findings",
            ),
        ),
        _docx_heading("Failed Findings", level=2),
    ]
    failed_rows = [
        (
            turn["index"],
            turn["name"],
            _findings_text(turn),
            turn["request"]["message"],
            turn["response"]["text"][:700],
        )
        for turn in report["turns"]
        if not turn["passed"]
    ]
    parts.append(
        _docx_table(
            rows=failed_rows or [("-", "No failed turns", "-", "-", "-")],
            headers=("#", "Turn", "Findings", "Request", "Response Preview"),
        )
    )
    parts.append(_docx_heading("Detailed Turn Records", level=2))
    for turn in report["turns"]:
        parts.extend(
            [
                _docx_heading(f"{turn['index']:02d}. {turn['name']}", level=3),
                _docx_table(
                    rows=[
                        ("Category", turn["category"]),
                        ("Passed", "yes" if turn["passed"] else "no"),
                        ("Status code", turn["status_code"]),
                        ("Latency ms", turn["latency_ms"]),
                        ("Thread ID", turn["response"]["thread_id"]),
                        ("Correlation ID", turn["request"]["correlation_id"]),
                        ("Language", turn["response"]["language_status"]),
                        ("Intent", _intent_value(turn, "query_primary")),
                        ("Service", _intent_value(turn, "service_primary")),
                        ("Funnel stage", _intent_value(turn, "funnel_stage")),
                        ("Findings", _findings_text(turn) or "-"),
                    ],
                    headers=("Field", "Value"),
                ),
                _docx_paragraph("Request", bold=True),
                _docx_paragraph(turn["request"]["message"]),
                _docx_paragraph("Response", bold=True),
                _docx_paragraph(turn["response"]["text"] or "-"),
            ]
        )
    body = "".join(parts)
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    {body}
    <w:sectPr>
      <w:pgSz w:w="12240" w:h="15840"/>
      <w:pgMar w:top="720" w:right="720" w:bottom="720" w:left="720"/>
    </w:sectPr>
  </w:body>
</w:document>"""


def _docx_turn_row(turn: dict[str, Any]) -> tuple[Any, ...]:
    return (
        turn["index"],
        turn["name"],
        turn["category"],
        "yes" if turn["passed"] else "no",
        turn["latency_ms"],
        _intent_value(turn, "query_primary"),
        _intent_value(turn, "service_primary"),
        _findings_text(turn) or "-",
    )


def _intent_value(turn: dict[str, Any], key: str) -> Any:
    intent = turn["response"]["intent"]
    if isinstance(intent, dict):
        return intent.get(key)
    return None


def _findings_text(turn: dict[str, Any]) -> str:
    findings = [
        *turn["diagnostics"]["safety_findings"],
        *turn["diagnostics"]["expected_findings"],
    ]
    if turn["diagnostics"]["error"]:
        findings.append(turn["diagnostics"]["error"])
    return "; ".join(str(item) for item in findings)


def _docx_heading(text: str, *, level: int) -> str:
    size = {1: "32", 2: "26", 3: "22"}.get(level, "22")
    return (
        "<w:p><w:pPr><w:spacing w:after=\"120\"/></w:pPr>"
        f"<w:r><w:rPr><w:b/><w:sz w:val=\"{size}\"/></w:rPr>"
        f"<w:t>{_xml(text)}</w:t></w:r></w:p>"
    )


def _docx_paragraph(text: Any, *, bold: bool = False) -> str:
    bold_xml = "<w:b/>" if bold else ""
    return (
        "<w:p><w:pPr><w:spacing w:after=\"80\"/></w:pPr><w:r>"
        f"<w:rPr>{bold_xml}</w:rPr><w:t xml:space=\"preserve\">{_xml(text)}</w:t>"
        "</w:r></w:p>"
    )


def _docx_table(*, rows: list[tuple[Any, ...]], headers: tuple[str, ...]) -> str:
    table_rows = [_docx_row(headers, header=True)]
    table_rows.extend(_docx_row(tuple(row), header=False) for row in rows)
    return (
        "<w:tbl>"
        "<w:tblPr><w:tblW w:w=\"0\" w:type=\"auto\"/>"
        "<w:tblBorders>"
        "<w:top w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"808080\"/>"
        "<w:left w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"808080\"/>"
        "<w:bottom w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"808080\"/>"
        "<w:right w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"808080\"/>"
        "<w:insideH w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"808080\"/>"
        "<w:insideV w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"808080\"/>"
        "</w:tblBorders></w:tblPr>"
        f"{''.join(table_rows)}"
        "</w:tbl>"
        "<w:p/>"
    )


def _docx_row(values: tuple[Any, ...], *, header: bool) -> str:
    return f"<w:tr>{''.join(_docx_cell(value, header=header) for value in values)}</w:tr>"


def _docx_cell(value: Any, *, header: bool) -> str:
    fill = "<w:shd w:fill=\"D9EAF7\"/>" if header else ""
    bold = "<w:b/>" if header else ""
    text = _xml(value)
    return (
        "<w:tc><w:tcPr>"
        "<w:tcW w:w=\"2400\" w:type=\"dxa\"/>"
        f"{fill}</w:tcPr><w:p><w:r><w:rPr>{bold}</w:rPr>"
        f"<w:t xml:space=\"preserve\">{text}</w:t></w:r></w:p></w:tc>"
    )


def _xml(value: Any) -> str:
    if value is None:
        return ""
    return escape(str(value), quote=False)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        raise SystemExit(130) from None
