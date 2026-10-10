from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import col, select

from bookcraft.api.chat import ConsultationConfirmedRequest
from bookcraft.api.management_access import factory, owned_thread
from bookcraft.api.sales_access import sales_access, sales_enabled
from bookcraft.components.consultations.holidays import is_blackout_date
from bookcraft.components.consultations.repository import (
    AppointmentConflictError,
    ConsultationRepository,
    commit_appointment,
)
from bookcraft.components.consultations.schemas import CSRProfile
from bookcraft.components.consultations.service import DEFAULT_CSR_ROSTER
from bookcraft.components.storage.management_models import WorkflowRecord
from bookcraft.components.storage.models import SalesConsultationRecord, utc_now
from bookcraft.components.storage.thread_repository import ThreadRepository
from bookcraft.domain.enums import ServiceCategory
from bookcraft.domain.state import ThreadState
from bookcraft.services.chat import ChatService

router = APIRouter(prefix="/api/v1/customer/threads/{thread_id}", tags=["customer-appointments"])
BUSINESS_TZ = ZoneInfo("America/Chicago")


class BookingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    appointment_id: UUID
    request_id: UUID | None = None
    expected_version: int = Field(default=0, ge=0)
    starts_at: datetime
    customer_timezone: str = Field(max_length=64)
    csr_id: str = Field(max_length=200)
    name: str = Field(min_length=1, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    services: list[ServiceCategory] = Field(default_factory=list, max_length=20)
    reminders_enabled: bool = False


def customer_zone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(422, "invalid_timezone") from exc


def booking_policy(request: Request) -> tuple[list[CSRProfile], int, int]:
    chat = getattr(request.app.state, "chat_service", None)
    dispatcher = getattr(chat, "action_dispatcher", None)
    service = getattr(dispatcher, "consultation_action_service", None)
    if service is None:
        return list(DEFAULT_CSR_ROSTER), 10, 19
    return service.csr_roster, service.business_start_hour, service.business_end_hour


def valid_slot(start: datetime, opening: int = 10, closing: int = 19) -> datetime:
    if start.tzinfo is None:
        raise HTTPException(422, "timezone_required")
    local = start.astimezone(BUSINESS_TZ)
    end = local + timedelta(minutes=30)
    if (
        start <= datetime.now(UTC)
        or start > datetime.now(UTC) + timedelta(days=180)
        or local.weekday() >= 5
        or is_blackout_date(local.date())
        or local.hour < opening
        or end.time() > time(closing)
        or end.date() != local.date()
        or local.minute not in {0, 30}
        or local.second
        or local.microsecond
    ):
        raise HTTPException(422, "slot_outside_business_availability")
    return local


@router.get("/appointments/availability")
async def availability(
    thread_id: UUID, request: Request, day: date, timezone: str = "America/Chicago"
) -> list[dict[str, Any]]:
    zone = customer_zone(timezone)
    if sales_enabled(request):
        async with sales_access(request, thread_id) as sales:
            return await sales.availability(thread_id, day, timezone)
    roster, opening, closing = booking_policy(request)
    if day < datetime.now(BUSINESS_TZ).date() or day > datetime.now(BUSINESS_TZ).date() + timedelta(
        days=180
    ):
        raise HTTPException(422, "date_out_of_range")
    async with factory(request)() as session:
        await owned_thread(request, session, thread_id)
        if day.weekday() >= 5 or is_blackout_date(day):
            return []
        start = datetime.combine(day, time(opening), BUSINESS_TZ)
        end = datetime.combine(day, time(closing), BUSINESS_TZ)
        records = (
            (
                await session.execute(
                    select(SalesConsultationRecord).where(
                        SalesConsultationRecord.status == "scheduled",
                        col(SalesConsultationRecord.cancelled_at).is_(None),
                        SalesConsultationRecord.starts_at_utc
                        < end.astimezone(UTC).replace(tzinfo=None),
                        SalesConsultationRecord.ends_at_utc
                        > start.astimezone(UTC).replace(tzinfo=None),
                    )
                )
            )
            .scalars()
            .all()
        )
    slots = []
    while start < end:
        stop = start + timedelta(minutes=30)
        if start > datetime.now(UTC):
            for csr in roster:
                if csr.active and not any(
                    row.csr_id == csr.csr_id
                    and row.starts_at_utc < stop.astimezone(UTC).replace(tzinfo=None)
                    and row.ends_at_utc > start.astimezone(UTC).replace(tzinfo=None)
                    for row in records
                ):
                    slots.append(
                        {
                            "starts_at": start.astimezone(UTC).isoformat(),
                            "customer_time": start.astimezone(zone).isoformat(),
                            "csr_id": csr.csr_id,
                            "csr_name": csr.name,
                        }
                    )
        start = stop
    return slots


@router.get("/appointments")
async def appointments(thread_id: UUID, request: Request) -> list[dict[str, Any]]:
    if sales_enabled(request):
        async with sales_access(request, thread_id) as sales:
            return await sales.appointments(thread_id)
    async with factory(request)() as session:
        await owned_thread(request, session, thread_id)
        records = (
            (
                await session.execute(
                    select(SalesConsultationRecord)
                    .where(SalesConsultationRecord.thread_id == thread_id)
                    .order_by(col(SalesConsultationRecord.created_at).desc())
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )
        return [row.model_dump(mode="json") for row in records]


@router.post("/appointments")
async def book(thread_id: UUID, payload: BookingInput, request: Request) -> dict[str, Any]:
    if sales_enabled(request):
        customer_zone(payload.customer_timezone)
        if payload.starts_at.tzinfo is None:
            raise HTTPException(422, "timezone_required")
        if payload.reminders_enabled:
            raise HTTPException(422, "reminders_managed_by_sales_communication")
        if payload.expected_version and payload.request_id is None:
            raise HTTPException(422, "request_id_required")
        arguments: dict[str, Any] = {
            "startTime": payload.starts_at.isoformat(),
            "userTimezone": payload.customer_timezone,
        }
        if payload.csr_id:
            arguments["preferredOwnerId"] = payload.csr_id
        if payload.expected_version:
            arguments.update(
                consultationId=str(payload.appointment_id), expectedVersion=payload.expected_version
            )
        async with sales_access(request, thread_id) as sales:
            return await sales.mutate(
                thread_id,
                payload.request_id or payload.appointment_id,
                "reschedule" if payload.expected_version else "schedule",
                arguments,
            )
    roster, opening, closing = booking_policy(request)
    local = valid_slot(payload.starts_at, opening, closing)
    zone = customer_zone(payload.customer_timezone)
    csr = next((p for p in roster if p.csr_id == payload.csr_id and p.active), None)
    if csr is None or not (payload.email or payload.phone):
        raise HTTPException(422, "valid_csr_and_contact_required")
    if payload.reminders_enabled and not payload.email:
        raise HTTPException(422, "email_required_for_reminders")
    sessions = factory(request)
    async with ThreadRepository(sessions).serialize(thread_id), sessions() as session:
        thread = await owned_thread(request, session, thread_id)
        record = await session.get(
            SalesConsultationRecord, payload.appointment_id, with_for_update=True
        )
        if record is not None and record.thread_id != thread_id:
            raise HTTPException(403, "appointment_not_accessible")
        if (record.version if record else 0) != payload.expected_version:
            raise HTTPException(409, "appointment_changed_refresh_before_retry")
        if record is None:
            active = (
                (
                    await session.execute(
                        select(SalesConsultationRecord).where(
                            SalesConsultationRecord.thread_id == thread_id,
                            SalesConsultationRecord.status == "scheduled",
                            col(SalesConsultationRecord.cancelled_at).is_(None),
                        )
                    )
                )
                .scalars()
                .first()
            )
            if active:
                raise HTTPException(409, "reschedule_existing_appointment")
            record = SalesConsultationRecord(
                id=payload.appointment_id,
                thread_id=thread_id,
                customer_id=thread.customer_id,
                customer_name=payload.name,
                csr_id=csr.csr_id,
                csr_name=csr.name,
                starts_at_utc=local.astimezone(UTC).replace(tzinfo=None),
                ends_at_utc=(local + timedelta(minutes=30)).astimezone(UTC).replace(tzinfo=None),
                houston_display_time=local.isoformat(),
            )
        else:
            record.version += 1
        record.customer_name, record.customer_email, record.customer_phone = (
            payload.name,
            payload.email,
            payload.phone,
        )
        record.csr_id, record.csr_name = csr.csr_id, csr.name
        record.priority_rank = csr.priority_rank
        record.starts_at_utc = local.astimezone(UTC).replace(tzinfo=None)
        record.requested_time_text = payload.starts_at.isoformat()
        record.ends_at_utc = record.starts_at_utc + timedelta(minutes=30)
        record.customer_timezone = payload.customer_timezone
        record.houston_display_time = local.isoformat()
        record.customer_display_time = local.astimezone(zone).isoformat()
        record.status, record.cancelled_at = "scheduled", None
        record.services = [service.value for service in payload.services]
        record.reminders_enabled = payload.reminders_enabled
        record.updated_at = utc_now()
        state = ThreadState.model_validate(thread.state)
        ChatService._apply_external_consultation_confirmation(
            state,
            ConsultationConfirmedRequest(
                thread_id=thread_id,
                appointment_id=str(record.id),
                csr_name=record.csr_name,
                customer_timezone=record.customer_timezone,
                customer_display_time=record.customer_display_time,
                houston_display_time=record.houston_display_time,
            ),
            str(record.id),
        )
        thread.state, thread.version, thread.updated_at = (
            state.model_dump(mode="json"),
            thread.version + 1,
            utc_now(),
        )
        session.add_all([record, thread])
        ConsultationRepository(sessions, enqueue_csr_sync=True)._enqueue(session, record)
        try:
            await commit_appointment(session)
        except AppointmentConflictError as exc:
            raise HTTPException(409, "slot_no_longer_available") from exc
        return record.model_dump(mode="json")


@router.delete("/appointments/{appointment_id}")
async def cancel(
    thread_id: UUID,
    appointment_id: UUID,
    request: Request,
    expected_version: int = Query(ge=1),
    request_id: UUID | None = None,
    reason: str = Query(default="Customer requested cancellation", min_length=1, max_length=1000),
) -> dict[str, Any]:
    if sales_enabled(request):
        if request_id is None:
            raise HTTPException(422, "request_id_required")
        async with sales_access(request, thread_id) as sales:
            return await sales.mutate(
                thread_id,
                request_id,
                "cancel",
                {
                    "consultationId": str(appointment_id),
                    "expectedVersion": expected_version,
                    "reason": reason,
                },
            )
    sessions = factory(request)
    async with ThreadRepository(sessions).serialize(thread_id), sessions() as session:
        thread = await owned_thread(request, session, thread_id)
        record = await session.get(SalesConsultationRecord, appointment_id, with_for_update=True)
        if record is None or record.thread_id != thread_id:
            raise HTTPException(404, "appointment_not_found")
        if record.status == "cancelled":
            return record.model_dump(mode="json")
        if record.version != expected_version:
            raise HTTPException(409, "appointment_changed_refresh_before_retry")
        record.status, record.cancelled_at = "cancelled", utc_now()
        record.version += 1
        record.updated_at = utc_now()
        state = ThreadState.model_validate(thread.state)
        ChatService._apply_external_consultation_cancellation(state, str(record.id))
        thread.state, thread.version, thread.updated_at = (
            state.model_dump(mode="json"),
            thread.version + 1,
            utc_now(),
        )
        session.add_all([record, thread])
        ConsultationRepository(sessions, enqueue_csr_sync=True)._enqueue(session, record)
        await session.commit()
        return record.model_dump(mode="json")


class ConfirmInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_version: int = Field(ge=1)


class SalesContactInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    phone: str = Field(pattern=r"^\+[1-9]\d{7,14}$")
    email: str | None = Field(default=None, max_length=255)


@router.get("/appointments/policy")
async def policy(thread_id: UUID, request: Request) -> dict[str, Any]:
    async with factory(request)() as session:
        await owned_thread(request, session, thread_id)
    if sales_enabled(request):
        async with sales_access(request, thread_id) as sales:
            return await sales.policy()
    return {"mode": "standalone", "reminders": "email_opt_in"}


@router.get("/appointments/contact")
async def sales_contact(thread_id: UUID, request: Request) -> dict[str, Any]:
    if not sales_enabled(request):
        raise HTTPException(404, "sales_mode_required")
    async with sales_access(request, thread_id) as sales:
        profile = await sales.profile(thread_id)
        return {key: profile.get(key) for key in ("name", "email", "phone")}


@router.post("/appointments/contact")
async def save_sales_contact(
    thread_id: UUID,
    payload: SalesContactInput,
    request: Request,
) -> dict[str, str]:
    if not sales_enabled(request):
        raise HTTPException(404, "sales_mode_required")
    if not payload.name.strip():
        raise HTTPException(422, "name_required")
    async with sales_access(request, thread_id) as sales:
        return await sales.contact(
            thread_id, payload.model_dump(exclude_unset=True, exclude_none=True)
        )


@router.post("/appointments/{appointment_id}/confirm")
async def confirm_sales_offer(
    thread_id: UUID,
    appointment_id: UUID,
    payload: ConfirmInput,
    request: Request,
) -> dict[str, Any]:
    if not sales_enabled(request):
        raise HTTPException(404, "sales_mode_required")
    async with sales_access(request, thread_id) as sales:
        return await sales.mutate(
            thread_id,
            payload.request_id,
            "confirm",
            {
                "consultationId": str(appointment_id),
                "expectedVersion": payload.expected_version,
            },
        )


@router.get("/appointments/operations")
async def pending_sales_operations(thread_id: UUID, request: Request) -> list[dict[str, Any]]:
    async with factory(request)() as session:
        await owned_thread(request, session, thread_id)
        rows = (
            (
                await session.execute(
                    select(WorkflowRecord).where(
                        WorkflowRecord.thread_id == thread_id,
                        WorkflowRecord.kind == "sales_operation",
                        WorkflowRecord.status == "pending",
                    )
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "request_id": str(row.id),
                "status": row.status,
                "action": row.payload["request"]["tool"],
            }
            for row in rows
        ]


@router.post("/appointments/operations/{request_id}/retry")
async def retry_sales_operation(
    thread_id: UUID,
    request_id: UUID,
    request: Request,
) -> dict[str, Any]:
    if not sales_enabled(request):
        raise HTTPException(404, "sales_mode_required")
    async with sales_access(request, thread_id) as sales:
        async with factory(request)() as session:
            row = await session.get(WorkflowRecord, request_id)
            if row is None or row.thread_id != thread_id or row.kind != "sales_operation":
                raise HTTPException(404, "sales_operation_not_found")
            intent = row.payload["request"]
            arguments = {
                k: v for k, v in intent["arguments"].items() if k not in {"requestId", "leadId"}
            }
        return await sales.mutate(
            thread_id,
            request_id,
            intent["tool"].removeprefix("sales_consultation_"),
            arguments,
        )
