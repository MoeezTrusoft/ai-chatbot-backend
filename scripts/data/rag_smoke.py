import asyncio

import httpx
from elasticsearch import AsyncElasticsearch

from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.components.rag.retriever import RagRetriever
from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory
from bookcraft.infra.config import get_settings


async def embed_query(tei_url: str, text: str, timeout_seconds: float) -> list[float]:
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.post(
            f"{tei_url.rstrip('/')}/embed",
            json={"inputs": text},
        )
        response.raise_for_status()
        data = response.json()

    if not isinstance(data, list) or not data or not isinstance(data[0], list):
        raise ValueError(f"Invalid TEI embedding response: {data!r}")

    return [float(value) for value in data[0]]


async def async_main() -> int:
    settings = get_settings()
    query = "Tell me about ghostwriting"
    embedding = await embed_query(
        settings.tei_url,
        query,
        settings.tei_timeout_seconds,
    )

    if len(embedding) != settings.embedding_dimensions:
        print(f"embedding dimension mismatch: {len(embedding)} != {settings.embedding_dimensions}")
        return 1

    client = AsyncElasticsearch(settings.elasticsearch_url)
    try:
        retriever = RagRetriever(client=client, index_alias=settings.rag_index_alias)
        chunks = await retriever.retrieve(
            ProcessedMessage(
                raw=query,
                normalized=query.lower(),
                tokens=[],
                negation_spans=[],
                hedge_spans=[],
                counterfactual_spans=[],
                deterministic_atoms={"services": ["ghostwriting"]},
                embedding=embedding,
                language="en",
                char_count=len(query),
            ),
            IntentVote(
                query_primary=QueryIntentType.SERVICE_QUESTION,
                service_primary=ServiceCategory.GHOSTWRITING,
                funnel_stage=SalesStage.SERVICE_DISCOVERY,
                needs_clarification=False,
                confidence=0.9,
                rationale="smoke",
            ),
            top_k=3,
        )
    finally:
        await client.close()

    if not chunks:
        print("rag smoke returned no chunks")
        return 1

    print(f"rag smoke returned {len(chunks)} chunks")
    for chunk in chunks:
        print(f"- {chunk.title} / {chunk.section} / {chunk.chunk_id}")
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
