from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from elasticsearch import AsyncElasticsearch
from prometheus_client import Counter, Histogram

from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.components.rag.schemas import RagRetrievalRequest, RetrievedChunk
from bookcraft.domain.enums import QueryIntentType, ServiceCategory

RAG_RETRIEVAL_SECONDS = Histogram("rag_retrieval_seconds", "RAG retrieval latency.")
RAG_QUERY_LATENCY = Histogram("rag_query_latency_seconds", "RAG query latency.")
RAG_QUERIES_TOTAL = Counter("rag_queries_total", "RAG queries by result.", ["result"])
RAG_CHUNKS_RETURNED = Counter("rag_chunks_returned", "RAG chunks returned.")
RAG_EMPTY_RESULTS = Counter("rag_empty_result_total", "RAG retrieval empty result count.")


@dataclass(slots=True)
class RagRetriever:
    client: AsyncElasticsearch
    index_alias: str

    async def retrieve(
        self,
        processed_message: ProcessedMessage,
        intent: IntentVote,
        top_k: int = 8,
    ) -> list[RetrievedChunk]:
        quote_intents = {QueryIntentType.PRICING_QUESTION, QueryIntentType.TIMELINE_QUESTION}
        if intent.query_primary in quote_intents:
            return []
        request = RagRetrievalRequest(
            normalized_query=processed_message.normalized,
            query_embedding=processed_message.embedding,
            query_intent=intent.query_primary,
            service_intent=intent.service_primary,
            top_k=top_k,
        )
        with RAG_RETRIEVAL_SECONDS.time(), RAG_QUERY_LATENCY.time():
            try:
                bm25 = await self._bm25(request)
                vector = await self._vector(request)
                ranked = reciprocal_rank_fusion([bm25, vector], top_k=top_k)
                maybe_chunks = [
                    _hit_to_chunk(hit_id, score, bm25, vector) for hit_id, score in ranked
                ]
                chunks = [chunk for chunk in maybe_chunks if chunk is not None]
            except Exception:
                RAG_QUERIES_TOTAL.labels(result="failed").inc()
                raise
        if not chunks:
            RAG_EMPTY_RESULTS.inc()
            RAG_QUERIES_TOTAL.labels(result="empty").inc()
        else:
            RAG_QUERIES_TOTAL.labels(result="found").inc()
        RAG_CHUNKS_RETURNED.inc(len(chunks))
        return chunks

    async def _bm25(self, request: RagRetrievalRequest) -> dict[str, dict[str, Any]]:
        filters = _filters(request.service_intent)
        response = await self.client.search(
            index=self.index_alias,
            size=request.top_k,
            query={
                "bool": {
                    "must": [{"match": {"content": request.normalized_query}}],
                    "filter": filters,
                }
            },
        )
        return _hits_by_id(dict(response))

    async def _vector(self, request: RagRetrievalRequest) -> dict[str, dict[str, Any]]:
        filters = _filters(request.service_intent)
        response = await self.client.search(
            index=self.index_alias,
            size=request.top_k,
            query={
                "script_score": {
                    "query": {"bool": {"filter": filters}} if filters else {"match_all": {}},
                    "script": {
                        "source": "cosineSimilarity(params.query_vector, 'content_vector') + 1.0",
                        "params": {"query_vector": request.query_embedding},
                    },
                }
            },
        )
        return _hits_by_id(dict(response))


def reciprocal_rank_fusion(
    result_sets: list[dict[str, dict[str, Any]]],
    *,
    top_k: int,
    k: int = 60,
) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for result_set in result_sets:
        for rank, hit_id in enumerate(result_set.keys(), start=1):
            scores[hit_id] = scores.get(hit_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)[:top_k]


def _filters(service: ServiceCategory | None) -> list[dict[str, object]]:
    filters: list[dict[str, object]] = [{"term": {"allowed_for_response": True}}]
    if service is not None:
        filters.append({"term": {"service_category": service.value}})
    return filters


def _hits_by_id(response: dict[str, Any]) -> dict[str, dict[str, Any]]:
    hits = response.get("hits", {}).get("hits", [])
    return {str(hit["_id"]): hit for hit in hits if isinstance(hit, dict) and "_id" in hit}


def _hit_to_chunk(
    hit_id: str,
    score: float,
    bm25: dict[str, dict[str, Any]],
    vector: dict[str, dict[str, Any]],
) -> RetrievedChunk | None:
    hit = bm25.get(hit_id) or vector.get(hit_id)
    if hit is None:
        return None
    source = hit.get("_source", {})
    if not isinstance(source, dict):
        return None
    return RetrievedChunk(
        chunk_id=str(source["chunk_id"]),
        content=str(source["content"]),
        score=score,
        service_category=ServiceCategory(source["service_category"])
        if source.get("service_category")
        else None,
        section=str(source["section"]),
        source_id=str(source["source_id"]),
        title=str(source["title"]),
        checksum=str(source["checksum"]),
        citation=f"{source['title']}::{source['section']}::{source['chunk_id']}",
    )
