from __future__ import annotations

# ruff: noqa: E402
import argparse
import asyncio
import html
import json
import os
import random
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import create_async_engine
from weasyprint import HTML

from scripts.data.run_production_component_performance_report import (
    TurnResult,
    analyze_results,
    create_jwt,
    percentile,
    seed_customer_record,
    send_turn,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run 100 production canary messages split into random 10-20 turn threads "
            "and generate JSON/Markdown/HTML/PDF analysis."
        )
    )
    parser.add_argument("--base-url", default=os.getenv("STAGING_API_BASE_URL"))
    parser.add_argument("--jwt-signing-key", default=os.getenv("JWT_SIGNING_KEY"))
    parser.add_argument("--customer-id", default=os.getenv("SMOKE_CUSTOMER_ID"))
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--output-dir", default="reports/production")
    parser.add_argument("--message-count", type=int, default=100)
    parser.add_argument("--min-thread-size", type=int, default=10)
    parser.add_argument("--max-thread-size", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20260514)
    parser.add_argument("--seed-customer", action="store_true")
    parser.add_argument("--pdf", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--pause-seconds", type=float, default=0.2)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    report = asyncio.run(
        build_threaded_report(
            base_url=args.base_url,
            jwt_signing_key=args.jwt_signing_key,
            customer_id=args.customer_id,
            database_url=args.database_url,
            output_dir=output_dir,
            message_count=args.message_count,
            min_thread_size=args.min_thread_size,
            max_thread_size=args.max_thread_size,
            seed=args.seed,
            seed_customer=args.seed_customer,
            write_pdf=args.pdf,
            timeout_seconds=args.timeout_seconds,
            pause_seconds=args.pause_seconds,
        )
    )

    return 0 if report["summary"]["valid"] else 1


async def build_threaded_report(
    *,
    base_url: str | None,
    jwt_signing_key: str | None,
    customer_id: str | None,
    database_url: str | None,
    output_dir: Path,
    message_count: int,
    min_thread_size: int,
    max_thread_size: int,
    seed: int,
    seed_customer: bool,
    write_pdf: bool,
    timeout_seconds: float,
    pause_seconds: float,
) -> dict[str, Any]:
    setup_errors = validate_setup(
        base_url=base_url,
        jwt_signing_key=jwt_signing_key,
        customer_id=customer_id,
        database_url=database_url,
        message_count=message_count,
        min_thread_size=min_thread_size,
        max_thread_size=max_thread_size,
    )
    if setup_errors:
        report = setup_error_report(setup_errors, base_url, customer_id)
        write_outputs(report, output_dir=output_dir, write_pdf=write_pdf)
        print_summary(report, output_dir=output_dir, write_pdf=write_pdf)
        return report

    rng = random.Random(seed)  # noqa: S311 - deterministic test shuffling only.
    normalized_customer_id = str(customer_id)
    messages = build_message_pool(message_count)
    rng.shuffle(messages)
    thread_sizes = build_thread_sizes(
        total=message_count,
        min_size=min_thread_size,
        max_size=max_thread_size,
        rng=rng,
    )

    engine = create_async_engine(str(database_url), pool_pre_ping=True)
    try:
        if seed_customer:
            await seed_customer_record(engine, normalized_customer_id)

        token = create_jwt(str(jwt_signing_key), normalized_customer_id)
        flat_results: list[TurnResult] = []
        thread_records: list[dict[str, Any]] = []

        cursor = 0
        global_turn = 1

        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            for thread_index, thread_size in enumerate(thread_sizes, start=1):
                thread_id: str | None = None
                last_sequence = 0
                group_results: list[TurnResult] = []

                for _local_turn in range(1, thread_size + 1):
                    message = messages[cursor]
                    cursor += 1

                    result = await send_turn(
                        client=client,
                        engine=engine,
                        base_url=str(base_url).rstrip("/"),
                        token=token,
                        message=message,
                        thread_id=thread_id,
                        turn_index=global_turn,
                        last_sequence=last_sequence,
                    )

                    group_results.append(result)
                    flat_results.append(result)

                    if result.thread_id:
                        thread_id = result.thread_id
                    if result.events:
                        last_sequence = max(int(event["sequence"]) for event in result.events)

                    global_turn += 1
                    await asyncio.sleep(pause_seconds)

                thread_records.append(
                    {
                        "thread_index": thread_index,
                        "thread_id": thread_id,
                        "planned_turns": thread_size,
                        "actual_turns": len(group_results),
                        "turn_indexes": [result.index for result in group_results],
                    }
                )

        base_report = analyze_results(
            flat_results,
            base_url=base_url,
            customer_id=normalized_customer_id,
            seeded_customer=seed_customer,
        )

        report = enrich_threaded_report(
            base_report=base_report,
            thread_records=thread_records,
            thread_sizes=thread_sizes,
            seed=seed,
            min_thread_size=min_thread_size,
            max_thread_size=max_thread_size,
        )

        write_outputs(report, output_dir=output_dir, write_pdf=write_pdf)
        print_summary(report, output_dir=output_dir, write_pdf=write_pdf)
        return report
    finally:
        await engine.dispose()


