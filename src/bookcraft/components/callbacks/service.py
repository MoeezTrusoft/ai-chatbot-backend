"""Stores callback requests for the team, idempotently."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from bookcraft.components.callbacks.schemas import CallbackMode, CallbackRequest


def callback_idempotency_key(
    *, thread_id: UUID, visit_id: str | None, mode: str, requested_time_text: str | None
) -> str:
    """One request per thread, visit, mode and requested time.

    A repeated "yes" (a double tap, or a retried turn) must never produce a second
    call to the same visitor.
    """
    raw = f"{thread_id}:{visit_id or '-'}:{mode}:{(requested_time_text or '').strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


class CallbackRepository(Protocol):
    async def create(self, values: dict[str, Any]) -> tuple[dict[str, Any], bool]: ...


class InMemoryCallbackRepository:
    """Used in tests and when no database is configured."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def create(self, values: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        key = values["idempotency_key"]
        if key in self.rows:
            return self.rows[key], False
        self.rows[key] = dict(values)
        return self.rows[key], True


@dataclass(slots=True)
class DbCallbackRepository:
    session_factory: async_sessionmaker[AsyncSession]

    async def create(self, values: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        from bookcraft.components.storage.models import SalesCallbackRequestRecord

        record = SalesCallbackRequestRecord(**values)
        try:
            async with self.session_factory() as session:
                session.add(record)
                await session.commit()
            return dict(values), True
        except IntegrityError:
            # The unique idempotency key already exists: return the stored request.
            async with self.session_factory() as session:
                result = await session.execute(
                    select(SalesCallbackRequestRecord).where(
                        SalesCallbackRequestRecord.idempotency_key == values["idempotency_key"]
                    )
                )
                existing = result.scalar_one()
                return existing.model_dump(), False


@dataclass(slots=True)
class CallbackRequestService:
    repository: CallbackRepository = field(default_factory=InMemoryCallbackRepository)
    callback_minutes: int = 5

    async def request(
        self,
        *,
        thread_id: UUID,
        customer_id: UUID | None,
        visit_id: str | None,
        mode: CallbackMode,
        phone: str,
        name: str | None,
        requested_time_text: str | None,
        customer_timezone: str | None,
        summary: str,
        now_utc: datetime | None = None,
    ) -> CallbackRequest:
        if not phone or sum(ch.isdigit() for ch in phone) < 7:
            raise ValueError("callback_requires_phone")
        created_at = (now_utc or datetime.now(UTC)).astimezone(UTC).replace(tzinfo=None)
        values: dict[str, Any] = {
            "id": uuid4(),
            "thread_id": thread_id,
            "customer_id": customer_id,
            "visit_id": visit_id,
            "mode": mode,
            "status": "requested",
            "customer_phone": phone,
            "customer_name": name,
            "requested_time_text": requested_time_text,
            "customer_timezone": customer_timezone,
            "summary": summary[:2000],
            "promised_within_minutes": self.callback_minutes if mode == "now" else None,
            "idempotency_key": callback_idempotency_key(
                thread_id=thread_id,
                visit_id=visit_id,
                mode=mode,
                requested_time_text=requested_time_text,
            ),
            "created_at": created_at,
        }
        stored, created = await self.repository.create(values)
        structlog.get_logger(__name__).info(
            "callback_requested",
            thread_id=str(thread_id),
            callback_id=str(stored["id"]),
            mode=mode,
            created=created,
        )
        return CallbackRequest(
            callback_id=str(stored["id"]),
            thread_id=thread_id,
            customer_id=customer_id,
            visit_id=visit_id,
            mode=stored["mode"],
            phone=stored["customer_phone"],
            name=stored.get("customer_name"),
            requested_time_text=stored.get("requested_time_text"),
            customer_timezone=stored.get("customer_timezone"),
            summary=stored.get("summary") or "",
            promised_within_minutes=stored.get("promised_within_minutes"),
            created_at=stored["created_at"],
            created=created,
        )
