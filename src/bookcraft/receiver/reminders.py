"""Opt-in email reminders. Ambiguous sends require reconciliation, never blind retries."""

from __future__ import annotations

import asyncio
import hashlib
import smtplib
import ssl
from datetime import timedelta
from email.message import EmailMessage
from uuid import UUID

import structlog
from sqlalchemy import exists, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col, select

from bookcraft.components.storage.models import utc_now
from bookcraft.components.storage.thread_repository import local_thread_lock
from bookcraft.infra.config import Settings
from bookcraft.receiver.models import ReceiverAppointment, ReceiverReminder, ReceiverTombstone


def send_reminder(settings: Settings, appointment: ReceiverAppointment) -> None:
    email = appointment.payload.get("email")
    if not email or not settings.reminder_smtp_host or not settings.reminder_sender_email:
        raise ValueError("reminder_delivery_not_configured")
    message = EmailMessage()
    message["From"] = settings.reminder_sender_email
    message["To"] = email
    message["Subject"] = "Your BookCraft consultation"
    message["Message-ID"] = f"<{appointment.id}.{appointment.version}@bookcraft-reminders>"
    display = appointment.payload.get("preferredCallTime") or appointment.payload["startsAtUtc"]
    message.set_content(
        f"Your BookCraft consultation is scheduled for {display}. "
        "You can manage or cancel it in your appointment portal."
    )
    with smtplib.SMTP(settings.reminder_smtp_host, settings.reminder_smtp_port, timeout=15) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        if settings.reminder_smtp_username:
            smtp.login(settings.reminder_smtp_username, settings.reminder_smtp_password or "")
        smtp.send_message(message)


async def remind_one(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    appointment_id: UUID,
) -> None:
    async with sessions() as session:
        candidate = await session.get(ReceiverAppointment, appointment_id)
        if candidate is None:
            return
        tid = candidate.thread_id
    async with local_thread_lock(tid), sessions() as guard, guard.begin():
        if guard.get_bind().dialect.name == "postgresql":
            key = int.from_bytes(hashlib.sha256(str(tid).encode()).digest()[:8], "big", signed=True)
            await guard.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
        async with sessions() as session:
            row = await session.get(ReceiverAppointment, appointment_id)
            if (
                row is None
                or row.status != "scheduled"
                or not row.payload.get("remindersEnabled")
                or not (utc_now() < row.starts_at <= utc_now() + timedelta(hours=24))
                or (await session.get(ReceiverTombstone, tid)) is not None
            ):
                return
            reminder = await session.get(ReceiverReminder, (row.id, row.version))
            if reminder is not None:
                return
            # Persist an ambiguous state before crossing the SMTP boundary.
            reminder = ReceiverReminder(
                appointment_id=row.id, version=row.version, status="needs_reconciliation"
            )
            session.add(reminder)
            await session.commit()
            sending = asyncio.create_task(asyncio.to_thread(send_reminder, settings, row))
            try:
                await asyncio.shield(sending)
            except asyncio.CancelledError:
                await asyncio.gather(sending, return_exceptions=True)
                raise
            except Exception as exc:
                structlog.get_logger(__name__).warning(
                    "reminder_reconciliation_required", error=type(exc).__name__
                )
            else:
                reminder.status, reminder.updated_at = "sent", utc_now()
                session.add(reminder)
                await session.commit()


async def run_reminders(sessions: async_sessionmaker[AsyncSession], settings: Settings) -> None:
    while True:
        try:
            async with sessions() as session:
                rows = (
                    (
                        await session.execute(
                            select(ReceiverAppointment.id)
                            .where(
                                ReceiverAppointment.status == "scheduled",
                                ReceiverAppointment.payload["remindersEnabled"]
                                .as_boolean()
                                .is_(True),
                                ~exists().where(
                                    col(ReceiverReminder.appointment_id) == ReceiverAppointment.id,
                                    col(ReceiverReminder.version) == ReceiverAppointment.version,
                                ),
                                ReceiverAppointment.starts_at > utc_now(),
                                ReceiverAppointment.starts_at <= utc_now() + timedelta(hours=24),
                            )
                            .order_by(col(ReceiverAppointment.starts_at))
                            .limit(100)
                        )
                    )
                    .scalars()
                    .all()
                )
            for appointment_id in rows:
                await remind_one(sessions, settings, appointment_id)
        except Exception as exc:
            structlog.get_logger(__name__).warning(
                "reminder_worker_retry", error=type(exc).__name__
            )
        await asyncio.sleep(60)
