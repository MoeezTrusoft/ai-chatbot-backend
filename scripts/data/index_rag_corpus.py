import asyncio
from pathlib import Path

from elasticsearch import AsyncElasticsearch

from bookcraft.components.rag.index import RagIndexManager
from bookcraft.components.rag.pipeline import load_chunks
from bookcraft.components.rag.verifier import RagVerifier
from bookcraft.infra.config import get_settings
from bookcraft.infra.embeddings import TeiEmbeddingClient


async def async_main() -> int:
    settings = get_settings()
    RagVerifier(strict=True).verify_build_dir(Path(settings.rag_build_dir))
    chunks = load_chunks(Path(settings.rag_build_dir))
    await _ensure_embeddings(chunks, settings)
    client = AsyncElasticsearch(settings.elasticsearch_url)
    try:
        manager = RagIndexManager(
            client=client,
            index_name=settings.rag_index_version,
            alias_name=settings.rag_index_alias,
            dimensions=settings.embedding_dimensions,
        )
        await manager.create_index()
        await manager.index_chunks(chunks)
        await manager.promote_alias()
    finally:
        await client.close()
    print(f"indexed {len(chunks)} chunks into {settings.rag_index_version}")
    return 0


def main() -> int:
    return asyncio.run(async_main())


async def _ensure_embeddings(chunks, settings):
    embedder = TeiEmbeddingClient(
        base_url=settings.tei_url,
        timeout_seconds=settings.tei_timeout_seconds,
    )

    for chunk in chunks:
        if len(chunk.content_vector) == settings.embedding_dimensions:
            continue

        chunk.content_vector = await embedder.embed(chunk.content)

        if len(chunk.content_vector) != settings.embedding_dimensions:
            raise ValueError(
                f"Chunk {chunk.chunk_id} embedding dimension mismatch: "
                f"{len(chunk.content_vector)} != {settings.embedding_dimensions}"
            )


if __name__ == "__main__":
    raise SystemExit(main())
