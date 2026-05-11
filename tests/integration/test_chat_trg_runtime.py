from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import pytest

from bookcraft.api.chat import ChatTurnRequest
from bookcraft.api.main import build_chat_service, build_trg_engine
from bookcraft.components.extraction.schemas import StateDelta
from bookcraft.components.trg import InMemoryGraphRepository, TemporalRelationGraphEngine
from bookcraft.infra.config import Settings
from bookcraft.tools import MemoryCache


@dataclass(slots=True)
class FailingTrgEngine:
    calls: int = 0

    async def update_after_turn(
        self,
        *,
        thread_id: UUID,
        turn_sequence: int,
        user_text: str,
        assistant_text: str,
        previous_state: object | None = None,
        state_deltas: list[StateDelta] | tuple[StateDelta, ...] = (),
    ) -> object:
        del thread_id, turn_sequence, user_text, assistant_text, previous_state, state_deltas
        self.calls += 1
        raise RuntimeError("trg backend unavailable author@example.com")


@pytest.mark.asyncio
async def test_chat_service_updates_injected_trg_engine() -> None:
    repository = InMemoryGraphRepository()
    engine = TemporalRelationGraphEngine(repository=repository, compact_keep=12)
    service = build_chat_service(
        Settings(app_env="test"),
        trg_engine=engine,
    )

    response = await service.handle_turn(
        ChatTurnRequest(message="I need help with editing. Can you guide me?")
    )

    graph = await repository.load(response.thread_id)
    assert graph is not None
    assert graph.nodes
    serialized_events = str(service.threads[response.thread_id].events)
    assert "trg.updated" in serialized_events


@pytest.mark.asyncio
async def test_chat_service_degrades_when_trg_update_fails() -> None:
    engine = FailingTrgEngine()
    service = build_chat_service(
        Settings(app_env="test"),
        trg_engine=engine,  # type: ignore[arg-type]
    )

    response = await service.handle_turn(
        ChatTurnRequest(message="My email is author@example.com and I need editing.")
    )

    assert engine.calls == 1
    assert response.bubbles
    serialized_events = str(service.threads[response.thread_id].events)
    assert "trg.failed" in serialized_events
    assert "author@example.com" not in serialized_events


def test_build_trg_engine_uses_in_memory_repository_without_cache() -> None:
    engine = build_trg_engine(Settings(app_env="test"), cache_client=None)

    assert isinstance(engine.repository, InMemoryGraphRepository)


def test_build_trg_engine_uses_redis_hot_store_with_cache() -> None:
    engine = build_trg_engine(
        Settings(app_env="dev", redis_hot_ttl_hours=2),
        cache_client=MemoryCache(),
    )

    assert engine.compact_keep == 12
    assert engine.repository.__class__.__name__ == "RedisHotGraphStore"
