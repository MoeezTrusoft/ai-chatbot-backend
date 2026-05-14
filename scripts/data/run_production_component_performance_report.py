from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import html
import json
import os
import statistics
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from weasyprint import HTML


@dataclass(slots=True)
class TurnResult:
    index: int
    message: str
    status_code: int | None
    elapsed_ms: float
    thread_id: str | None
    response: dict[str, Any] | None
    error: str | None
    events: list[dict[str, Any]]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run production canary messages and analyze component performance."
    )
    parser.add_argument("--base-url", default=os.getenv("STAGING_API_BASE_URL"))
    parser.add_argument("--jwt-signing-key", default=os.getenv("JWT_SIGNING_KEY"))
    parser.add_argument("--customer-id", default=os.getenv("SMOKE_CUSTOMER_ID"))
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--output-dir", default="reports/production")
    parser.add_argument("--message-count", type=int, default=10)
    parser.add_argument("--messages-file", default=None)
    parser.add_argument("--seed-customer", action="store_true")
    parser.add_argument("--pdf", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=45.0)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    report = asyncio.run(
        build_report(
            base_url=args.base_url,
            jwt_signing_key=args.jwt_signing_key,
            customer_id=args.customer_id,
            database_url=args.database_url,
            output_dir=output_dir,
            message_count=args.message_count,
            messages_file=args.messages_file,
            seed_customer=args.seed_customer,
            write_pdf=args.pdf,
            timeout_seconds=args.timeout_seconds,
        )
    )

    return 0 if report["summary"]["valid"] else 1


async def build_report(
    *,
    base_url: str | None,
    jwt_signing_key: str | None,
    customer_id: str | None,
    database_url: str | None,
    output_dir: Path,
    message_count: int,
    messages_file: str | None,
    seed_customer: bool,
    write_pdf: bool,
    timeout_seconds: float,
) -> dict[str, Any]:
    setup_errors: list[str] = []
    if not base_url:
        setup_errors.append("Missing --base-url or STAGING_API_BASE_URL.")
    if not jwt_signing_key:
        setup_errors.append("Missing --jwt-signing-key or JWT_SIGNING_KEY.")
    if not customer_id:
        setup_errors.append("Missing --customer-id or SMOKE_CUSTOMER_ID.")
    if not database_url:
        setup_errors.append("Missing --database-url or DATABASE_URL.")

    if setup_errors:
        report = {
            "schema_version": 1,
            "summary": {
                "valid": False,
                "generated_at": now_iso(),
                "base_url": base_url,
                "customer_id": customer_id,
                "seeded_customer": seed_customer,
                "message_count": 0,
                "success_count": 0,
                "failure_count": 0,
                "avg_latency_ms": 0.0,
                "p50_latency_ms": 0.0,
                "p95_latency_ms": 0.0,
                "max_latency_ms": 0.0,
                "critical_issue_count": len(setup_errors),
                "setup_error_count": len(setup_errors),
                "errors": setup_errors,
            },
            "turns": [],
            "component_summary": empty_component_summary(),
            "safety_note": safety_note(),
        }
        write_outputs(report, output_dir=output_dir, write_pdf=write_pdf)
        print_summary(report, output_dir=output_dir, write_pdf=write_pdf)
        return report

    normalized_customer_id = normalize_uuid(customer_id)
    engine = create_async_engine(database_url, pool_pre_ping=True)

    try:
        if seed_customer:
            await seed_customer_record(engine, normalized_customer_id)

        messages = load_messages(messages_file=messages_file, message_count=message_count)
        token = create_jwt(jwt_signing_key or "", normalized_customer_id)

        results: list[TurnResult] = []
        thread_id: str | None = None
        last_sequence = 0

        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            for index, message in enumerate(messages, start=1):
                result = await send_turn(
                    client=client,
                    engine=engine,
                    base_url=(base_url or "").rstrip("/"),
                    token=token,
                    message=message,
                    thread_id=thread_id,
                    turn_index=index,
                    last_sequence=last_sequence,
                )
                results.append(result)

                if result.thread_id:
                    thread_id = result.thread_id
                if result.events:
                    last_sequence = max(int(event["sequence"]) for event in result.events)

                await asyncio.sleep(0.2)

        report = analyze_results(
            results,
            base_url=base_url,
            customer_id=normalized_customer_id,
            seeded_customer=seed_customer,
        )
        write_outputs(report, output_dir=output_dir, write_pdf=write_pdf)
        print_summary(report, output_dir=output_dir, write_pdf=write_pdf)
        return report
    finally:
        await engine.dispose()


