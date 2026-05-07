from dataclasses import dataclass
from typing import Any

from elasticsearch import AsyncElasticsearch, helpers

from bookcraft.components.rag.schemas import RagChunk


def rag_index_mapping(dimensions: int = 384) -> dict[str, Any]:
    return {
        "mappings": {
            "properties": {
                "chunk_id": {"type": "keyword"},
                "source_id": {"type": "keyword"},
                "source_type": {"type": "keyword"},
                "title": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                "service_category": {"type": "keyword"},
                "subservice": {"type": "keyword"},
                "audience": {"type": "keyword"},
                "funnel_stage": {"type": "keyword"},
                "content": {"type": "text"},
                "content_vector": {
                    "type": "dense_vector",
                    "dims": dimensions,
                    "index": True,
                    "similarity": "cosine",
                },
                "tags": {"type": "keyword"},
                "section": {"type": "keyword"},
                "source_filename": {"type": "keyword"},
                "checksum": {"type": "keyword"},
                "allowed_for_response": {"type": "boolean"},
                "created_at": {"type": "date"},
                "updated_at": {"type": "date"},
            }
        }
    }


@dataclass(slots=True)
class RagIndexManager:
    client: AsyncElasticsearch
    index_name: str
    alias_name: str
    dimensions: int = 384

    async def create_index(self) -> None:
        if await self.client.indices.exists(index=self.index_name):
            await self.client.indices.delete(index=self.index_name)
        await self.client.indices.create(
            index=self.index_name,
            mappings=rag_index_mapping(self.dimensions)["mappings"],
        )

    async def index_chunks(self, chunks: list[RagChunk]) -> None:
        actions = [
            {
                "_index": self.index_name,
                "_id": chunk.chunk_id,
                "_source": _chunk_source(chunk),
            }
            for chunk in chunks
        ]
        if actions:
            await helpers.async_bulk(self.client, actions)
        await self.client.indices.refresh(index=self.index_name)

    async def promote_alias(self) -> None:
        actions: list[dict[str, object]] = []
        if await self.client.indices.exists_alias(name=self.alias_name):
            existing = await self.client.indices.get_alias(name=self.alias_name)
            actions.extend(
                {"remove": {"index": index_name, "alias": self.alias_name}}
                for index_name in existing.keys()
            )
        actions.append({"add": {"index": self.index_name, "alias": self.alias_name}})
        await self.client.indices.update_aliases(actions=actions)


def _chunk_source(chunk: RagChunk) -> dict[str, object]:
    metadata = chunk.metadata
    return {
        "chunk_id": chunk.chunk_id,
        "source_id": metadata.source_id,
        "source_type": metadata.source_type,
        "title": metadata.title,
        "service_category": metadata.service_category.value if metadata.service_category else None,
        "subservice": metadata.subservice,
        "audience": metadata.audience,
        "funnel_stage": metadata.funnel_stage.value if metadata.funnel_stage else None,
        "content": chunk.content,
        "content_vector": chunk.content_vector,
        "tags": metadata.tags,
        "section": metadata.section,
        "source_filename": metadata.source_filename,
        "checksum": chunk.checksum,
        "allowed_for_response": chunk.allowed_for_response,
        "created_at": chunk.created_at.isoformat(),
        "updated_at": chunk.updated_at.isoformat(),
    }
