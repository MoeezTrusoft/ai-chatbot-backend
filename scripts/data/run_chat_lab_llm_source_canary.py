from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TestMessage:
    id: str
    message: str
    expected_services: list[str]


TEST_MESSAGES = [
    TestMessage(
        id="T01_MULTI_SERVICE_EXISTING_MANUSCRIPT",
        message=(
            "I have a 58,000-word memoir already drafted in Google Docs. "
            "I don’t need ghostwriting, but I do need developmental editing, "
            "proofreading, interior formatting for KDP and IngramSpark, and maybe "
            "launch marketing. What should I handle first, and what details do you "
            "need from me?"
        ),
        expected_services=[
            "editing_proofreading",
            "interior_formatting",
            "marketing_promotion",
        ],
    ),
    TestMessage(
        id="T02_STRONG_NEGATION",
        message=(
            "I do not need cover design, audiobook production, video trailer, "
            "author website, or marketing. I only need proofreading and clean "
            "print-ready formatting for a 35,000-word poetry collection that already "
            "has a finished cover."
        ),
        expected_services=[
            "editing_proofreading",
            "interior_formatting",
        ],
    ),
    TestMessage(
        id="T03_MARKETING_CONTRADICTION",
        message=(
            "I don’t want marketing services, but I do need help with Amazon "
            "keywords, book description, review strategy, launch posts, and visibility "
            "after publishing. I also need paperback formatting. Is that considered "
            "marketing or something else?"
        ),
        expected_services=[
            "marketing_promotion",
            "interior_formatting",
        ],
    ),
    TestMessage(
        id="T04_CHILDRENS_BOOK_IDEA_STAGE",
        message=(
            "I only have an idea for a children’s picture book about a shy robot and "
            "a brave little girl. I need help writing the story, creating illustrations, "
            "formatting it for print and Kindle, and publishing it on Amazon. I don’t "
            "know the word count or page count yet."
        ),
        expected_services=[
            "ghostwriting",
            "cover_design_illustration",
            "interior_formatting",
            "publishing_distribution",
        ],
    ),
    TestMessage(
        id="T05_SERVICE_COMPARISON",
        message=(
            "I’m confused between ghostwriting, book coaching, developmental editing, "
            "and manuscript completion. I have 14 messy chapters, some voice notes, "
            "and a rough outline. I don’t want someone to take over completely, but I "
            "need serious structure help."
        ),
        expected_services=[
            "ghostwriting",
            "editing_proofreading",
        ],
    ),
    TestMessage(
        id="T06_READY_TO_START",
        message=(
            "I’m ready to start this week. My manuscript is 54,000 words, "
            "self-help/business category, fully drafted in Word. I need copyediting, "
            "proofreading, interior formatting, cover design, and Amazon KDP publishing. "
            "What are the exact next steps to begin?"
        ),
        expected_services=[
            "editing_proofreading",
            "interior_formatting",
            "cover_design_illustration",
            "publishing_distribution",
        ],
    ),
    TestMessage(
        id="T07_IMAGE_HEAVY_COOKBOOK",
        message=(
            "I have a cookbook with photos, recipe tables, ingredient lists, and "
            "section dividers. I need it to look clean as paperback and Kindle without "
            "the layout breaking. I may also need light proofreading because the recipes "
            "came from different contributors."
        ),
        expected_services=[
            "interior_formatting",
            "editing_proofreading",
        ],
    ),
    TestMessage(
        id="T08_PUBLISHED_BOOK_EXPANSION",
        message=(
            "My book is already published on Amazon, but now I want to improve the "
            "book description, update keywords, create launch-style social content, "
            "and maybe turn it into an audiobook later. What would you recommend first?"
        ),
        expected_services=[
            "marketing_promotion",
            "audiobook_production",
        ],
    ),
    TestMessage(
        id="T09_VISUAL_BRAND_AUTHOR_PLATFORM",
        message=(
            "I’m building my author brand around a nonfiction business book. I need a "
            "better author website, updated book positioning, cleaner sales copy, and "
            "maybe some launch assets. The manuscript is already edited, so I don’t "
            "need editing right now."
        ),
        expected_services=[
            "author_website",
            "marketing_promotion",
        ],
    ),
    TestMessage(
        id="T10_COMPLEX_PRODUCTION_ORDER",
        message=(
            "I have a completed fantasy novel and I’m not sure what order to do things "
            "in. It still needs a final proofread, a professional cover, interior "
            "formatting, publishing setup, and some basic launch preparation. I don’t "
            "want to waste money doing steps in the wrong sequence."
        ),
        expected_services=[
            "editing_proofreading",
            "cover_design_illustration",
            "interior_formatting",
            "publishing_distribution",
            "marketing_promotion",
        ],
    ),
]


