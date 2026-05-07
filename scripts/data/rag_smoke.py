import asyncio

from elasticsearch import AsyncElasticsearch

from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.components.rag.retriever import RagRetriever
from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory
from bookcraft.infra.config import get_settings


async def async_main() -> int:
    settings = get_settings()
    client = AsyncElasticsearch(settings.elasticsearch_url)
    try:
        retriever = RagRetriever(client=client, index_alias=settings.rag_index_alias)
        chunks = await retriever.retrieve(
            ProcessedMessage(
                raw="Tell me about ghostwriting",
                normalized="Tell me about ghostwriting",
                tokens=[],
                negation_spans=[],
                hedge_spans=[],
                counterfactual_spans=[],
                deterministic_atoms={"services": ["ghostwriting"]},
                embedding=[0.0] * settings.embedding_dimensions,
                language="en",
                char_count=26,
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
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())

