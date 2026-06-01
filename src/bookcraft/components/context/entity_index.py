"""ES-based conversation entity memory.

Stores extracted facts as entity-centric documents — one document per
(thread_id, entity_path), updated in place when a fact changes. Supports:
  - Exact-match lookup by entity_path
  - Hybrid BM25 + cosine-vector retrieval by semantic content

This is distinct from the RAG index (which stores BookCraft knowledge base
chunks). The entity index stores facts about THIS conversation and visitor.

Index name: conversation_entities
Document id: f"{thread_id}::{entity_path}" for structured facts
             f"{thread_id}::free::{key}" for free-text rich metadata
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import structlog
from elasticsearch import AsyncElasticsearch
from prometheus_client import Counter, Histogram

from bookcraft.components.preprocessor.embedding import EmbeddingClient

logger = structlog.get_logger(__name__)

ENTITY_UPSERTS = Counter("entity_index_upserts_total", "Entity index upserts.", ["outcome"])
ENTITY_RETRIEVALS = Counter("entity_index_retrievals_total", "Entity index retrievals.", ["outcome"])
ENTITY_RETRIEVAL_SECONDS = Histogram("entity_retrieval_seconds", "Entity retrieval latency.")

INDEX_NAME = "conversation_entities"

# Mapping template sent on first upsert if the index does not yet exist.
_INDEX_MAPPING = {
    "mappings": {
        "properties": {
            "thread_id": {"type": "keyword"},
            "entity_path": {"type": "keyword"},
            "entity_type": {"type": "keyword"},
            "entity_value_text": {"type": "text"},
            "confidence": {"type": "float"},
            "is_free_text": {"type": "boolean"},
            "source_extraction": {"type": "boolean"},
            "source_turn_index": {"type": "integer"},
            "content_vector": {
                "type": "dense_vector",
                "dims": 384,
                "index": True,
                "similarity": "cosine",
            },
            "created_at": {"type": "date"},
            "updated_at": {"type": "date"},
        }
    }
}


class EntityDocument:
    """Plain-object representation of an entity memory document."""

    __slots__ = (
        "thread_id",
        "entity_path",
        "entity_type",
        "entity_value_text",
        "confidence",
        "is_free_text",
        "source_extraction",
        "source_turn_index",
        "content_vector",
    )

    def __init__(
        self,
        *,
        thread_id: str,
        entity_path: str,
        entity_type: str,
        entity_value_text: str,
        confidence: float,
        is_free_text: bool,
        source_extraction: bool,
        source_turn_index: int,
        content_vector: list[float],
    ) -> None:
        self.thread_id = thread_id
        self.entity_path = entity_path
        self.entity_type = entity_type
        self.entity_value_text = entity_value_text
        self.confidence = confidence
        self.is_free_text = is_free_text
        self.source_extraction = source_extraction
        self.source_turn_index = source_turn_index
        self.content_vector = content_vector

    def to_es_doc(self) -> dict[str, Any]:
        from datetime import UTC, datetime

        now = datetime.now(UTC).isoformat()
        return {
            "thread_id": self.thread_id,
            "entity_path": self.entity_path,
            "entity_type": self.entity_type,
            "entity_value_text": self.entity_value_text,
            "confidence": self.confidence,
            "is_free_text": self.is_free_text,
            "source_extraction": self.source_extraction,
            "source_turn_index": self.source_turn_index,
            "content_vector": self.content_vector,
            "updated_at": now,
        }

    @property
    def doc_id(self) -> str:
        prefix = f"{self.thread_id}::{self.entity_path}"
        return hashlib.sha1(prefix.encode()).hexdigest()  # noqa: S324


@dataclass(slots=True)
class ConversationEntityIndex:
    """Upserts and retrieves entity memory documents from ElasticSearch."""

    client: AsyncElasticsearch
    embedding_client: EmbeddingClient
    index_name: str = INDEX_NAME
    _index_ensured: bool = field(default=False, init=False)

    async def _ensure_index(self) -> None:
        if self._index_ensured:
            return
        try:
            exists = await self.client.indices.exists(index=self.index_name)
            if not exists:
                await self.client.indices.create(
                    index=self.index_name,
                    body=_INDEX_MAPPING,
                )
            self._index_ensured = True
        except Exception as exc:
            logger.warning(
                "entity_index_ensure_failed",
                exception_class=exc.__class__.__name__,
            )

    async def upsert_structured_fact(
        self,
        thread_id: UUID,
        entity_path: str,
        entity_value: str,
        confidence: float,
        source_extraction: bool,
        turn_index: int,
    ) -> None:
        """Upsert a structured fact (maps to a FieldMeta path)."""
        await self._upsert(
            thread_id=thread_id,
            entity_path=entity_path,
            entity_type=_path_to_type(entity_path),
            entity_value_text=entity_value,
            confidence=confidence,
            is_free_text=False,
            source_extraction=source_extraction,
            turn_index=turn_index,
        )

    async def upsert_free_text_fact(
        self,
        thread_id: UUID,
        key: str,
        value: str,
        confidence: float,
        turn_index: int,
    ) -> None:
        """Upsert a rich free-text fact (cover preferences, section structure, etc.)."""
        entity_path = f"book_specs.{key}"
        await self._upsert(
            thread_id=thread_id,
            entity_path=entity_path,
            entity_type=key,
            entity_value_text=value,
            confidence=confidence,
            is_free_text=True,
            source_extraction=True,
            turn_index=turn_index,
        )

    async def retrieve_relevant(
        self,
        thread_id: UUID,
        query_text: str,
        top_k: int = 6,
    ) -> list[dict[str, Any]]:
        """Return top-k entity documents most relevant to query_text for this thread."""
        if not query_text.strip():
            return []

        await self._ensure_index()
        try:
            with ENTITY_RETRIEVAL_SECONDS.time():
                embedding = await self.embedding_client.embed(query_text, language="en")
                bm25_hits = await self._bm25(thread_id, query_text, top_k)
                vector_hits = await self._vector(thread_id, embedding, top_k)
                merged = _reciprocal_rank_fusion([bm25_hits, vector_hits], top_k=top_k)
                result = [
                    bm25_hits.get(hit_id) or vector_hits.get(hit_id)
                    for hit_id, _ in merged
                    if (bm25_hits.get(hit_id) or vector_hits.get(hit_id)) is not None
                ]
            ENTITY_RETRIEVALS.labels(outcome="success").inc()
            return result
        except Exception as exc:
            logger.warning(
                "entity_retrieval_failed",
                thread_id=str(thread_id),
                exception_class=exc.__class__.__name__,
            )
            ENTITY_RETRIEVALS.labels(outcome="failed").inc()
            return []

    async def _upsert(
        self,
        *,
        thread_id: UUID,
        entity_path: str,
        entity_type: str,
        entity_value_text: str,
        confidence: float,
        is_free_text: bool,
        source_extraction: bool,
        turn_index: int,
    ) -> None:
        await self._ensure_index()
        try:
            embedding = await self.embedding_client.embed(entity_value_text, language="en")
            doc = EntityDocument(
                thread_id=str(thread_id),
                entity_path=entity_path,
                entity_type=entity_type,
                entity_value_text=entity_value_text,
                confidence=confidence,
                is_free_text=is_free_text,
                source_extraction=source_extraction,
                source_turn_index=turn_index,
                content_vector=embedding,
            )
            await self.client.index(
                index=self.index_name,
                id=doc.doc_id,
                document=doc.to_es_doc(),
            )
            ENTITY_UPSERTS.labels(outcome="success").inc()
        except Exception as exc:
            logger.warning(
                "entity_upsert_failed",
                thread_id=str(thread_id),
                entity_path=entity_path,
                exception_class=exc.__class__.__name__,
            )
            ENTITY_UPSERTS.labels(outcome="failed").inc()

    async def _bm25(
        self,
        thread_id: UUID,
        query_text: str,
        top_k: int,
    ) -> dict[str, dict[str, Any]]:
        response = await self.client.search(
            index=self.index_name,
            size=top_k,
            query={
                "bool": {
                    "must": [{"match": {"entity_value_text": query_text}}],
                    "filter": [{"term": {"thread_id": str(thread_id)}}],
                }
            },
        )
        return _hits_by_id(dict(response))

    async def _vector(
        self,
        thread_id: UUID,
        embedding: list[float],
        top_k: int,
    ) -> dict[str, dict[str, Any]]:
        if not embedding or not any(abs(v) > 0 for v in embedding):
            return {}
        response = await self.client.search(
            index=self.index_name,
            size=top_k,
            query={
                "script_score": {
                    "query": {"term": {"thread_id": str(thread_id)}},
                    "script": {
                        "source": "cosineSimilarity(params.query_vector, 'content_vector') + 1.0",
                        "params": {"query_vector": embedding},
                    },
                }
            },
        )
        return _hits_by_id(dict(response))


def _hits_by_id(response: dict[str, Any]) -> dict[str, dict[str, Any]]:
    hits = response.get("hits", {}).get("hits", [])
    return {str(hit["_id"]): hit.get("_source", {}) for hit in hits if "_id" in hit}


def _reciprocal_rank_fusion(
    result_sets: list[dict[str, dict[str, Any]]],
    *,
    top_k: int,
    k: int = 60,
) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for result_set in result_sets:
        for rank, hit_id in enumerate(result_set, start=1):
            scores[hit_id] = scores.get(hit_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]


def _path_to_type(path: str) -> str:
    """Derive a human-readable entity type from a state path."""
    return path.split(".")[-1].replace("_", " ")
