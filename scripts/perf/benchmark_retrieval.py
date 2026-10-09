"""Compare exact and ANN retrieval on a labeled JSON corpus without changing the index.

Usage: uv run python scripts/perf/benchmark_retrieval.py cases.json --index bookcraft_rag
Each case: {"query": "...", "embedding": [...], "service": "editing_proofreading",
            "relevant_ids": ["chunk-id", ...]}
Vectors must match the indexed model. Run against representative staging data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

from elasticsearch import AsyncElasticsearch

from bookcraft.components.rag.retriever import RagRetriever, reciprocal_rank_fusion
from bookcraft.components.rag.schemas import RagRetrievalRequest
from bookcraft.domain.enums import QueryIntentType, ServiceCategory
from bookcraft.infra.config import Settings


async def benchmark(path: Path, index: str, repeats: int, candidates: int) -> None:
    settings = Settings()
    cases = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
    if not cases or any(
        not case.get("relevant_ids") or not case.get("embedding") for case in cases
    ):
        raise ValueError("Every case requires a nonempty embedding and labeled relevant_ids")
    auth = (
        (settings.elasticsearch_user, settings.elasticsearch_password or "")
        if settings.elasticsearch_user
        else None
    )
    async with AsyncElasticsearch(settings.elasticsearch_url, basic_auth=auth) as client:
        results = {}
        # Alternate modes each repetition to reduce cache/order bias.
        timings: dict[str, list[float]] = {"exact": [], "knn": []}
        recalls: dict[str, list[float]] = {"exact": [], "knn": []}
        for repeat in range(repeats + 1):
            for case in cases:
                for mode in ("exact", "knn") if repeat % 2 == 0 else ("knn", "exact"):
                    retriever = RagRetriever(client, index, mode, candidates)
                    request = RagRetrievalRequest(
                        normalized_query=case["query"],
                        query_embedding=case["embedding"],
                        query_intent=QueryIntentType.SERVICE_QUESTION,
                        service_intent=ServiceCategory(case["service"])
                        if case.get("service")
                        else None,
                        top_k=8,
                    )
                    start = time.perf_counter()
                    lexical, vector = await asyncio.gather(
                        retriever._bm25(request), retriever._vector(request)
                    )
                    elapsed = (time.perf_counter() - start) * 1000
                    hits = reciprocal_rank_fusion([lexical, vector], top_k=8)
                    found = {
                        str((lexical.get(key) or vector[key])["_source"]["chunk_id"])
                        for key, _ in hits
                    }
                    relevant = set(case["relevant_ids"])
                    if repeat:  # First pass warms clients/index caches.
                        timings[mode].append(elapsed)
                        recalls[mode].append(len(found & relevant) / len(relevant))
        for mode in timings:
            ordered = sorted(timings[mode])
            results[mode] = {
                "queries": len(ordered),
                "p50_ms": statistics.median(ordered),
                "p95_ms": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))],
                "mean_recall_at_8": statistics.mean(recalls[mode]),
            }
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", type=Path)
    parser.add_argument("--index", required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--candidates", type=int, default=100)
    args = parser.parse_args()
    if args.repeats < 1 or not 8 <= args.candidates <= 10000:
        parser.error("repeats must be positive; candidates must be between 8 and 10000")
    asyncio.run(benchmark(args.cases, args.index, args.repeats, args.candidates))