CLAUDE_SOURCE_RE = re.compile(r"(claude|sonnet|anthropic)", re.IGNORECASE)

BAD_SOURCES = {
    "rag_fast_path",
    "template_no_adapter",
    "mock_sonnet",
    "mock_sonnet_reduced",
    "direct_answer",
    "clarification",
    "deterministic_mixed_request_guard",
    "deterministic_greeting",
}

BAD_RESPONSE_PATTERNS = [
    r"\bSource:\s*",
    r"\brag_fast_path\b",
    r"\btemplate_no_adapter\b",
    r"\bquote engine\b",
    r"\bdeterministic engine\b",
    r"\bapproved engine\b",
    r"\bdocument queue\b",
    r"\btool output\b",
    r"\bruntime atoms\b",
    r"\bprovider votes\b",
    r"\bclassifier\b",
    r"\bbackend\b",
    r"^\s*#{1,6}\s",
    r"\n\s*\|.*\|",
]


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def make_chat_jwt(signing_key: str, customer_id: str, ttl_seconds: int = 3600) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": customer_id,
        "customer_id": customer_id,
        "exp": int(time.time()) + ttl_seconds,
    }

    raw_header = b64url(json.dumps(header, separators=(",", ":")).encode())
    raw_payload = b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{raw_header}.{raw_payload}"

    signature = b64url(
        hmac.new(
            signing_key.encode("utf-8"),
            signing_input.encode("ascii"),
            hashlib.sha256,
        ).digest()
    )

    return f"{signing_input}.{signature}"


