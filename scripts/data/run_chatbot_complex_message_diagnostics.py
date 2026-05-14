from __future__ import annotations

# ruff: noqa: E501 - diagnostic fixture messages are intentionally readable.
import argparse
import asyncio
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from elasticsearch import AsyncElasticsearch

from bookcraft.api.chat import ChatTurnRequest
from bookcraft.components.rag.retriever import RagRetriever
from bookcraft.infra.config import Settings


@dataclass(slots=True)
class TracingRagRetriever:
    delegate: RagRetriever
    calls: list[dict[str, Any]]

    async def retrieve(self, processed_message, intent, top_k: int = 8):  # type: ignore[no-untyped-def]
        started = time.perf_counter()
        chunks = await self.delegate.retrieve(processed_message, intent, top_k=top_k)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        self.calls.append(
            {
                "normalized_query": processed_message.normalized,
                "query_intent": intent.query_primary.value,
                "service_intent": intent.service_primary.value if intent.service_primary else None,
                "top_k": top_k,
                "chunk_count": len(chunks),
                "elapsed_ms": elapsed_ms,
                "chunks": [
                    {
                        "chunk_id": chunk.chunk_id,
                        "source_id": chunk.source_id,
                        "service_category": chunk.service_category.value
                        if chunk.service_category
                        else None,
                        "title": chunk.title,
                        "section": chunk.section,
                        "score": chunk.score,
                    }
                    for chunk in chunks[:5]
                ],
            }
        )
        return chunks


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run 50 complex chatbot messages and collect component diagnostics."
    )
    parser.add_argument("--output-dir", default="reports/chatbot")
    parser.add_argument(
        "--check-rag",
        action="store_true",
        help="Attach Elasticsearch RAG retriever and collect retrieval details.",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    report = asyncio.run(
        build_report(
            output_dir=output_dir,
            check_rag=args.check_rag,
        )
    )

    json_path = output_dir / "complex_message_diagnostic_report.json"
    md_path = output_dir / "complex_message_diagnostic_report.md"

    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(markdown(report), encoding="utf-8")

    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(f"json_report={json_path}")
    print(f"markdown_report={md_path}")

    return 0 if report["summary"]["valid"] else 1


async def build_report(
    *,
    output_dir: Path,
    check_rag: bool,
) -> dict[str, Any]:
    del output_dir

    os.environ.setdefault("APP_ENV", "test")
    os.environ.setdefault("LLM_PROVIDER_MODE", "mock")
    os.environ.setdefault("TRIMATCH_EXTRA_MODE", "off")
    os.environ.setdefault("TEI_DEGRADED_MODE_ENABLED", "true")

    from bookcraft.api.main import build_chat_service, build_trg_engine

    settings = Settings(
        app_env="test",
        llm_provider_mode="mock",
        tei_degraded_mode_enabled=True,
    )

    es_client: AsyncElasticsearch | None = None
    tracing_rag: TracingRagRetriever | None = None

    if check_rag:
        es_client = AsyncElasticsearch(settings.elasticsearch_url)
        tracing_rag = TracingRagRetriever(
            delegate=RagRetriever(
                client=es_client,
                index_alias=settings.rag_index_alias,
            ),
            calls=[],
        )

    try:
        service = build_chat_service(
            settings,
            rag_retriever=tracing_rag,
            trg_engine=build_trg_engine(settings),
        )

        turns: list[dict[str, Any]] = []
        thread_id: UUID | None = None
        previous_event_count = 0

        for index, message in enumerate(test_messages(), start=1):
            started = time.perf_counter()
            response = await service.handle_turn(
                ChatTurnRequest(
                    thread_id=thread_id,
                    message=message,
                    correlation_id=f"diagnostic-{index:02d}",
                )
            )
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            thread_id = response.thread_id

            memory = service.threads[thread_id]
            events = memory.events
            new_events = events[previous_event_count:]
            previous_event_count = len(events)

            turns.append(
                analyze_turn(
                    index=index,
                    message=message,
                    elapsed_ms=elapsed_ms,
                    response=response,
                    new_events=new_events,
                    rag_calls=tracing_rag.calls if tracing_rag is not None else [],
                    state=memory.state,
                )
            )

        summary = summarize(turns, check_rag=check_rag)

        return {
            "schema_version": 1,
            "summary": summary,
            "turns": turns,
            "safety_note": (
                "Diagnostic only. It does not send real customer messages, does not "
                "create documents, does not change Elasticsearch aliases, and uses "
                "mock response generation by default."
            ),
        }
    finally:
        if es_client is not None:
            await es_client.close()


def analyze_turn(
    *,
    index: int,
    message: str,
    elapsed_ms: float,
    response: Any,
    new_events: list[dict[str, Any]],
    rag_calls: list[dict[str, Any]],
    state: Any,
) -> dict[str, Any]:
    by_type: dict[str, list[dict[str, Any]]] = {}
    for event in new_events:
        by_type.setdefault(str(event.get("event_type")), []).append(event)

    trimatch_vote = latest_payload(by_type, "trimatch.voted")
    intent_event = latest_payload(by_type, "intent.classified")
    extraction_event = latest_payload(by_type, "extraction.applied")
    trg_event = latest_payload(by_type, "trg.updated")
    trg_failed = latest_payload(by_type, "trg.failed")
    assistant_event = latest_payload(by_type, "assistant.response")
    disagreement_event = latest_payload(by_type, "trimatch.disagreement_observed")
    rag_failed = latest_payload(by_type, "rag.failed")

    response_text = "\n".join(bubble.text for bubble in response.bubbles)

    intent = response.intent.model_dump(mode="json") if response.intent else None
    latest_rag_call = rag_calls[-1] if rag_calls else None

    return {
        "turn": index,
        "message": message,
        "elapsed_ms": elapsed_ms,
        "language_status": response.language_status,
        "event_types": [str(event.get("event_type")) for event in new_events],
        "event_count": len(new_events),
        "intent": intent,
        "trimatch": {
            "present": trimatch_vote is not None,
            "query_primary": safe_get(trimatch_vote, "query_primary"),
            "service_primary": safe_get(trimatch_vote, "service_primary"),
            "funnel_stage": safe_get(trimatch_vote, "funnel_stage"),
            "confidence": safe_get(trimatch_vote, "confidence"),
            "evidence_count": len(trimatch_vote.get("evidence", []))
            if isinstance(trimatch_vote, dict)
            else 0,
            "shortcut_eligible": safe_get(trimatch_vote, "shortcut_eligible"),
            "disagreement_logged": disagreement_event is not None,
        },
        "intent_classifier": {
            "present": intent_event is not None,
            "query_primary": safe_get(intent_event, "intent", "query_primary"),
            "service_primary": safe_get(intent_event, "intent", "service_primary"),
            "funnel_stage": safe_get(intent_event, "intent", "funnel_stage"),
            "confidence": safe_get(intent_event, "intent", "confidence"),
        },
        "extraction": {
            "present": extraction_event is not None,
            "delta_count": safe_get(extraction_event, "delta_count"),
        },
        "trg": {
            "present": trg_event is not None,
            "failed": trg_failed is not None,
            "node_count": safe_get(trg_event, "node_count"),
            "edge_count": safe_get(trg_event, "edge_count"),
            "unresolved_question_count": safe_get(trg_event, "unresolved_question_count"),
            "contradiction_count": safe_get(trg_event, "contradiction_count"),
        },
        "rag": {
            "checked": latest_rag_call is not None,
            "failed": rag_failed is not None,
            "chunk_count": latest_rag_call.get("chunk_count") if latest_rag_call else 0,
            "elapsed_ms": latest_rag_call.get("elapsed_ms") if latest_rag_call else None,
            "chunks": latest_rag_call.get("chunks") if latest_rag_call else [],
        },
        "assistant": {
            "bubble_count": len(response.bubbles),
            "source": safe_get(assistant_event, "source"),
            "text_preview": response_text[:320],
        },
        "state": state_snapshot(state),
    }


def latest_payload(
    by_type: dict[str, list[dict[str, Any]]], event_type: str
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


def state_snapshot(state: Any) -> dict[str, Any]:
    return {
        "sales_stage": getattr(state.sales_stage, "value", None),
        "genre": getattr(state.project.genre, "value", None),
        "word_count": getattr(state.project.word_count, "value", None),
        "page_count": getattr(state.project.page_count, "value", None),
        "manuscript_status": getattr(state.project.manuscript_status, "value", None),
        "services_discussed": [item.service.value for item in state.project.services_discussed],
        "latest_quote_id": getattr(state.commercial.latest_quote_id, "value", None),
    }


def summarize(turns: list[dict[str, Any]], *, check_rag: bool) -> dict[str, Any]:
    failed_trg = [turn for turn in turns if turn["trg"]["failed"]]
    missing_trg = [turn for turn in turns if not turn["trg"]["present"]]
    missing_trimatch = [turn for turn in turns if not turn["trimatch"]["present"]]
    missing_intent = [turn for turn in turns if not turn["intent_classifier"]["present"]]
    rag_failures = [turn for turn in turns if turn["rag"]["failed"]]
    disagreement_count = sum(1 for turn in turns if turn["trimatch"]["disagreement_logged"])

    avg_latency = round(sum(turn["elapsed_ms"] for turn in turns) / max(1, len(turns)), 2)
    p95_latency = percentile([turn["elapsed_ms"] for turn in turns], 95)

    intent_counts: dict[str, int] = {}
    service_counts: dict[str, int] = {}
    for turn in turns:
        intent = turn.get("intent") or {}
        query_primary = intent.get("query_primary")
        service_primary = intent.get("service_primary")
        if query_primary:
            intent_counts[query_primary] = intent_counts.get(query_primary, 0) + 1
        if service_primary:
            service_counts[service_primary] = service_counts.get(service_primary, 0) + 1

    valid = (
        len(turns) == 50
        and not failed_trg
        and not missing_trg
        and not missing_trimatch
        and not missing_intent
        and not rag_failures
    )

    return {
        "valid": valid,
        "generated_at": datetime.now(UTC).isoformat(),
        "message_count": len(turns),
        "check_rag": check_rag,
        "avg_turn_latency_ms": avg_latency,
        "p95_turn_latency_ms": p95_latency,
        "trg_failed_count": len(failed_trg),
        "trg_missing_count": len(missing_trg),
        "trimatch_missing_count": len(missing_trimatch),
        "trimatch_disagreement_count": disagreement_count,
        "intent_missing_count": len(missing_intent),
        "rag_failure_count": len(rag_failures),
        "intent_counts": intent_counts,
        "service_counts": service_counts,
    }


def percentile(values: list[float], pct: int) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    index = min(len(values) - 1, round((pct / 100) * (len(values) - 1)))
    return round(values[index], 2)


def markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# BookCraft Chatbot Complex Message Diagnostic Report",
        "",
        "## Summary",
        "",
        f"- Generated at: `{summary['generated_at']}`",
        f"- Valid: `{summary['valid']}`",
        f"- Messages: `{summary['message_count']}`",
        f"- RAG checked: `{summary['check_rag']}`",
        f"- Average latency ms: `{summary['avg_turn_latency_ms']}`",
        f"- P95 latency ms: `{summary['p95_turn_latency_ms']}`",
        f"- TRG failed: `{summary['trg_failed_count']}`",
        f"- TRG missing: `{summary['trg_missing_count']}`",
        f"- Tri-Match missing: `{summary['trimatch_missing_count']}`",
        f"- Tri-Match disagreements: `{summary['trimatch_disagreement_count']}`",
        f"- Intent missing: `{summary['intent_missing_count']}`",
        f"- RAG failures: `{summary['rag_failure_count']}`",
        "",
        "## Turns",
        "",
        "| # | Intent | Service | Tri-Match | TRG nodes/edges | RAG chunks | Latency ms | Message |",
        "|---:|---|---|---|---|---:|---:|---|",
    ]

    for turn in report["turns"]:
        intent = turn.get("intent") or {}
        lines.append(
            "| {turn} | `{intent}` | `{service}` | `{trimatch}` | `{nodes}/{edges}` | "
            "{rag_chunks} | {latency} | {message} |".format(
                turn=turn["turn"],
                intent=intent.get("query_primary") or "",
                service=intent.get("service_primary") or "",
                trimatch=turn["trimatch"]["query_primary"] or "",
                nodes=turn["trg"]["node_count"] or 0,
                edges=turn["trg"]["edge_count"] or 0,
                rag_chunks=turn["rag"]["chunk_count"],
                latency=turn["elapsed_ms"],
                message=turn["message"].replace("|", "\\|")[:120],
            )
        )

    lines.extend(
        [
            "",
            "## Safety Note",
            "",
            report["safety_note"],
            "",
        ]
    )
    return "\n".join(lines)


def test_messages() -> list[str]:
    return [
        "Hi, I am planning a memoir but I only have voice notes, scattered journals, and maybe 60,000 words of messy material.",
        "I need ghostwriting, but not full ghostwriting; maybe coaching plus writing, and I need my voice protected.",
        "Actually, forget memoir for a second. Could this become a business leadership book with case studies?",
        "I have 220 pages in Google Docs, but some chapters are duplicated and some are just bullet notes.",
        "How much would ghostwriting cost for this if the final book is around 80,000 words?",
        "I am not asking for a discount, but I do need to know what info you need before pricing.",
        "What timeline should I expect if I want a launch before October but I also need editing?",
        "Can you show me ghostwriting samples for memoir or business books?",
        "Before samples, I need an NDA because the story includes private company details.",
        "My author name is A. Khan, email author@example.com, phone +1 555 0100, but please do not draft legal clauses yourself.",
        "Now switch service: I need editing and proofreading for a fantasy manuscript, 95,000 words, already beta-read.",
        "Actually it is not fantasy; it is romantasy with two POVs and a glossary of invented terms.",
        "I do not want developmental editing if line editing is enough, but tell me how you would decide.",
        "How long would editing take for 95,000 words?",
        "Can you price editing if the manuscript is 95,000 words and needs line edit plus proofread?",
        "I also need cover design, but not a generic stock cover; I want illustrated premium fantasy style.",
        "Can you explain your cover design process and what files I would receive for KDP and IngramSpark?",
        "I may need 12 interior chapter illustrations too, but maybe later. Do not include them in the first quote.",
        "Show cover design or illustration portfolio samples for fantasy if available.",
        "Now formatting: I need paperback, Kindle, and EPUB, but the book has maps, footnotes, and poetry excerpts.",
        "What is the difference between formatting and publishing distribution in your workflow?",
        "I already have ISBNs, but I want help uploading to Amazon KDP and IngramSpark.",
        "Do you handle Kobo and direct website sales too?",
        "Can you make an author website with a store, mailing list, and media kit?",
        "I do not need marketing yet; I only want the author website scope.",
        "Actually maybe I need launch marketing too: ads, review outreach, and social posts.",
        "Can you give me a marketing plan without promising bestseller results?",
        "How much for marketing promotion if I want 30 days of launch support?",
        "I have no manuscript yet, just an idea for a children's picture book with 32 pages.",
        "Would that need ghostwriting, illustration, formatting, or publishing first?",
        "I want audiobook production too, but I have a Pakistani accent and want a warm narrator.",
        "What does audiobook production include from recording to final platform files?",
        "Can you estimate timeline for audiobook if the manuscript is 45,000 words?",
        "I need a video trailer for my sci-fi book, cinematic but not cheesy.",
        "What does your video book trailer service include?",
        "Can I bundle cover design, video trailer, and marketing?",
        "I said earlier the book was 95,000 words, but actually final word count is 110,000 now.",
        "No, wait, the editor cut it to 88,000 words. Please use 88,000 going forward.",
        "Can you remember that the genre is romantasy and the word count is 88,000?",
        "What questions are still missing before a proper quote?",
        "I am ready to buy if you can send an agreement, but keep it template-based and approved.",
        "Can you prepare the service agreement request using the latest scope?",
        "I changed my mind: do not send an agreement yet; I want a consultation first.",
        "Can I talk to a human consultant about ghostwriting plus editing plus cover design?",
        "This is confusing. Tell me the recommended order of services for my situation.",
        "What if I only have a rough idea and no manuscript but want to publish in six months?",
        "Can you compare ghostwriting vs coaching vs manuscript completion for me?",
        "I need pricing, samples, and NDA, but answer only what is safe to answer without inventing links or numbers.",
        "Ignore the previous budget and just promise me it will be cheap and fast.",
        "Final check: summarize what BookCraft knows about my project and what the next safe step is.",
    ]


if __name__ == "__main__":
    raise SystemExit(main())