def validate_setup(
    *,
    base_url: str | None,
    jwt_signing_key: str | None,
    customer_id: str | None,
    database_url: str | None,
    message_count: int,
    min_thread_size: int,
    max_thread_size: int,
) -> list[str]:
    errors: list[str] = []
    if not base_url:
        errors.append("Missing --base-url or STAGING_API_BASE_URL.")
    if not jwt_signing_key:
        errors.append("Missing --jwt-signing-key or JWT_SIGNING_KEY.")
    if not customer_id:
        errors.append("Missing --customer-id or SMOKE_CUSTOMER_ID.")
    if not database_url:
        errors.append("Missing --database-url or DATABASE_URL.")
    if message_count < min_thread_size:
        errors.append("message-count must be >= min-thread-size.")
    if min_thread_size < 1:
        errors.append("min-thread-size must be >= 1.")
    if max_thread_size < min_thread_size:
        errors.append("max-thread-size must be >= min-thread-size.")
    return errors


def setup_error_report(
    setup_errors: list[str],
    base_url: str | None,
    customer_id: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "summary": {
            "valid": False,
            "generated_at": now_iso(),
            "base_url": base_url,
            "customer_id": customer_id,
            "message_count": 0,
            "thread_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "critical_issue_count": len(setup_errors),
            "soft_warning_count": 0,
            "errors": setup_errors,
        },
        "thread_summary": {},
        "component_summary": {},
        "turns": [],
        "safety_note": safety_note(),
    }


def build_thread_sizes(
    *,
    total: int,
    min_size: int,
    max_size: int,
    rng: random.Random,
) -> list[int]:
    remaining = total
    sizes: list[int] = []

    while remaining > 0:
        if remaining <= max_size:
            if remaining < min_size and sizes:
                sizes[-1] += remaining
            else:
                sizes.append(remaining)
            break

        size = rng.randint(min_size, max_size)
        if remaining - size < min_size:
            size = remaining - min_size

        sizes.append(size)
        remaining -= size

    rng.shuffle(sizes)
    return sizes


