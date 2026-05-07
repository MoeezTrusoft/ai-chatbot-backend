from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from bookcraft.components.storage.models import ThreadEvent


def canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def calculate_event_hash(
    *,
    thread_id: UUID,
    sequence: int,
    event_type: str,
    payload: dict[str, Any],
    previous_hash: str | None,
) -> str:
    hash_input = "|".join(
        [
            str(thread_id),
            str(sequence),
            event_type,
            canonical_json(payload),
            previous_hash or "",
        ]
    )
    return hashlib.sha256(hash_input.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class EventChainService:
    session: AsyncSession

    async def append_event(
        self,
        *,
        thread_id: UUID,
        event_type: str,
        payload: dict[str, Any],
        actor: str = "system",
        confidence: float | None = None,
    ) -> ThreadEvent:
        previous_event = await self._latest_event(thread_id)
        sequence = 1 if previous_event is None else previous_event.sequence + 1
        previous_hash = previous_event.event_hash if previous_event is not None else None
        event_hash = calculate_event_hash(
            thread_id=thread_id,
            sequence=sequence,
            event_type=event_type,
            payload=payload,
            previous_hash=previous_hash,
        )
        event = ThreadEvent(
            thread_id=thread_id,
            sequence=sequence,
            event_type=event_type,
            actor=actor,
            payload=payload,
            confidence=confidence,
            previous_hash=previous_hash,
            event_hash=event_hash,
        )
        self.session.add(event)
        return event

    async def verify_chain(self, thread_id: UUID) -> bool:
        result = await self.session.execute(
            select(ThreadEvent)
            .where(col(ThreadEvent.thread_id) == thread_id)
            .order_by(col(ThreadEvent.sequence).asc())
        )
        previous_hash: str | None = None
        expected_sequence = 1
        for event in result.scalars():
            expected_hash = calculate_event_hash(
                thread_id=event.thread_id,
                sequence=event.sequence,
                event_type=event.event_type,
                payload=event.payload,
                previous_hash=previous_hash,
            )
            if event.sequence != expected_sequence:
                return False
            if event.previous_hash != previous_hash or event.event_hash != expected_hash:
                return False
            previous_hash = event.event_hash
            expected_sequence += 1
        return True

    async def _latest_event(self, thread_id: UUID) -> ThreadEvent | None:
        result = await self.session.execute(
            select(ThreadEvent)
            .where(col(ThreadEvent.thread_id) == thread_id)
            .order_by(col(ThreadEvent.sequence).desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
