"""PostgreSQL persistence layer for TRG semantic facts.

Survives Redis TTL expiry. One row per (thread_id, fact_path); rows are
upserted when a fact value changes. On cold-start, facts are reloaded from
here into a fresh in-memory TRG graph.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bookcraft.components.storage.models import TRGFactRecord
from bookcraft.components.trg.schemas import TRGFactNode

logger = structlog.get_logger(__name__)


class TRGFactStore:
    """Reads and writes TRGFactRecord rows to PostgreSQL."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def upsert_facts(
        self,
        thread_id: UUID,
        facts: list[TRGFactNode],
        turn_index: int,
    ) -> None:
        """Persist active facts. Inactive (superseded) facts are marked inactive in DB."""
        if not facts:
            return

        try:
            async with self._session_factory() as session:
                async with session.begin():
                    for fact in facts:
                        fact_value_json = json.dumps(
                            fact.value if isinstance(fact.value, str | int | float | bool)
                            else str(fact.value)
                        )
                        # Try to find existing row
                        result = await session.execute(
                            select(TRGFactRecord).where(
                                TRGFactRecord.thread_id == thread_id,
                                TRGFactRecord.fact_path == fact.fact_path,
                            )
                        )
                        existing = result.scalar_one_or_none()

                        if existing is not None:
                            existing.fact_value = fact_value_json
                            existing.confidence = fact.confidence
                            existing.active = fact.active
                            existing.source_extraction = fact.source_extraction
                            existing.turn_index = turn_index
                            existing.raw_excerpt = fact.raw_excerpt
                            existing.updated_at = datetime.now(UTC).replace(tzinfo=None)
                        else:
                            session.add(
                                TRGFactRecord(
                                    thread_id=thread_id,
                                    fact_path=fact.fact_path,
                                    fact_value=fact_value_json,
                                    confidence=fact.confidence,
                                    source_extraction=fact.source_extraction,
                                    turn_index=turn_index,
                                    raw_excerpt=fact.raw_excerpt,
                                    active=fact.active,
                                )
                            )
        except Exception as exc:
            logger.warning(
                "trg_fact_store_upsert_failed",
                thread_id=str(thread_id),
                exception_class=exc.__class__.__name__,
            )

    async def load_active_facts(self, thread_id: UUID) -> list[TRGFactNode]:
        """Load all active facts for a thread (used on cold-start)."""
        try:
            async with self._session_factory() as session:
                result = await session.execute(
                    select(TRGFactRecord).where(
                        TRGFactRecord.thread_id == thread_id,
                        TRGFactRecord.active.is_(True),
                    )
                )
                rows = result.scalars().all()

            nodes: list[TRGFactNode] = []
            for row in rows:
                try:
                    raw_value = json.loads(row.fact_value)
                except json.JSONDecodeError:
                    raw_value = row.fact_value
                nodes.append(
                    TRGFactNode(
                        fact_path=row.fact_path,
                        value=raw_value,
                        confidence=row.confidence,
                        source_extraction=row.source_extraction,
                        raw_excerpt=row.raw_excerpt,
                        active=True,
                    )
                )
            return nodes
        except Exception as exc:
            logger.warning(
                "trg_fact_store_load_failed",
                thread_id=str(thread_id),
                exception_class=exc.__class__.__name__,
            )
            return []