def enrich_threaded_report(
    *,
    base_report: dict[str, Any],
    thread_records: list[dict[str, Any]],
    thread_sizes: list[int],
    seed: int,
    min_thread_size: int,
    max_thread_size: int,
) -> dict[str, Any]:
    turns_by_index = {int(turn["turn"]): turn for turn in base_report["turns"]}
    enriched_threads = []

    for record in thread_records:
        turns = [turns_by_index[index] for index in record["turn_indexes"]]
        latencies = [float(turn["elapsed_ms"]) for turn in turns]
        issue_count = sum(int(turn["issue_count"]) for turn in turns)
        soft_warnings = sum(soft_warning_score(turn) for turn in turns)

        service_counts: dict[str, int] = {}
        intent_counts: dict[str, int] = {}
        for turn in turns:
            decision = turn["components"]["decision_layer"]
            service = decision.get("service_primary")
            intent = decision.get("query_primary")
            if service:
                service_counts[str(service)] = service_counts.get(str(service), 0) + 1
            if intent:
                intent_counts[str(intent)] = intent_counts.get(str(intent), 0) + 1

        enriched_threads.append(
            {
                **record,
                "success_count": sum(1 for turn in turns if turn["status_code"] == 200),
                "failure_count": sum(1 for turn in turns if turn["status_code"] != 200),
                "issue_count": issue_count,
                "soft_warning_score": soft_warnings,
                "avg_latency_ms": round(statistics.mean(latencies), 2) if latencies else 0.0,
                "p95_latency_ms": percentile(latencies, 95),
                "max_latency_ms": max(latencies) if latencies else 0.0,
                "intent_counts": intent_counts,
                "service_counts": service_counts,
            }
        )

    base_report["schema_version"] = 2
    base_report["summary"]["thread_count"] = len(enriched_threads)
    base_report["summary"]["random_seed"] = seed
    base_report["summary"]["min_thread_size"] = min_thread_size
    base_report["summary"]["max_thread_size"] = max_thread_size
    base_report["thread_summary"] = {
        "thread_count": len(enriched_threads),
        "thread_sizes": thread_sizes,
        "avg_thread_size": round(statistics.mean(thread_sizes), 2) if thread_sizes else 0.0,
        "min_thread_size": min(thread_sizes) if thread_sizes else 0,
        "max_thread_size": max(thread_sizes) if thread_sizes else 0,
        "threads": enriched_threads,
        "highest_latency_threads": sorted(
            enriched_threads,
            key=lambda row: float(row["p95_latency_ms"]),
            reverse=True,
        )[:5],
        "highest_warning_threads": sorted(
            enriched_threads,
            key=lambda row: int(row["soft_warning_score"]),
            reverse=True,
        )[:5],
    }
    base_report["safety_note"] = safety_note()
    return base_report


def soft_warning_score(turn: dict[str, Any]) -> int:
    components = turn["components"]
    providers = components["providers"]
    fallbacks = components["fallbacks"]
    quality = components["response_quality"]

    return (
        int(providers["timeout_count"])
        + int(providers["circuit_open_count"])
        + int(providers["failed_count"])
        + int(fallbacks["no_provider_votes_count"])
        + int(fallbacks["deterministic_hardening_count"])
        + int(fallbacks["trimatch_fallback_count"])
        + int(quality["possible_fragment_start"])
        + int(quality["table_format_warning"])
    )


def build_message_pool(message_count: int) -> list[str]:
    base_messages = [
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
        "Can you format a cookbook with photos, recipe cards, and print layout?",
        "My manuscript is 65,000 words. I need copyediting and interior formatting.",
        "Can you help publish on Amazon KDP only, not IngramSpark?",
        "I need a cover for a thriller novel and want to see portfolio examples.",
        "What information do you need before giving a quote?",
        "Can you help turn my podcast into a book?",
        "I want a children's book illustration package with cover and formatting.",
        "Can I get audiobook narration and ACX-ready files?",
        "Please explain the publishing process step by step.",
        "I need marketing help after my book goes live.",
        "Can you create a book trailer for Instagram and YouTube?",
        "I have an academic manuscript and need proofreading only.",
        "Do you work with first-time authors?",
        "Can you help me finish a half-written novel?",
        "What is the safest next step if I am not ready to share the full manuscript?",
        "Can you review my book idea and suggest the best BookCraft service?",
        "I need a website for my author brand and a landing page for the book.",
        "Can you help with metadata, categories, and book description?",
        "I need urgent formatting but do not invent a timeline.",
        "What happens after I approve a quote?",
        "Can you explain your editing levels without making up pricing?",
        "Can you prepare a distribution-ready EPUB and print PDF?",
        "I need help with a memoir but I want my voice preserved.",
        "Can you support a series with consistent covers and formatting?",
        "I need a professional blurb, author bio, and sales copy.",
        "What documents do you need to start publishing?",
        "Can you handle copyright page and ISBN guidance?",
        "I need a clean quote for cover design plus formatting.",
        "Can you help with a business book for lead generation?",
        "What should I choose: ghostwriting or developmental editing?",
        "Can you explain BookCraft services in simple terms?",
        "I want to know if you can work under NDA.",
        "Can you create launch marketing assets?",
        "My book has illustrations. Can you manage layout without damaging quality?",
        "Can you help with an ebook only, no print?",
        "Can you help with print only, no ebook?",
        "Can you improve readability and flow in my manuscript?",
        "I need a human-sounding rewrite, not generic AI writing.",
        "Can you help package my book for multiple platforms?",
        "What is the difference between proofreading and copyediting?",
    ]

    messages: list[str] = []
    while len(messages) < message_count:
        messages.extend(base_messages)
    return messages[:message_count]