async def send_turn(
    *,
    client: httpx.AsyncClient,
    engine: Any,
    base_url: str,
    token: str,
    message: str,
    thread_id: str | None,
    turn_index: int,
    last_sequence: int,
) -> TurnResult:
    payload: dict[str, Any] = {
        "message": message,
        "correlation_id": f"production-component-canary-{turn_index:02d}",
    }
    if thread_id:
        payload["thread_id"] = thread_id

    started = time.perf_counter()
    status_code: int | None = None
    response_data: dict[str, Any] | None = None
    error: str | None = None
    resolved_thread_id = thread_id

    try:
        response = await client.post(
            f"{base_url}/api/v1/chat/turn",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-Correlation-ID": f"production-component-canary-{turn_index:02d}",
            },
            json=payload,
        )
        status_code = response.status_code
        try:
            response_data = response.json()
        except ValueError:
            response_data = {"raw_text": response.text[:2000]}

        if isinstance(response_data, dict) and isinstance(response_data.get("thread_id"), str):
            resolved_thread_id = response_data["thread_id"]

        if status_code >= 500:
            error = f"HTTP {status_code}: server error"
        elif status_code >= 400:
            error = f"HTTP {status_code}: client/auth error"
    except Exception as exc:  # noqa: BLE001
        error = f"{exc.__class__.__name__}: {exc}"

    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

    events: list[dict[str, Any]] = []
    if resolved_thread_id:
        events = await fetch_new_thread_events(
            engine=engine,
            thread_id=resolved_thread_id,
            last_sequence=last_sequence,
        )

    return TurnResult(
        index=turn_index,
        message=message,
        status_code=status_code,
        elapsed_ms=elapsed_ms,
        thread_id=resolved_thread_id,
        response=response_data,
        error=error,
        events=events,
    )


async def fetch_new_thread_events(
    *,
    engine: Any,
    thread_id: str,
    last_sequence: int,
) -> list[dict[str, Any]]:
    query = text(
        """
        SELECT sequence, event_type, payload, event_hash, created_at
        FROM thread_events
        WHERE thread_id = :thread_id
          AND sequence > :last_sequence
        ORDER BY sequence ASC
        """
    )
    async with engine.connect() as conn:
        rows = (
            (
                await conn.execute(
                    query,
                    {
                        "thread_id": UUID(thread_id),
                        "last_sequence": last_sequence,
                    },
                )
            )
            .mappings()
            .all()
        )

    return [
        {
            "sequence": int(row["sequence"]),
            "event_type": row["event_type"],
            "payload": row["payload"],
            "event_hash": row["event_hash"],
            "created_at": str(row["created_at"]),
        }
        for row in rows
    ]


async def seed_customer_record(engine: Any, customer_id: str) -> None:
    query = text(
        """
        INSERT INTO customers (
          id,
          email,
          phone,
          name,
          first_seen_at,
          last_seen_at,
          total_threads,
          total_quotes_value,
          has_signed_agreement,
          metadata,
          created_at,
          updated_at
        )
        VALUES (
          :id,
          :email,
          NULL,
          'Production Canary Customer',
          now(),
          now(),
          0,
          0.0,
          false,
          '{}'::json,
          now(),
          now()
        )
        ON CONFLICT (id) DO NOTHING
        """
    )
    async with engine.begin() as conn:
        await conn.execute(
            query,
            {
                "id": UUID(customer_id),
                "email": f"prod-canary-{customer_id}@example.test",
            },
        )


