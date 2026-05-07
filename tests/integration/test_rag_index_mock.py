import pytest

from bookcraft.components.rag.index import RagIndexManager
from bookcraft.components.rag.schemas import RagChunk, RagChunkMetadata


class FakeIndices:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.alias_actions: list[dict[str, object]] = []

    async def exists(self, index: str) -> bool:
        return False

    async def delete(self, index: str) -> None:
        raise AssertionError(index)

    async def create(self, index: str, **kwargs: object) -> None:
        del kwargs
        self.created.append(index)

    async def exists_alias(self, name: str) -> bool:
        del name
        return False

    async def update_aliases(self, actions: list[dict[str, object]]) -> None:
        self.alias_actions = actions

    async def refresh(self, index: str) -> None:
        del index


class FakeClient:
    def __init__(self) -> None:
        self.indices = FakeIndices()


@pytest.mark.asyncio
async def test_index_manager_creates_and_promotes_alias(monkeypatch) -> None:
    indexed: list[dict[str, object]] = []

    async def fake_bulk(client: object, actions: list[dict[str, object]]) -> None:
        del client
        indexed.extend(actions)

    monkeypatch.setattr("bookcraft.components.rag.index.helpers.async_bulk", fake_bulk)
    client = FakeClient()
    manager = RagIndexManager(
        client=client,  # type: ignore[arg-type]
        index_name="bookcraft_rag_v1",
        alias_name="bookcraft_rag_current",
    )
    chunk = RagChunk(
        chunk_id="chunk",
        content="clean content",
        content_vector=[0.0] * 384,
        metadata=RagChunkMetadata(
            source_id="source",
            title="Title",
            section="Overview",
            source_filename="source.md",
            content_version="test",
        ),
        checksum="checksum",
    )

    await manager.create_index()
    await manager.index_chunks([chunk])
    await manager.promote_alias()

    assert client.indices.created == ["bookcraft_rag_v1"]
    assert indexed[0]["_id"] == "chunk"
    assert client.indices.alias_actions == [
        {"add": {"index": "bookcraft_rag_v1", "alias": "bookcraft_rag_current"}}
    ]

