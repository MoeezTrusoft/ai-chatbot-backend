"""Idempotent, ordered appointment receiver; credentials are never accepted in URLs."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col, select

from bookcraft.components.storage.db import create_engine, create_session_factory
from bookcraft.components.storage.thread_repository import local_thread_lock
from bookcraft.infra.config import Settings, get_settings
from bookcraft.receiver.models import (
    ReceiverAppointment,
    ReceiverReceipt,
    ReceiverReminder,
    ReceiverTombstone,
)
from bookcraft.receiver.reminders import run_reminders


class AppointmentDelivery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    externalAppointmentId: UUID
    version: int = Field(ge=1)
    status: Literal["scheduled", "cancelled"]
    startsAtUtc: datetime
    endsAtUtc: datetime
    customerId: UUID | None = None
    csrId: str = Field(min_length=1, max_length=128)
    name: str = Field(max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    email: str | None = Field(default=None, max_length=255)
    customerTimezone: str | None = Field(default=None, max_length=64)
    preferredCallTime: str | None = None
    houstonTime: str | None = None
    csrName: str | None = None
    services: list[str] = Field(default_factory=list, max_length=30)
    threadId: UUID
    source: Literal["ai_chatbot"] = "ai_chatbot"
    remindersEnabled: bool = False

    @model_validator(mode="after")
    def valid_interval(self) -> AppointmentDelivery:
        if self.startsAtUtc.tzinfo is None or self.endsAtUtc.tzinfo is None:
            raise ValueError("timezone_required")
        if self.endsAtUtc <= self.startsAtUtc:
            raise ValueError("invalid_interval")
        return self


def create_receiver(
    settings: Settings | None = None,
    factory: async_sessionmaker[AsyncSession] | None = None,
) -> FastAPI:
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if not resolved.csr_node_api_token or len(resolved.csr_node_api_token) < 32:
            raise RuntimeError("CSR_NODE_API_TOKEN must contain at least 32 characters")
        engine = None
        if factory is None:
            engine = create_engine(resolved)
            app.state.factory = create_session_factory(engine)
        else:
            app.state.factory = factory
        reminder_task = None
        if resolved.reminder_smtp_host and not resolved.reminder_sender_email:
            raise RuntimeError("REMINDER_SENDER_EMAIL is required for SMTP reminders")
        if resolved.reminder_smtp_host:
            reminder_task = asyncio.create_task(run_reminders(app.state.factory, resolved))
        try:
            yield
        finally:
            if reminder_task is not None:
                reminder_task.cancel()
                with suppress(asyncio.CancelledError):
                    await reminder_task
            if engine is not None:
                await engine.dispose()

    app = FastAPI(title="BookCraft CSR Receiver", lifespan=lifespan)

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/consultations")
    async def receive(
        payload: AppointmentDelivery,
        request: Request,
        authorization: str = Header(default=""),
        idempotency_key: str = Header(min_length=1, max_length=128),
    ) -> dict[str, Any]:
        expected = f"Bearer {resolved.csr_node_api_token}"
        if not resolved.csr_node_api_token or not hmac.compare_digest(authorization, expected):
            raise HTTPException(401, "invalid_service_credentials")
        data = payload.model_dump(mode="json")
        digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        async with local_thread_lock(payload.threadId):
            async with request.app.state.factory() as session, session.begin():
                if session.get_bind().dialect.name == "postgresql":
                    # Serialize identical idempotency keys too, including malicious key reuse.
                    for value in (
                        str(payload.threadId),
                        idempotency_key,
                        str(payload.externalAppointmentId),
                    ):
                        key = int.from_bytes(
                            hashlib.sha256(value.encode()).digest()[:8], "big", signed=True
                        )
                        await session.execute(
                            text("SELECT pg_advisory_xact_lock(:key)"), {"key": key}
                        )
                receipt = await session.get(ReceiverReceipt, idempotency_key)
                if await session.get(ReceiverTombstone, payload.threadId):
                    raise HTTPException(410, "thread_deleted")
                if receipt and receipt.payload_hash != digest:
                    raise HTTPException(409, "idempotency_key_reused_with_different_payload")
                record = await session.get(ReceiverAppointment, payload.externalAppointmentId)
                if record is not None and record.thread_id != payload.threadId:
                    raise HTTPException(409, "appointment_thread_conflict")
                if record and payload.version == record.version and record.payload != data:
                    raise HTTPException(409, "appointment_version_conflict")
                if record is None:
                    record = ReceiverAppointment(
                        id=payload.externalAppointmentId,
                        thread_id=payload.threadId,
                        starts_at=payload.startsAtUtc.astimezone(UTC).replace(tzinfo=None),
                        version=payload.version,
                        status=payload.status,
                        payload=data,
                    )
                elif payload.version > record.version:
                    record.version, record.status, record.payload = (
                        payload.version,
                        payload.status,
                        data,
                    )
                    record.starts_at = payload.startsAtUtc.astimezone(UTC).replace(tzinfo=None)
                session.add(record)
                if receipt is None:
                    session.add(
                        ReceiverReceipt(
                            id=idempotency_key,
                            payload_hash=digest,
                            appointment_id=payload.externalAppointmentId,
                        )
                    )
                return {
                    "externalAppointmentId": str(record.id),
                    "version": record.version,
                    "status": record.status,
                    "duplicate": receipt is not None,
                }

    @app.delete("/api/threads/{thread_id}/data")
    async def erase(
        thread_id: UUID, request: Request, authorization: str = Header(default="")
    ) -> dict[str, str]:
        if not resolved.csr_node_api_token or not hmac.compare_digest(
            authorization, f"Bearer {resolved.csr_node_api_token}"
        ):
            raise HTTPException(401, "invalid_service_credentials")
        async with (
            local_thread_lock(thread_id),
            request.app.state.factory() as session,
            session.begin(),
        ):
            if session.get_bind().dialect.name == "postgresql":
                key = int.from_bytes(
                    hashlib.sha256(str(thread_id).encode()).digest()[:8], "big", signed=True
                )
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
            # A durable tombstone rejects delayed deliveries after erasure.
            tombstone = await session.get(ReceiverTombstone, thread_id)
            if tombstone is None:
                session.add(ReceiverTombstone(thread_id=thread_id))
                await session.flush()
            rows = (
                (
                    await session.execute(
                        select(ReceiverAppointment).where(
                            ReceiverAppointment.thread_id == thread_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                if row.payload.get("threadId") == str(thread_id):
                    await session.execute(
                        delete(ReceiverReceipt).where(col(ReceiverReceipt.appointment_id) == row.id)
                    )
                    await session.execute(
                        delete(ReceiverReminder).where(
                            col(ReceiverReminder.appointment_id) == row.id
                        )
                    )
                    await session.delete(row)
        return {"status": "deleted"}

    return app


app = create_receiver()