def write_outputs(report: dict[str, Any], *, output_dir: Path, write_pdf: bool) -> None:
    json_path = output_dir / "production_threaded_component_load_report.json"
    md_path = output_dir / "production_threaded_component_load_report.md"
    html_path = output_dir / "production_threaded_component_load_report.html"
    pdf_path = output_dir / "production_threaded_component_load_report.pdf"

    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(report), encoding="utf-8")
    html_path.write_text(render_html(report), encoding="utf-8")

    if write_pdf:
        HTML(
            string=html_path.read_text(encoding="utf-8"),
            base_url=str(output_dir),
        ).write_pdf(pdf_path)


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    component = report["component_summary"]
    thread_summary = report["thread_summary"]

    lines = [
        "# Production Threaded Component Load Report",
        "",
        "## Executive Summary",
        "",
        f"- Valid: `{summary['valid']}`",
        f"- Messages: `{summary['message_count']}`",
        f"- Threads: `{summary['thread_count']}`",
        f"- Success: `{summary['success_count']}`",
        f"- Failures: `{summary['failure_count']}`",
        f"- Critical issues: `{summary['critical_issue_count']}`",
        f"- Soft warnings: `{summary['soft_warning_count']}`",
        f"- Avg latency ms: `{summary['avg_latency_ms']}`",
        f"- P95 latency ms: `{summary['p95_latency_ms']}`",
        "",
        "## Provider Health",
        "",
        f"- Total votes: `{component['provider_health']['total_vote_count']}`",
        f"- Usable votes: `{component['provider_health']['usable_vote_count']}`",
        (
            "- Turns with no usable votes: "
            f"`{component['provider_health']['turns_with_no_usable_votes']}`"
        ),
        f"- Timeouts: `{component['provider_health']['timeout_count']}`",
        f"- Circuit open: `{component['provider_health']['circuit_open_count']}`",
        f"- Failed votes: `{component['provider_health']['failed_count']}`",
        f"- Provider statuses: `{component['provider_health']['provider_status_counts']}`",
        "",
        "## Thread Summary",
        "",
        f"- Thread sizes: `{thread_summary['thread_sizes']}`",
        f"- Average thread size: `{thread_summary['avg_thread_size']}`",
        "",
        "| Thread | Turns | Success | Fail | Avg ms | P95 ms | Warnings | Top intents |",
        "|---:|---:|---:|---:|---:|---:|---:|---|",
    ]

    for thread in thread_summary["threads"]:
        lines.append(
            "| {idx} | {turns} | {success} | {fail} | {avg} | {p95} | {warn} | {intents} |".format(
                idx=thread["thread_index"],
                turns=thread["actual_turns"],
                success=thread["success_count"],
                fail=thread["failure_count"],
                avg=thread["avg_latency_ms"],
                p95=thread["p95_latency_ms"],
                warn=thread["soft_warning_score"],
                intents=json.dumps(thread["intent_counts"], sort_keys=True),
            )
        )

    lines.extend(
        [
            "",
            "## Response Quality",
            "",
            (
                "- Fragment starts: "
                f"`{component['response_quality']['possible_fragment_start_count']}`"
            ),
            (f"- Table warnings: `{component['response_quality']['table_format_warning_count']}`"),
            (f"- Empty responses: `{component['response_quality']['empty_response_count']}`"),
            "",
            "## Safety",
            "",
            report["safety_note"],
            "",
        ]
    )
    return "\n".join(lines)