def analyze_results(
    results: list[TurnResult],
    *,
    base_url: str | None,
    customer_id: str,
    seeded_customer: bool,
) -> dict[str, Any]:
    turn_dicts = [analyze_turn(result) for result in results]
    component_summary = summarize_components(turn_dicts)
    latencies = [turn["elapsed_ms"] for turn in turn_dicts]
    failures = [turn for turn in turn_dicts if turn["error"]]

    summary = {
        "valid": not failures and component_summary["critical_issue_count"] == 0,
        "generated_at": now_iso(),
        "base_url": base_url,
        "customer_id": customer_id,
        "seeded_customer": seeded_customer,
        "message_count": len(turn_dicts),
        "success_count": sum(1 for turn in turn_dicts if turn["status_code"] == 200),
        "failure_count": len(failures),
        "avg_latency_ms": round(statistics.mean(latencies), 2) if latencies else 0.0,
        "p50_latency_ms": percentile(latencies, 50),
        "p95_latency_ms": percentile(latencies, 95),
        "max_latency_ms": max(latencies) if latencies else 0.0,
        "critical_issue_count": component_summary["critical_issue_count"],
    }

    return {
        "schema_version": 1,
        "summary": summary,
        "component_summary": component_summary,
        "turns": turn_dicts,
        "safety_note": safety_note(),
    }


def analyze_turn(result: TurnResult) -> dict[str, Any]:
    by_type: dict[str, list[dict[str, Any]]] = {}
    for event in result.events:
        by_type.setdefault(str(event["event_type"]), []).append(event)

    response = result.response or {}
    intent = response.get("intent") if isinstance(response.get("intent"), dict) else {}

    trimatch = latest_payload(by_type, "trimatch.voted")
    intent_event = latest_payload(by_type, "intent.classified")
    extraction = latest_payload(by_type, "extraction.applied")
    trg = latest_payload(by_type, "trg.updated")
    trg_failed = latest_payload(by_type, "trg.failed")
    rag_failed = latest_payload(by_type, "rag.failed")
    assistant = latest_payload(by_type, "assistant.response")
    disagreement = latest_payload(by_type, "trimatch.disagreement_observed")

    event_types = [event["event_type"] for event in result.events]

    component = {
        "language_status": response.get("language_status"),
        "decision_layer": {
            "intent_present": bool(intent),
            "query_primary": intent.get("query_primary"),
            "service_primary": intent.get("service_primary"),
            "funnel_stage": intent.get("funnel_stage"),
            "confidence": intent.get("confidence"),
            "rationale": intent.get("rationale"),
            "evidence": intent.get("evidence", []),
        },
        "trimatch": {
            "present": trimatch is not None,
            "query_primary": safe_get(trimatch, "query_primary"),
            "service_primary": safe_get(trimatch, "service_primary"),
            "funnel_stage": safe_get(trimatch, "funnel_stage"),
            "confidence": safe_get(trimatch, "confidence"),
            "shortcut_eligible": safe_get(trimatch, "shortcut_eligible"),
            "disagreement_logged": disagreement is not None,
        },
        "intent_classifier": {
            "present": intent_event is not None,
            "query_primary": safe_get(intent_event, "intent", "query_primary"),
            "service_primary": safe_get(intent_event, "intent", "service_primary"),
            "confidence": safe_get(intent_event, "intent", "confidence"),
            "provider_vote_keys": provider_vote_keys(intent_event),
        },
        "extraction": {
            "present": extraction is not None,
            "delta_count": safe_get(extraction, "delta_count"),
        },
        "trg": {
            "present": trg is not None,
            "failed": trg_failed is not None,
            "node_count": safe_get(trg, "node_count"),
            "edge_count": safe_get(trg, "edge_count"),
            "unresolved_question_count": safe_get(trg, "unresolved_question_count"),
            "contradiction_count": safe_get(trg, "contradiction_count"),
        },
        "rag": {
            "failed": rag_failed is not None,
            "rag_events": [event for event in event_types if str(event).startswith("rag.")],
        },
        "assistant": {
            "present": assistant is not None,
            "source": safe_get(assistant, "source"),
            "bubble_count": len(response.get("bubbles", []))
            if isinstance(response.get("bubbles"), list)
            else 0,
            "text_preview": response_text_preview(response),
        },
    }

    issues = detect_turn_issues(
        component=component,
        status_code=result.status_code,
        error=result.error,
    )

    return {
        "turn": result.index,
        "message": result.message,
        "status_code": result.status_code,
        "elapsed_ms": result.elapsed_ms,
        "thread_id": result.thread_id,
        "error": result.error,
        "issue_count": len(issues),
        "issues": issues,
        "event_count": len(result.events),
        "event_types": event_types,
        "components": component,
        "raw_response": result.response,
        "raw_events": result.events,
    }


