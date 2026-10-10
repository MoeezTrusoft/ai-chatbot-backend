"""Authenticated staff summaries, delivery repair and customer feedback."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlmodel import col, select

from bookcraft.api.management_access import factory, owned_thread, staff
from bookcraft.api.sales_access import sales_access, sales_enabled
from bookcraft.components.storage.management_models import FeedbackRecord, WorkflowRecord
from bookcraft.components.storage.models import (
    DeliveryOutboxRecord,
    SalesActionRecord,
    SalesConsultationRecord,
    SalesLeadRecord,
    ThreadRecord,
    utc_now,
)
from bookcraft.components.storage.thread_repository import ThreadRepository
from bookcraft.infra.redaction import redact_text

router = APIRouter(prefix="/api/v1", tags=["workspace"])


class FeedbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turn_sequence: int = Field(ge=1)
    helpful: bool
    reason: Literal["accurate", "unclear", "incorrect", "irrelevant", "slow", "other"]
    comment: str | None = Field(default=None, max_length=2000)


@router.put("/customer/threads/{thread_id}/feedback")
async def feedback(thread_id: UUID, payload: FeedbackInput, request: Request) -> dict[str, str]:
    sessions = factory(request)
    async with ThreadRepository(sessions).serialize(thread_id), sessions() as session:
        thread = await owned_thread(request, session, thread_id)
        if payload.turn_sequence > thread.turn_count:
            raise HTTPException(422, "turn_not_found")
        record = await session.get(FeedbackRecord, (thread_id, payload.turn_sequence))
        if record is None:
            record = FeedbackRecord(thread_id=thread_id, **payload.model_dump())
        record.helpful, record.reason = payload.helpful, payload.reason
        record.comment = redact_text(payload.comment) if payload.comment else None
        session.add(record)
        await session.commit()
    return {"status": "saved"}


@router.get("/staff/threads")
async def threads(
    request: Request,
    limit: int = Query(default=30, ge=1, le=100),
    before: UUID | None = None,
) -> list[dict[str, Any]]:
    staff(request)
    async with factory(request)() as session:
        statement = select(ThreadRecord).where(col(ThreadRecord.deleted_at).is_(None))
        if before is not None:
            statement = statement.where(col(ThreadRecord.id) < before)
        rows = (
            (await session.execute(statement.order_by(col(ThreadRecord.id).desc()).limit(limit)))
            .scalars()
            .all()
        )
        return [
            {
                "id": str(row.id),
                "updated_at": row.updated_at.isoformat(),
                "turn_count": row.turn_count,
                "sales_stage": row.sales_stage,
                "summary": row.state.get("rolling_summary", ""),
            }
            for row in rows
        ]


@router.get("/staff/threads/{thread_id}")
async def summary(thread_id: UUID, request: Request) -> dict[str, Any]:
    staff(request)
    sales_bookings = None
    if sales_enabled(request):
        async with sales_access(request, thread_id) as sales:
            sales_bookings = await sales.appointments(thread_id)
    async with factory(request)() as session:
        thread = await owned_thread(request, session, thread_id)
        bookings = (
            (
                await session.execute(
                    select(SalesConsultationRecord)
                    .where(SalesConsultationRecord.thread_id == thread_id)
                    .order_by(col(SalesConsultationRecord.updated_at).desc())
                    .limit(50)
                )
            )
            .scalars()
            .all()
        )
        failures = (
            (
                await session.execute(
                    select(SalesActionRecord)
                    .where(
                        SalesActionRecord.thread_id == thread_id,
                        col(SalesActionRecord.status).in_(["failed", "needs_reconciliation"]),
                    )
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )
        intake = (
            (
                await session.execute(
                    select(WorkflowRecord)
                    .where(WorkflowRecord.thread_id == thread_id, WorkflowRecord.kind == "intake")
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )
        state = thread.state
        return {
            "thread_id": str(thread.id),
            "summary": state.get("rolling_summary", ""),
            "confirmed_facts": {
                "personal": state.get("personal", {}),
                "project": state.get("project", {}),
                "contact": state.get("contact_info", {}),
            },
            "open_questions": state.get("pending_slots", []),
            "pending_question": state.get("pending_question", {}),
            "requested_services": state.get("service_metadata", {}),
            "appointments": sales_bookings
            if sales_bookings is not None
            else [row.model_dump(mode="json") for row in bookings],
            "failed_actions": [row.model_dump(mode="json") for row in failures],
            "intake": [row.model_dump(mode="json") for row in intake],
        }


@router.get("/staff/deliveries")
async def deliveries(
    request: Request, limit: int = Query(default=50, ge=1, le=100)
) -> list[dict[str, Any]]:
    staff(request)
    async with factory(request)() as session:
        rows = (
            (
                await session.execute(
                    select(DeliveryOutboxRecord)
                    .where(col(DeliveryOutboxRecord.completed_at).is_(None))
                    .order_by(col(DeliveryOutboxRecord.created_at))
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": str(row.id),
                "appointment_id": str(row.aggregate_id),
                "attempts": row.attempts,
                "quarantined": row.dead_letter_at is not None,
                "last_error": row.last_error,
                "available_at": row.available_at.isoformat(),
                "age_seconds": max(0, (utc_now() - row.created_at).total_seconds()),
            }
            for row in rows
        ]


@router.post("/staff/deliveries/{delivery_id}/replay")
async def replay(delivery_id: UUID, request: Request) -> dict[str, str]:
    principal = staff(request)
    if sales_enabled(request):
        raise HTTPException(409, "standalone_deliveries_disabled_in_sales_mode")
    async with factory(request)() as session:
        row = await session.get(DeliveryOutboxRecord, delivery_id, with_for_update=True)
        if row is None:
            raise HTTPException(404, "delivery_not_found")
        if row.completed_at is not None or row.dead_letter_at is None:
            raise HTTPException(409, "only_quarantined_deliveries_can_be_replayed")
        # Preserve the original idempotency key and payload.
        row.attempts, row.dead_letter_at, row.available_at = 0, None, utc_now()
        row.replayed_by = principal.subject
        session.add(row)
        await session.commit()
    return {"status": "queued"}


@router.get("/staff/analytics")
async def analytics(
    request: Request, days: int = Query(default=30, ge=1, le=365)
) -> dict[str, Any]:
    staff(request)
    cutoff = utc_now() - timedelta(days=days)
    async with factory(request)() as session:
        result: dict[str, Any] = {"days": days}
        for name, model in (
            ("conversations", ThreadRecord),
            ("leads", SalesLeadRecord),
            ("consultations", SalesConsultationRecord),
        ):
            result[name] = (
                await session.execute(
                    select(func.count()).select_from(model).where(col(model.created_at) >= cutoff)
                )
            ).scalar_one()
        votes = (
            await session.execute(
                select(FeedbackRecord.helpful, FeedbackRecord.reason, func.count())
                .where(FeedbackRecord.created_at >= cutoff)
                .group_by(col(FeedbackRecord.helpful), col(FeedbackRecord.reason))
            )
        ).all()
        result["feedback"] = [
            {"helpful": row[0], "reason": row[1], "count": row[2]} for row in votes
        ]
        result["booked_conversations"] = (
            await session.execute(
                select(func.count(func.distinct(SalesConsultationRecord.thread_id))).where(
                    col(SalesConsultationRecord.thread_id).in_(
                        select(ThreadRecord.id).where(ThreadRecord.created_at >= cutoff)
                    ),
                    SalesConsultationRecord.status == "scheduled",
                )
            )
        ).scalar_one()
        result["consultation_conversion"] = (
            result["booked_conversations"] / result["conversations"]
            if result["conversations"]
            else None
        )
        if sales_enabled(request):
            result.update(
                leads=None,
                consultations=None,
                booked_conversations=None,
                consultation_conversion=None,
                sales_metrics_source="erp_sales",
            )
        return result
