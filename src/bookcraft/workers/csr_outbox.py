"""Durable, at-least-once CSR delivery, with one ordered consumer across workers."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import httpx
import structlog
from prometheus_client import Counter, Gauge
from sqlalchemy import and_, exists, func, or_, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased
from sqlmodel import col, select

from bookcraft.components.storage.models import (
    DeliveryOutboxRecord,
    SalesConsultationRecord,
    utc_now,
)

DELIVERIES = Counter("csr_outbox_deliveries_total", "CSR delivery attempts.", ["result"])
OLDEST_PENDING = Gauge("csr_outbox_oldest_pending_seconds", "Age of oldest pending CSR delivery.")


class CsrOutboxWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        url: str,
        client: httpx.AsyncClient,
        token: str | None = None,
        max_attempts: int = 12,
    ) -> None:
        self.session_factory = session_factory
        self.url = url.rstrip("/") + "/api/consultations"
        self.client = client
        self.token = token
        self.max_attempts = max_attempts

    async def deliver_one(self) -> bool:
        async with self.session_factory() as session, session.begin():
            if session.get_bind().dialect.name == "postgresql":
                acquired = (
                    await session.execute(text("SELECT pg_try_advisory_xact_lock(72649101)"))
                ).scalar_one()
                if not acquired:
                    return False
            # Keep order per consultation while allowing unrelated bookings past
            # a failed delivery. Acknowledge only after a successful POST.
            oldest = (
                await session.execute(
                    select(func.min(DeliveryOutboxRecord.created_at)).where(
                        col(DeliveryOutboxRecord.completed_at).is_(None),
                        col(DeliveryOutboxRecord.dead_letter_at).is_(None),
                    )
                )
            ).scalar_one()
            OLDEST_PENDING.set(max(0, (utc_now() - oldest).total_seconds()) if oldest else 0)
            earlier = aliased(DeliveryOutboxRecord)
            pending_predecessor = exists(
                select(col(earlier.id)).where(
                    col(earlier.aggregate_id) == col(DeliveryOutboxRecord.aggregate_id),
                    col(earlier.completed_at).is_(None),
                    col(earlier.dead_letter_at).is_(None),
                    or_(
                        col(earlier.created_at) < col(DeliveryOutboxRecord.created_at),
                        and_(
                            col(earlier.created_at) == col(DeliveryOutboxRecord.created_at),
                            col(earlier.id) < col(DeliveryOutboxRecord.id),
                        ),
                    ),
                )
            )
            result = await session.execute(
                select(DeliveryOutboxRecord)
                .where(
                    col(DeliveryOutboxRecord.completed_at).is_(None),
                    col(DeliveryOutboxRecord.dead_letter_at).is_(None),
                    col(DeliveryOutboxRecord.available_at) <= utc_now(),
                    ~pending_predecessor,
                )
                .order_by(col(DeliveryOutboxRecord.created_at), col(DeliveryOutboxRecord.id))
                .limit(1)
                .with_for_update()
            )
            record = result.scalar_one_or_none()
            if record is None or record.available_at > utc_now():
                return False
            appointment = await session.get(SalesConsultationRecord, record.aggregate_id)
            version = int(record.payload.get("version", 1))
            if appointment is not None and version < appointment.version:
                record.completed_at = utc_now()
                record.last_error = "superseded"
                session.add(record)
                return True
            payload = {
                **record.payload,
                "externalAppointmentId": str(record.aggregate_id),
                "version": version,
                "status": record.payload.get("status", "scheduled"),
            }
            headers = {"Idempotency-Key": str(record.id)}
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            record.attempts += 1
            try:
                response = await self.client.post(self.url, json=payload, headers=headers)
                response.raise_for_status()
                acknowledgement = response.json()
                if (
                    acknowledgement.get("externalAppointmentId") != str(record.aggregate_id)
                    or acknowledgement.get("version", 0) < version
                ):
                    raise ValueError("invalid_receiver_acknowledgement")
            except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
                record.last_error = type(exc).__name__
                record.available_at = utc_now() + timedelta(
                    seconds=min(3600, 2 ** min(record.attempts, 11))
                )
                DELIVERIES.labels("failed").inc()
                permanent = isinstance(exc, httpx.HTTPStatusError) and (
                    400 <= exc.response.status_code < 500
                    and exc.response.status_code not in {408, 429}
                )
                if permanent or record.attempts >= self.max_attempts:
                    record.dead_letter_at = utc_now()
                    DELIVERIES.labels("quarantined").inc()
            else:
                record.completed_at = utc_now()
                record.last_error = None
                DELIVERIES.labels("delivered").inc()
            session.add(record)
            return True

    async def run(self) -> None:
        while True:
            try:
                delivered = await self.deliver_one()
            except Exception:
                structlog.get_logger(__name__).exception("csr_outbox_iteration_failed")
                delivered = False
            if not delivered:
                await asyncio.sleep(2)