def empty_component_summary() -> dict[str, Any]:
    return {
        "critical_issue_count": 0,
        "turn_count": 0,
        "http_failure_count": 0,
        "decision_layer_missing_count": 0,
        "trimatch_missing_count": 0,
        "trimatch_disagreement_count": 0,
        "intent_classifier_missing_count": 0,
        "extraction_missing_count": 0,
        "trg_missing_count": 0,
        "trg_failed_count": 0,
        "rag_failed_count": 0,
        "assistant_missing_count": 0,
        "intent_counts": {},
        "service_counts": {},
        "latency_buckets": {
            "under_1000ms": 0,
            "1000_to_3000ms": 0,
            "3000_to_8000ms": 0,
            "over_8000ms": 0,
        },
    }


def summarize_components(turns: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {
        "critical_issue_count": 0,
        "turn_count": len(turns),
        "http_failure_count": 0,
        "decision_layer_missing_count": 0,
        "trimatch_missing_count": 0,
        "trimatch_disagreement_count": 0,
        "intent_classifier_missing_count": 0,
        "extraction_missing_count": 0,
        "trg_missing_count": 0,
        "trg_failed_count": 0,
        "rag_failed_count": 0,
        "assistant_missing_count": 0,
        "intent_counts": {},
        "service_counts": {},
        "latency_buckets": {
            "under_1000ms": 0,
            "1000_to_3000ms": 0,
            "3000_to_8000ms": 0,
            "over_8000ms": 0,
        },
    }

    for turn in turns:
        components = turn["components"]
        if turn["status_code"] != 200:
            summary["http_failure_count"] += 1
        if not components["decision_layer"]["intent_present"]:
            summary["decision_layer_missing_count"] += 1
        if not components["trimatch"]["present"]:
            summary["trimatch_missing_count"] += 1
        if components["trimatch"]["disagreement_logged"]:
            summary["trimatch_disagreement_count"] += 1
        if not components["intent_classifier"]["present"]:
            summary["intent_classifier_missing_count"] += 1
        if not components["extraction"]["present"]:
            summary["extraction_missing_count"] += 1
        if not components["trg"]["present"]:
            summary["trg_missing_count"] += 1
        if components["trg"]["failed"]:
            summary["trg_failed_count"] += 1
        if components["rag"]["failed"]:
            summary["rag_failed_count"] += 1
        if not components["assistant"]["present"]:
            summary["assistant_missing_count"] += 1

        intent = components["decision_layer"]["query_primary"]
        service = components["decision_layer"]["service_primary"]
        if intent:
            summary["intent_counts"][intent] = summary["intent_counts"].get(intent, 0) + 1
        if service:
            summary["service_counts"][service] = summary["service_counts"].get(service, 0) + 1

        latency = turn["elapsed_ms"]
        if latency < 1000:
            summary["latency_buckets"]["under_1000ms"] += 1
        elif latency < 3000:
            summary["latency_buckets"]["1000_to_3000ms"] += 1
        elif latency < 8000:
            summary["latency_buckets"]["3000_to_8000ms"] += 1
        else:
            summary["latency_buckets"]["over_8000ms"] += 1

    critical_fields = [
        "http_failure_count",
        "decision_layer_missing_count",
        "trimatch_missing_count",
        "intent_classifier_missing_count",
        "trg_missing_count",
        "trg_failed_count",
        "rag_failed_count",
        "assistant_missing_count",
    ]
    summary["critical_issue_count"] = sum(int(summary[field]) for field in critical_fields)

    return summary


def detect_turn_issues(
    *,
    component: dict[str, Any],
    status_code: int | None,
    error: str | None,
) -> list[str]:
    issues: list[str] = []
    if status_code != 200:
        issues.append(f"http_status_not_200:{status_code}")
    if error:
        issues.append(f"request_error:{error}")
    if not component["decision_layer"]["intent_present"]:
        issues.append("decision_layer_missing_intent")
    if not component["trimatch"]["present"]:
        issues.append("trimatch_missing")
    if not component["intent_classifier"]["present"]:
        issues.append("intent_classifier_missing")
    if not component["trg"]["present"]:
        issues.append("trg_missing")
    if component["trg"]["failed"]:
        issues.append("trg_failed")
    if component["rag"]["failed"]:
        issues.append("rag_failed")
    if not component["assistant"]["present"]:
        issues.append("assistant_event_missing")
    return issues


def latest_payload(
    by_type: dict[str, list[dict[str, Any]]],
    event_type: str,
) -> dict[str, Any] | None:
    events = by_type.get(event_type) or []
    if not events:
        return None
    payload = events[-1].get("payload")
    return payload if isinstance(payload, dict) else None


def safe_get(data: dict[str, Any] | None, *path: str) -> Any:
    current: Any = data
    for item in path:
        if not isinstance(current, dict):
            return None
        current = current.get(item)
    return current


def provider_vote_keys(intent_event: dict[str, Any] | None) -> list[str]:
    if not isinstance(intent_event, dict):
        return []
    votes = intent_event.get("votes")
    if isinstance(votes, dict):
        return sorted(str(key) for key in votes)
    if isinstance(votes, list):
        return [f"vote_{index}" for index, _ in enumerate(votes, start=1)]
    return []


def response_text_preview(response: dict[str, Any]) -> str:
    bubbles = response.get("bubbles")
    if not isinstance(bubbles, list):
        return ""
    texts = []
    for bubble in bubbles:
        if isinstance(bubble, dict) and isinstance(bubble.get("text"), str):
            texts.append(bubble["text"])
    return "\n".join(texts)[:500]


def load_messages(*, messages_file: str | None, message_count: int) -> list[str]:
    if messages_file:
        path = Path(messages_file)
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
            raise ValueError("--messages-file must contain a JSON array of strings.")
        return data[:message_count]
    return default_messages()[:message_count]


def default_messages() -> list[str]:
    return [
        "I need ghostwriting for a 40,000-word memoir. What do you need from me?",
        "Can you help with editing and proofreading a business book manuscript?",
        "I want book cover design and publishing distribution for KDP and IngramSpark.",
        "Do you provide NDA before sharing manuscript details?",
        "Can you estimate timeline for formatting and publishing?",
        "I have no manuscript yet, just an idea for a children's picture book.",
        "I need pricing, samples, and NDA, but do not invent links or numbers.",
        "Can you compare ghostwriting vs coaching vs manuscript completion?",
        "I may need audiobook production and a video trailer too.",
        "Summarize what BookCraft knows about my project and the next safe step.",
    ]


def create_jwt(signing_key: str, customer_id: str) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": "production-canary",
        "customer_id": customer_id,
        "scope": "chat:write",
        "iat": now,
        "nbf": now - 5,
        "exp": now + 900,
    }
    raw_header = b64url_json(header)
    raw_payload = b64url_json(payload)
    signing_input = f"{raw_header}.{raw_payload}"
    signature = hmac.new(
        signing_key.encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{signing_input}.{b64url(signature)}"


def b64url_json(value: dict[str, Any]) -> str:
    return b64url(json.dumps(value, separators=(",", ":"), sort_keys=True).encode())


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def normalize_uuid(value: str | None) -> str:
    if not value:
        return str(uuid4())
    return str(UUID(value))


def percentile(values: list[float], pct: int) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    index = min(len(sorted_values) - 1, round((pct / 100) * (len(sorted_values) - 1)))
    return round(sorted_values[index], 2)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def safety_note() -> str:
    return (
        "Production canary diagnostic. It sends test messages only to the configured API, "
        "does not send emails, does not create legal documents, does not create "
        "Elasticsearch indices, and does not move aliases."
    )


def write_outputs(report: dict[str, Any], *, output_dir: Path, write_pdf: bool) -> None:
    json_path = output_dir / "production_component_performance_report.json"
    md_path = output_dir / "production_component_performance_report.md"
    html_path = output_dir / "production_component_performance_report.html"
    pdf_path = output_dir / "production_component_performance_report.pdf"

    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(markdown(report), encoding="utf-8")
    html_path.write_text(render_html(report), encoding="utf-8")
    if write_pdf:
        HTML(
            string=html_path.read_text(encoding="utf-8"),
            base_url=str(output_dir),
        ).write_pdf(pdf_path)


def print_summary(report: dict[str, Any], *, output_dir: Path, write_pdf: bool) -> None:
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(f"json_report={output_dir / 'production_component_performance_report.json'}")
    print(f"markdown_report={output_dir / 'production_component_performance_report.md'}")
    print(f"html_report={output_dir / 'production_component_performance_report.html'}")
    if write_pdf:
        print(f"pdf_report={output_dir / 'production_component_performance_report.pdf'}")


def markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    component = report["component_summary"]
    lines = [
        "# Production Component Performance Report",
        "",
        "## Summary",
        "",
        f"- Generated at: `{summary['generated_at']}`",
        f"- Valid: `{summary['valid']}`",
        f"- Messages: `{summary['message_count']}`",
        f"- Success: `{summary['success_count']}`",
        f"- Failures: `{summary['failure_count']}`",
        f"- Avg latency ms: `{summary['avg_latency_ms']}`",
        f"- P95 latency ms: `{summary['p95_latency_ms']}`",
        f"- Critical issues: `{summary['critical_issue_count']}`",
        "",
        "## Component Summary",
        "",
        f"- HTTP failures: `{component['http_failure_count']}`",
        f"- Decision layer missing: `{component['decision_layer_missing_count']}`",
        f"- Tri-Match missing: `{component['trimatch_missing_count']}`",
        f"- Tri-Match disagreements: `{component['trimatch_disagreement_count']}`",
        f"- Intent classifier missing: `{component['intent_classifier_missing_count']}`",
        f"- Extraction missing: `{component['extraction_missing_count']}`",
        f"- TRG missing: `{component['trg_missing_count']}`",
        f"- TRG failed: `{component['trg_failed_count']}`",
        f"- RAG failed: `{component['rag_failed_count']}`",
        "",
        "## Turns",
        "",
        "| # | HTTP | Latency ms | Intent | Service | TRG | Tri-Match | Issues | Message |",
        "|---:|---:|---:|---|---|---|---|---:|---|",
    ]

    for turn in report["turns"]:
        c = turn["components"]
        lines.append(
            (
                "| {turn} | {status} | {latency} | `{intent}` | `{service}` | "
                "`{trg}` | `{tm}` | {issues} | {message} |"
            ).format(
                turn=turn["turn"],
                status=turn["status_code"],
                latency=turn["elapsed_ms"],
                intent=c["decision_layer"]["query_primary"] or "",
                service=c["decision_layer"]["service_primary"] or "",
                trg="ok" if c["trg"]["present"] and not c["trg"]["failed"] else "bad",
                tm="ok" if c["trimatch"]["present"] else "missing",
                issues=turn["issue_count"],
                message=turn["message"].replace("|", "\\|")[:120],
            )
        )

    lines.extend(["", "## Safety Note", "", report["safety_note"], ""])
    return "\n".join(lines)


def render_html(report: dict[str, Any]) -> str:
    summary = report["summary"]
    component = report["component_summary"]
    rows = []
    for turn in report["turns"]:
        c = turn["components"]
        rows.append(
            f"""
            <tr>
              <td>{turn["turn"]}</td>
              <td>{turn["status_code"]}</td>
              <td>{turn["elapsed_ms"]}</td>
              <td>{esc(c["decision_layer"]["query_primary"])}</td>
              <td>{esc(c["decision_layer"]["service_primary"])}</td>
              <td>{esc(c["decision_layer"]["funnel_stage"])}</td>
              <td>{"yes" if c["trimatch"]["present"] else "no"}</td>
              <td>{"yes" if c["intent_classifier"]["present"] else "no"}</td>
              <td>{"yes" if c["trg"]["present"] else "no"}</td>
              <td>{"yes" if c["rag"]["failed"] else "no"}</td>
              <td>{turn["issue_count"]}</td>
              <td>{esc(turn["message"])}</td>
            </tr>
            """
        )

    return f"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Production Component Performance Report</title>
<style>
@page {{ size: A4 landscape; margin: 14mm; }}
body {{ font-family: Arial, sans-serif; font-size: 10px; color: #1f2937; }}
h1, h2 {{ color: #111827; }}
.grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-bottom: 16px; }}
.card {{ border: 1px solid #d1d5db; padding: 8px; border-radius: 6px; background: #f9fafb; }}
.label {{ color: #6b7280; font-size: 9px; }}
.value {{ font-size: 14px; font-weight: bold; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
th, td {{ border: 1px solid #d1d5db; padding: 5px; vertical-align: top; }}
th {{ background: #e5e7eb; }}
.ok {{ color: #065f46; font-weight: bold; }}
.bad {{ color: #991b1b; font-weight: bold; }}
</style>
</head>
<body>
<h1>Production Component Performance Report</h1>
<p>{esc(report["safety_note"])}</p>

<h2>Executive Summary</h2>
<div class="grid">
  {card("Valid", summary["valid"])}
  {card("Messages", summary["message_count"])}
  {card("Success", summary["success_count"])}
  {card("Failures", summary["failure_count"])}
  {card("Avg latency ms", summary["avg_latency_ms"])}
  {card("P95 latency ms", summary["p95_latency_ms"])}
  {card("Max latency ms", summary["max_latency_ms"])}
  {card("Critical issues", summary["critical_issue_count"])}
</div>

<h2>Component Health</h2>
<div class="grid">
  {card("HTTP failures", component["http_failure_count"])}
  {card("Decision layer missing", component["decision_layer_missing_count"])}
  {card("Tri-Match missing", component["trimatch_missing_count"])}
  {card("Tri-Match disagreements", component["trimatch_disagreement_count"])}
  {card("Intent classifier missing", component["intent_classifier_missing_count"])}
  {card("Extraction missing", component["extraction_missing_count"])}
  {card("TRG missing", component["trg_missing_count"])}
  {card("TRG failed", component["trg_failed_count"])}
  {card("RAG failed", component["rag_failed_count"])}
  {card("Assistant missing", component["assistant_missing_count"])}
</div>

<h2>Per-Turn Grid</h2>
<table>
<thead>
<tr>
<th>#</th><th>HTTP</th><th>Latency</th><th>Intent</th><th>Service</th><th>Stage</th>
<th>Tri-Match</th><th>NLP/Intent</th><th>TRG</th><th>RAG Failed</th><th>Issues</th><th>Message</th>
</tr>
</thead>
<tbody>
{"".join(rows)}
</tbody>
</table>
</body>
</html>
"""


def card(label: str, value: Any) -> str:
    return (
        f'<div class="card"><div class="label">{esc(label)}</div>'
        f'<div class="value">{esc(value)}</div></div>'
    )


def esc(value: Any) -> str:
    if value is None:
        return ""
    return html.escape(str(value))


if __name__ == "__main__":
    raise SystemExit(main())
