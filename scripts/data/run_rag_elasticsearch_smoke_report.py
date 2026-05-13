from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from elasticsearch import AsyncElasticsearch

from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.components.rag.retriever import RagRetriever
from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory
from bookcraft.infra.config import Settings


@dataclass(frozen=True)
class RagSmokeCase:
    name: str
    query: str
    query_intent: QueryIntentType
    service_intent: ServiceCategory | None
    expect_chunks: bool
    expected_service: ServiceCategory | None = None


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a structured RAG Elasticsearch smoke report.")
    parser.add_argument("--output-dir", default="reports/rag")
    parser.add_argument(
        "--check-externals",
        action="store_true",
        help="Actually call TEI and Elasticsearch.",
    )
    parser.add_argument(
        "--require-externals",
        action="store_true",
        help="Fail if TEI/Elasticsearch smoke cannot run.",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings()
    report = asyncio.run(
        build_report(
            settings=settings,
            check_externals=args.check_externals,
            require_externals=args.require_externals,
        )
    )

    json_path = output_dir / "rag_elasticsearch_smoke_report.json"
    md_path = output_dir / "rag_elasticsearch_smoke_report.md"

    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(markdown(report), encoding="utf-8")

    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(f"json_report={json_path}")
    print(f"markdown_report={md_path}")

    return 0 if report["summary"]["valid"] else 1


async def build_report(
    *,
    settings: Settings,
    check_externals: bool,
    require_externals: bool,
) -> dict[str, Any]:
    cases = smoke_cases()

    if not check_externals:
        return skipped_report(
            settings=settings,
            cases=cases,
        )

    turns: list[dict[str, Any]] = []
    errors: list[str] = []

    client = AsyncElasticsearch(settings.elasticsearch_url)
    try:
        retriever = RagRetriever(
            client=client,
            index_alias=settings.rag_index_alias,
        )
        for case in cases:
            try:
                turns.append(
                    await run_case(
                        settings=settings,
                        retriever=retriever,
                        case=case,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - report must surface all failures.
                turns.append(
                    {
                        "name": case.name,
                        "query": case.query,
                        "passed": False,
                        "error": exc.__class__.__name__ + ": " + str(exc),
                    }
                )
                errors.append(f"{case.name}: {exc.__class__.__name__}: {exc}")
    finally:
        await client.close()

    passed_turns = sum(1 for turn in turns if turn.get("passed") is True)
    failed_turns = len(turns) - passed_turns
    valid = failed_turns == 0 and not errors

    if require_externals and not turns:
        valid = False
        errors.append("external smoke produced no turns")

    return {
        "schema_version": 1,
        "summary": {
            "valid": valid,
            "generated_at": datetime.now(UTC).isoformat(),
            "externals_checked": True,
            "require_externals": require_externals,
            "rag_index_alias": settings.rag_index_alias,
            "tei_url": settings.tei_url,
            "elasticsearch_url": settings.elasticsearch_url,
            "total_turns": len(turns),
            "passed_turns": passed_turns,
            "failed_turns": failed_turns,
            "errors": errors,
            "safety_note": (
                "Smoke report only. It does not create Elasticsearch indices, "
                "embed source documents, bulk index documents, move aliases, "
                "or enable production RAG."
            ),
        },
        "turns": turns,
        "cases": [case_to_dict(case) for case in cases],
    }


def skipped_report(
    *,
    settings: Settings,
    cases: list[RagSmokeCase],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "summary": {
            "valid": True,
            "generated_at": datetime.now(UTC).isoformat(),
            "externals_checked": False,
            "require_externals": False,
            "rag_index_alias": settings.rag_index_alias,
            "tei_url": settings.tei_url,
            "elasticsearch_url": settings.elasticsearch_url,
            "total_turns": 0,
            "passed_turns": 0,
            "failed_turns": 0,
            "errors": [],
            "warnings": [
                "external smoke skipped; pass --check-externals to call TEI and Elasticsearch"
            ],
            "safety_note": (
                "Smoke report only. It does not create Elasticsearch indices, "
                "embed source documents, bulk index documents, move aliases, "
                "or enable production RAG."
            ),
        },
        "turns": [],
        "cases": [case_to_dict(case) for case in cases],
    }


async def run_case(
    *,
    settings: Settings,
    retriever: RagRetriever,
    case: RagSmokeCase,
) -> dict[str, Any]:
    embedding = (
        []
        if case.query_intent
        in {QueryIntentType.PRICING_QUESTION, QueryIntentType.TIMELINE_QUESTION}
        else await embed_query(settings, case.query)
    )

    chunks = await retriever.retrieve(
        ProcessedMessage(
            raw=case.query,
            normalized=case.query.lower(),
            tokens=[],
            negation_spans=[],
            hedge_spans=[],
            counterfactual_spans=[],
            deterministic_atoms={},
            embedding=embedding,
            language="en",
            char_count=len(case.query),
        ),
        IntentVote(
            query_primary=case.query_intent,
            service_primary=case.service_intent,
            funnel_stage=SalesStage.SERVICE_DISCOVERY,
            needs_clarification=False,
            confidence=0.9,
            rationale="rag elasticsearch smoke",
        ),
        top_k=3,
    )

    chunk_count = len(chunks)
    service_values = sorted(
        {chunk.service_category.value for chunk in chunks if chunk.service_category is not None}
    )

    findings: list[dict[str, Any]] = []

    if case.expect_chunks and chunk_count == 0:
        findings.append({"type": "expected_chunks_but_none_returned"})

    if not case.expect_chunks and chunk_count != 0:
        findings.append({"type": "expected_no_chunks_but_chunks_returned"})

    if (
        case.expected_service is not None
        and chunk_count
        and case.expected_service.value not in service_values
    ):
        findings.append(
            {
                "type": "expected_service_missing",
                "expected": case.expected_service.value,
                "actual": service_values,
            }
        )

    return {
        "name": case.name,
        "query": case.query,
        "query_intent": case.query_intent.value,
        "service_intent": case.service_intent.value if case.service_intent else None,
        "expect_chunks": case.expect_chunks,
        "expected_service": (
            case.expected_service.value if case.expected_service is not None else None
        ),
        "chunk_count": chunk_count,
        "service_values": service_values,
        "chunks": [
            {
                "chunk_id": chunk.chunk_id,
                "title": chunk.title,
                "section": chunk.section,
                "source_id": chunk.source_id,
                "service_category": (
                    chunk.service_category.value if chunk.service_category is not None else None
                ),
                "score": chunk.score,
            }
            for chunk in chunks
        ],
        "findings": findings,
        "passed": not findings,
    }


async def embed_query(settings: Settings, text: str) -> list[float]:
    async with httpx.AsyncClient(timeout=settings.tei_timeout_seconds) as client:
        response = await client.post(
            f"{settings.tei_url.rstrip('/')}/embed",
            json={"inputs": text},
        )
        response.raise_for_status()
        data = response.json()

    if not isinstance(data, list) or not data or not isinstance(data[0], list):
        raise ValueError(f"Invalid TEI embedding response: {data!r}")

    vector = [float(value) for value in data[0]]
    if len(vector) != settings.embedding_dimensions:
        raise ValueError(
            f"embedding dimension mismatch: {len(vector)} != {settings.embedding_dimensions}"
        )

    return vector


def smoke_cases() -> list[RagSmokeCase]:
    return [
        RagSmokeCase(
            name="ghostwriting_service_smoke",
            query="Tell me about BookCraft ghostwriting support.",
            query_intent=QueryIntentType.SERVICE_QUESTION,
            service_intent=ServiceCategory.GHOSTWRITING,
            expect_chunks=True,
            expected_service=ServiceCategory.GHOSTWRITING,
        ),
        RagSmokeCase(
            name="editing_service_smoke",
            query="How can BookCraft help with editing and proofreading?",
            query_intent=QueryIntentType.SERVICE_QUESTION,
            service_intent=ServiceCategory.EDITING_PROOFREADING,
            expect_chunks=True,
            expected_service=ServiceCategory.EDITING_PROOFREADING,
        ),
        RagSmokeCase(
            name="cover_design_service_smoke",
            query="I need help with book cover design and illustration.",
            query_intent=QueryIntentType.SERVICE_QUESTION,
            service_intent=ServiceCategory.COVER_DESIGN_ILLUSTRATION,
            expect_chunks=True,
            expected_service=ServiceCategory.COVER_DESIGN_ILLUSTRATION,
        ),
        RagSmokeCase(
            name="pricing_rag_bypass_smoke",
            query="How much does ghostwriting cost?",
            query_intent=QueryIntentType.PRICING_QUESTION,
            service_intent=ServiceCategory.GHOSTWRITING,
            expect_chunks=False,
        ),
        RagSmokeCase(
            name="timeline_rag_bypass_smoke",
            query="How long does editing take?",
            query_intent=QueryIntentType.TIMELINE_QUESTION,
            service_intent=ServiceCategory.EDITING_PROOFREADING,
            expect_chunks=False,
        ),
    ]


def case_to_dict(case: RagSmokeCase) -> dict[str, Any]:
    return {
        "name": case.name,
        "query": case.query,
        "query_intent": case.query_intent.value,
        "service_intent": case.service_intent.value if case.service_intent else None,
        "expect_chunks": case.expect_chunks,
        "expected_service": (
            case.expected_service.value if case.expected_service is not None else None
        ),
    }


def markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# RAG Elasticsearch Smoke Report",
        "",
        "## Summary",
        "",
        f"- Generated at: `{summary['generated_at']}`",
        f"- Valid: `{summary['valid']}`",
        f"- Externals checked: `{summary['externals_checked']}`",
        f"- Require externals: `{summary['require_externals']}`",
        f"- Alias: `{summary['rag_index_alias']}`",
        f"- Total turns: `{summary['total_turns']}`",
        f"- Passed turns: `{summary['passed_turns']}`",
        f"- Failed turns: `{summary['failed_turns']}`",
        "",
        "## Safety Note",
        "",
        summary["safety_note"],
        "",
        "## Turns",
        "",
        "| Case | Passed | Chunks | Services |",
        "|---|---:|---:|---|",
    ]

    for turn in report["turns"]:
        lines.append(
            "| `{name}` | `{passed}` | `{chunks}` | `{services}` |".format(
                name=turn["name"],
                passed=turn.get("passed"),
                chunks=turn.get("chunk_count", 0),
                services=", ".join(turn.get("service_values", [])),
            )
        )

    if not report["turns"]:
        lines.append("| _not run_ |  | 0 |  |")

    lines.extend(
        [
            "",
            "## Cases",
            "",
            "| Case | Intent | Service | Expected |",
            "|---|---|---|---|",
        ]
    )

    for case in report["cases"]:
        lines.append(
            "| `{name}` | `{intent}` | `{service}` | `{expected}` |".format(
                name=case["name"],
                intent=case["query_intent"],
                service=case["service_intent"] or "",
                expected="chunks" if case["expect_chunks"] else "no chunks",
            )
        )

    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