def render_html(report: dict[str, Any]) -> str:
    summary = report["summary"]
    component = report["component_summary"]
    thread_summary = report["thread_summary"]

    thread_rows = []
    for thread in thread_summary["threads"]:
        thread_rows.append(
            f"""
            <tr>
              <td>{thread["thread_index"]}</td>
              <td>{thread["actual_turns"]}</td>
              <td>{thread["success_count"]}</td>
              <td>{thread["failure_count"]}</td>
              <td>{thread["avg_latency_ms"]}</td>
              <td>{thread["p95_latency_ms"]}</td>
              <td>{thread["soft_warning_score"]}</td>
              <td>{esc(thread["intent_counts"])}</td>
              <td>{esc(thread["service_counts"])}</td>
            </tr>
            """
        )

    return f"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Production Threaded Component Load Report</title>
<style>
@page {{ size: A4 landscape; margin: 12mm; }}
body {{ font-family: Arial, sans-serif; font-size: 10px; color: #111827; }}
h1, h2 {{ color: #111827; }}
.grid {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; margin: 12px 0; }}
.card {{ border: 1px solid #d1d5db; background: #f9fafb; padding: 8px; border-radius: 6px; }}
.label {{ color: #6b7280; font-size: 9px; }}
.value {{ font-size: 14px; font-weight: bold; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
th, td {{ border: 1px solid #d1d5db; padding: 5px; vertical-align: top; }}
th {{ background: #e5e7eb; }}
</style>
</head>
<body>
<h1>Production Threaded Component Load Report</h1>
<p>{esc(report["safety_note"])}</p>

<h2>Executive Summary</h2>
<div class="grid">
  {card("Valid", summary["valid"])}
  {card("Messages", summary["message_count"])}
  {card("Threads", summary["thread_count"])}
  {card("Success", summary["success_count"])}
  {card("Failures", summary["failure_count"])}
  {card("Critical issues", summary["critical_issue_count"])}
  {card("Soft warnings", summary["soft_warning_count"])}
  {card("Avg latency ms", summary["avg_latency_ms"])}
  {card("P95 latency ms", summary["p95_latency_ms"])}
  {card("Max latency ms", summary["max_latency_ms"])}
</div>

<h2>Provider, Fallback & Quality Health</h2>
<div class="grid">
  {card("Provider votes", component["provider_health"]["total_vote_count"])}
  {card("Usable votes", component["provider_health"]["usable_vote_count"])}
  {card("No usable vote turns", component["provider_health"]["turns_with_no_usable_votes"])}
  {card("Timeouts", component["provider_health"]["timeout_count"])}
  {card("Circuit open", component["provider_health"]["circuit_open_count"])}
  {card("Failed votes", component["provider_health"]["failed_count"])}
  {card("No-provider fallback", component["fallback_summary"]["no_provider_votes_count"])}
  {card("Deterministic hardening", component["fallback_summary"]["deterministic_hardening_count"])}
  {card("Tri-Match disagreements", component["trimatch_disagreement_count"])}
  {card("Fragment starts", component["response_quality"]["possible_fragment_start_count"])}
</div>

<h2>Thread Grid</h2>
<table>
<thead>
<tr>
<th>Thread</th><th>Turns</th><th>Success</th><th>Fail</th><th>Avg ms</th>
<th>P95 ms</th><th>Warnings</th><th>Intent Counts</th><th>Service Counts</th>
</tr>
</thead>
<tbody>
{"".join(thread_rows)}
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
    return html.escape(str(value))


def print_summary(report: dict[str, Any], *, output_dir: Path, write_pdf: bool) -> None:
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(f"json_report={output_dir / 'production_threaded_component_load_report.json'}")
    print(f"markdown_report={output_dir / 'production_threaded_component_load_report.md'}")
    print(f"html_report={output_dir / 'production_threaded_component_load_report.html'}")
    if write_pdf:
        print(f"pdf_report={output_dir / 'production_threaded_component_load_report.pdf'}")


def safety_note() -> str:
    return (
        "Production threaded diagnostic. It sends controlled test messages only to "
        "the configured API, does not send emails, does not create legal documents, "
        "does not create Elasticsearch indices, and does not move aliases."
    )


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
