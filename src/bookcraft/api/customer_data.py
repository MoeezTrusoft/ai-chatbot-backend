from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlmodel import col, select

from bookcraft.api.management_access import factory, owned_thread
from bookcraft.components.storage.management_models import WorkflowRecord
from bookcraft.components.storage.models import ThreadEvent, utc_now
from bookcraft.components.storage.thread_repository import ThreadRepository

router = APIRouter(prefix="/api/v1/customer/threads/{thread_id}", tags=["customer-data"])


@router.get("/export")
async def export_thread(
    thread_id: UUID,
    request: Request,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    async with factory(request)() as session:
        thread = await owned_thread(request, session, thread_id)
        events = (
            (
                await session.execute(
                    select(ThreadEvent)
                    .where(
                        ThreadEvent.thread_id == thread_id,
                        ThreadEvent.sequence > after_sequence,
                        col(ThreadEvent.event_type).in_(
                            ["user.message", "assistant.response", "assistant.redirect"]
                        ),
                    )
                    .order_by(col(ThreadEvent.sequence))
                    .limit(limit + 1)
                )
            )
            .scalars()
            .all()
        )
        page = events[:limit]
        return {
            "thread_id": str(thread_id),
            "personal": thread.state.get("personal", {}),
            "project": thread.state.get("project", {}),
            "attachments": thread.state.get("attachments_received", []),
            "messages": [
                {
                    "sequence": row.sequence,
                    "type": row.event_type,
                    "created_at": row.created_at.isoformat(),
                    "payload": row.payload,
                }
                for row in page
            ],
            "next_after_sequence": page[-1].sequence if len(events) > limit else None,
        }


@router.delete("/data", status_code=202)
async def delete_thread(thread_id: UUID, request: Request) -> dict[str, str]:
    sessions = factory(request)
    async with ThreadRepository(sessions).serialize(thread_id), sessions() as session:
        thread = await owned_thread(request, session, thread_id, allow_deleted=True)
        existing = (
            (
                await session.execute(
                    select(WorkflowRecord).where(
                        WorkflowRecord.thread_id == thread_id, WorkflowRecord.kind == "deletion"
                    )
                )
            )
            .scalars()
            .first()
        )
        if existing:
            return {"request_id": str(existing.id), "status": existing.status}
        thread.deleted_at, thread.deletion_reason = utc_now(), "customer_request"
        record = WorkflowRecord(thread_id=thread_id, kind="deletion", status="queued")
        session.add_all([thread, record])
        await session.commit()
        return {"request_id": str(record.id), "status": "queued"}


@router.get("/deletion")
async def deletion_status(thread_id: UUID, request: Request) -> dict[str, str]:
    async with factory(request)() as session:
        await owned_thread(request, session, thread_id, allow_deleted=True)
        record = (
            (
                await session.execute(
                    select(WorkflowRecord).where(
                        WorkflowRecord.thread_id == thread_id, WorkflowRecord.kind == "deletion"
                    )
                )
            )
            .scalars()
            .first()
        )
        return {"status": record.status if record else "not_requested"}