def request_json(
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers = {**headers, "Content-Type": "application/json"}

    request = urllib.request.Request(
        url=url,
        data=data,
        method=method,
        headers=headers,
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed HTTP {exc.code}: {raw}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{method} {url} failed: {exc}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{method} {url} returned non-JSON: {raw[:500]}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError(f"{method} {url} returned non-object JSON: {payload!r}")

    return payload


def response_text(chat_response: dict[str, Any]) -> str:
    bubbles = chat_response.get("bubbles")
    if isinstance(bubbles, list):
        parts: list[str] = []
        for bubble in bubbles:
            if isinstance(bubble, dict) and isinstance(bubble.get("text"), str):
                parts.append(bubble["text"])
        if parts:
            return "\n\n".join(parts)

    for key in ["text", "response", "message"]:
        value = chat_response.get(key)
        if isinstance(value, str):
            return value

    return ""


def first_trace_for_thread(
    *,
    base_url: str,
    admin_token: str,
    thread_id: str,
    poll_seconds: float,
) -> dict[str, Any]:
    deadline = time.time() + poll_seconds
    last_payload: dict[str, Any] = {}

    while time.time() <= deadline:
        payload = request_json(
            method="GET",
            url=f"{base_url}/api/admin/analysis/traces/{thread_id}?limit=10",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=15,
        )
        last_payload = payload

        traces = payload.get("traces")
        if isinstance(traces, list) and traces:
            trace = traces[0]
            if isinstance(trace, dict):
                return trace

        time.sleep(0.5)

    raise RuntimeError(
        f"No live trace found for thread_id={thread_id}. "
        f"Last payload={last_payload}"
    )


def latest_trace_fallback(
    *,
    base_url: str,
    admin_token: str,
    thread_id: str,
    message: str,
    poll_seconds: float,
) -> dict[str, Any] | None:
    deadline = time.time() + poll_seconds
    message_head = message[:80].lower()

    while time.time() <= deadline:
        payload = request_json(
            method="GET",
            url=f"{base_url}/api/admin/analysis/traces/latest?limit=80",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=15,
        )

        traces = payload.get("traces")
        if isinstance(traces, list):
            for trace in traces:
                if not isinstance(trace, dict):
                    continue

                if trace.get("thread_id") == thread_id:
                    return trace

                preview = str(trace.get("message_preview") or "").lower()
                if message_head and message_head[:40] in preview:
                    return trace

        time.sleep(0.5)

    return None


def get_nested(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def trace_source(trace: dict[str, Any]) -> str:
    for path in [
        ("assistant", "source"),
        ("response", "source"),
        ("source",),
    ]:
        value = get_nested(trace, *path)
        if isinstance(value, str) and value:
            return value
    return "unknown"


def trace_services(trace: dict[str, Any]) -> list[str]:
    values: list[str] = []

    primary = get_nested(trace, "intent", "service_primary")
    if isinstance(primary, str) and primary:
        values.append(primary)

    secondary = get_nested(trace, "intent", "service_secondary")
    if isinstance(secondary, list):
        values.extend(str(item) for item in secondary if item)

    runtime_services = get_nested(trace, "runtime_atoms", "services")
    if isinstance(runtime_services, list):
        values.extend(str(item) for item in runtime_services if item)

    ordered: list[str] = []
    for value in values:
        if value not in ordered:
            ordered.append(value)
    return ordered


def has_bad_response_text(text: str) -> list[str]:
    hits: list[str] = []
    for pattern in BAD_RESPONSE_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE):
            hits.append(pattern)
    return hits


def source_is_claude(source: str) -> bool:
    return bool(CLAUDE_SOURCE_RE.search(source))


def source_is_bad(source: str) -> bool:
    normalized = source.strip().lower()
    return normalized in BAD_SOURCES or normalized.startswith("rag_fast_path")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send 10 Chat Lab canary messages and verify Claude/Sonnet wrote the response."
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("STAGING_API_BASE_URL") or "http://localhost:8000",
    )
    parser.add_argument(
        "--customer-id",
        default=os.getenv("SMOKE_CUSTOMER_ID"),
    )
    parser.add_argument(
        "--jwt-signing-key",
        default=os.getenv("JWT_SIGNING_KEY"),
    )
    parser.add_argument(
        "--admin-token",
        default=os.getenv("BOOKCRAFT_ADMIN_ANALYSIS_TOKEN"),
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=8.0,
    )
    parser.add_argument(
        "--out-dir",
        default="reports/llm_source_canary",
    )
    args = parser.parse_args()

    missing = [
        name
        for name, value in [
            ("SMOKE_CUSTOMER_ID / --customer-id", args.customer_id),
            ("JWT_SIGNING_KEY / --jwt-signing-key", args.jwt_signing_key),
            ("BOOKCRAFT_ADMIN_ANALYSIS_TOKEN / --admin-token", args.admin_token),
        ]
        if not value
    ]
    if missing:
        print("Missing required settings:", ", ".join(missing), file=sys.stderr)
        return 2

    base_url = args.base_url.rstrip("/")
    chat_jwt = make_chat_jwt(args.jwt_signing_key, args.customer_id)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    failures: list[str] = []

    started_at = datetime.now(UTC).isoformat()

    for index, item in enumerate(TEST_MESSAGES, 1):
        print(f"==> {index:02d}/{len(TEST_MESSAGES)} {item.id}")

        chat_response = request_json(
            method="POST",
            url=f"{base_url}/api/v1/chat/turn",
            headers={"Authorization": f"Bearer {chat_jwt}"},
            body={
                "message": item.message,
                "customer_id": args.customer_id,
            },
            timeout=45,
        )

        thread_id = chat_response.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            failures.append(f"{item.id}: chat response missing thread_id")
            trace = {}
            source = "missing_trace"
        else:
            try:
                trace = first_trace_for_thread(
                    base_url=base_url,
                    admin_token=args.admin_token,
                    thread_id=thread_id,
                    poll_seconds=args.poll_seconds,
                )
            except RuntimeError as exc:
                fallback_trace = latest_trace_fallback(
                    base_url=base_url,
                    admin_token=args.admin_token,
                    thread_id=thread_id,
                    message=item.message,
                    poll_seconds=max(args.poll_seconds, 20.0),
                )
                if fallback_trace is None:
                    print(f"    WARNING: trace missing for {item.id}: {exc}")
                    trace = {}
                    failures.append(f"{item.id}: trace missing for thread_id={thread_id}")
                else:
                    print(f"    used latest-trace fallback for {item.id}")
                    trace = fallback_trace

            source = trace_source(trace)

        text = response_text(chat_response)
        services = trace_services(trace)
        bad_text_hits = has_bad_response_text(text)

        missing_services = [
            service for service in item.expected_services if service not in services
        ]

        source_ok = source_is_claude(source) and not source_is_bad(source)
        text_ok = not bad_text_hits
        service_ok = not missing_services

        row = {
            "id": item.id,
            "thread_id": thread_id,
            "source": source,
            "source_ok": source_ok,
            "text_ok": text_ok,
            "service_ok": service_ok,
            "missing_services": missing_services,
            "bad_text_hits": bad_text_hits,
            "detected_services": services,
            "message": item.message,
            "response": text,
            "intent": chat_response.get("intent"),
            "trace_assistant": trace.get("assistant") if isinstance(trace, dict) else None,
        }
        rows.append(row)

        if not source_ok:
            failures.append(
                f"{item.id}: expected Claude/Sonnet/Anthropic source, got {source!r}"
            )
        if bad_text_hits:
            failures.append(
                f"{item.id}: response contains blocked artifacts {bad_text_hits}"
            )
        if missing_services:
            failures.append(
                f"{item.id}: missing expected services {missing_services}; detected={services}"
            )

        print(
            f"    source={source} source_ok={source_ok} "
            f"text_ok={text_ok} service_ok={service_ok}"
        )

    summary = {
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "base_url": base_url,
        "customer_id": args.customer_id,
        "message_count": len(TEST_MESSAGES),
        "passed_count": len(TEST_MESSAGES) - len({failure.split(':', 1)[0] for failure in failures}),
        "failure_count": len(failures),
        "valid": not failures,
        "failures": failures,
    }

    report = {
        "summary": summary,
        "rows": rows,
    }

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = out_dir / f"chat_lab_llm_source_canary_{timestamp}.json"
    md_path = out_dir / f"chat_lab_llm_source_canary_{timestamp}.md"

    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    md_lines = [
        "# Chat Lab LLM Source Canary",
        "",
        f"- Valid: **{summary['valid']}**",
        f"- Message count: **{summary['message_count']}**",
        f"- Failure count: **{summary['failure_count']}**",
        f"- Base URL: `{base_url}`",
        "",
        "## Failures",
        "",
    ]
    if failures:
        md_lines.extend(f"- {failure}" for failure in failures)
    else:
        md_lines.append("- None")

    md_lines.extend(["", "## Turn Results", ""])

    for row in rows:
        md_lines.extend(
            [
                f"### {row['id']}",
                "",
                f"- Thread: `{row['thread_id']}`",
                f"- Source: `{row['source']}`",
                f"- Source OK: **{row['source_ok']}**",
                f"- Text OK: **{row['text_ok']}**",
                f"- Service OK: **{row['service_ok']}**",
                f"- Detected services: `{row['detected_services']}`",
                f"- Missing services: `{row['missing_services']}`",
                "",
                "**User message:**",
                "",
                row["message"],
                "",
                "**Assistant response:**",
                "",
                row["response"] or "_No response text captured._",
                "",
            ]
        )

    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    print()
    print("json_report=", json_path)
    print("markdown_report=", md_path)
    print("valid=", summary["valid"])

    return 0 if summary["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
