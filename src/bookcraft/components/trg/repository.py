from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

from bookcraft.infra.cache import CacheClient, CacheKeyBuilder

from .schemas import TemporalRelationGraph


class GraphRepository:
    async def load(self, thread_id: UUID) -> TemporalRelationGraph | None:
        raise NotImplementedError

    async def save(self, graph: TemporalRelationGraph) -> None:
        raise NotImplementedError


class InMemoryGraphRepository(GraphRepository):
    def __init__(self) -> None:
        self.graphs: dict[UUID, TemporalRelationGraph] = {}

    async def load(self, thread_id: UUID) -> TemporalRelationGraph | None:
        graph = self.graphs.get(thread_id)
        return graph.model_copy(deep=True) if graph is not None else None

    async def save(self, graph: TemporalRelationGraph) -> None:
        self.graphs[graph.thread_id] = graph.model_copy(deep=True)


@dataclass(slots=True)
class RedisHotGraphStore(GraphRepository):
    client: CacheClient
    keys: CacheKeyBuilder
    ttl_seconds: int

    async def load(self, thread_id: UUID) -> TemporalRelationGraph | None:
        cached = await self.client.get(self.keys.thread_graph(str(thread_id)))
        if cached is None:
            return None
        return TemporalRelationGraph.model_validate(json.loads(cached))

    async def save(self, graph: TemporalRelationGraph) -> None:
        await self.client.set(
            self.keys.thread_graph(str(graph.thread_id)),
            graph.model_dump_json(),
            ex=self.ttl_seconds,
        )
