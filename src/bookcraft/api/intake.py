"""Versioned upload-service callbacks. Manuscript bytes never enter the chat pipeline."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import select

from bookcraft.api.management_access import factory, owned_thread, staff
from bookcraft.components.storage.management_models import WorkflowRecord
from bookcraft.components.storage.models import utc_now
from bookcraft.components.storage.thread_repository import ThreadRepository

router = APIRouter(prefix="/api/v1", tags=["manuscript-intake"])
TRANSITIONS = {
    "awaiting_upload": {"uploaded", "failed"},
    "uploaded": {"scanning", "failed"},
    "scanning": {"extracting", "failed"},
    "extracting": {"awaiting_assessment", "failed"},
    "awaiting_assessment": {"assessing", "failed"},
    "assessing": {"completed", "failed"},
}


class IntakeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=255)
    mime_type: Literal[
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
    ]
    size_bytes: int = Field(gt=0, le=20 * 1024 * 1024)


class IntakeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    status: Literal[
        "uploaded",
        "scanning",
        "extracting",
        "awaiting_assessment",
        "assessing",
        "completed",
        "failed",
    ]
    error_code: (
        Literal[
            "upload_failed",
            "unsafe_file",
            "unsupported_content",
            "extraction_failed",
            "assessment_failed",
        ]
        | None
    ) = None
    storage_key: str | None = Field(default=None, max_length=500)
    word_count: int | None = Field(default=None, ge=0, le=10000000)
    page_count: int | None = Field(default=None, ge=0, le=100000)
    assessment_summary: str | None = Field(default=None, max_length=2000)


@router.post("/customer/threads/{thread_id}/intake", status_code=201)
async def create_intake(thread_id: UUID, payload: IntakeInput, request: Request) -> dict[str, Any]:
    sessions = factory(request)
    async with ThreadRepository(sessions).serialize(thread_id), sessions() as session:
        await owned_thread(request, session, thread_id)
        record = WorkflowRecord(
            thread_id=thread_id,
            kind="intake",
            status="awaiting_upload",
            payload={**payload.model_dump(), "attempt": 1},
        )
        session.add(record)
        await session.commit()
        return record.model_dump(mode="json")


@router.get("/customer/threads/{thread_id}/intake")
async def list_intake(thread_id: UUID, request: Request) -> list[dict[str, Any]]:
    async with factory(request)() as session:
        await owned_thread(request, session, thread_id)
        rows = (
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
        return [row.model_dump(mode="json") for row in rows]


@router.post("/customer/threads/{thread_id}/intake/{intake_id}/retry")
async def retry_intake(thread_id: UUID, intake_id: UUID, request: Request) -> dict[str, Any]:
    sessions = factory(request)
    async with ThreadRepository(sessions).serialize(thread_id), sessions() as session:
        await owned_thread(request, session, thread_id)
        record = await session.get(WorkflowRecord, intake_id, with_for_update=True)
        if record is None or record.thread_id != thread_id or record.kind != "intake":
            raise HTTPException(404, "intake_not_found")
        if record.status != "failed" or record.payload.get("error_code") == "unsafe_file":
            raise HTTPException(409, "intake_cannot_be_retried")
        record.status = "awaiting_upload"
        record.version += 1
        record.payload = {
            **record.payload,
            "error_code": None,
            "attempt": record.payload.get("attempt", 1) + 1,
        }
        record.updated_at = utc_now()
        session.add(record)
        await session.commit()
        return record.model_dump(mode="json")


@router.patch("/staff/intake/{intake_id}")
async def update_intake(intake_id: UUID, payload: IntakeUpdate, request: Request) -> dict[str, Any]:
    staff(request, "intake:manage")
    sessions = factory(request)
    async with sessions() as lookup:
        initial = await lookup.get(WorkflowRecord, intake_id)
        if initial is None or initial.kind != "intake" or initial.thread_id is None:
            raise HTTPException(404, "intake_not_found")
        thread_id = initial.thread_id
    async with ThreadRepository(sessions).serialize(thread_id), sessions() as session:
        # An intake service token cannot bypass a pending deletion.
        from bookcraft.components.storage.models import ThreadRecord

        thread = await session.get(ThreadRecord, thread_id)
        record = await session.get(WorkflowRecord, intake_id, with_for_update=True)
        if thread is None or thread.deleted_at is not None or record is None:
            raise HTTPException(410, "thread_deleted")
        if record.version != payload.expected_version:
            raise HTTPException(409, "stale_intake_callback")
        if payload.status not in TRANSITIONS.get(record.status, set()):
            raise HTTPException(409, "invalid_intake_transition")
        if payload.status == "failed" and payload.error_code is None:
            raise HTTPException(422, "failure_reason_required")
        record.payload = {
            **record.payload,
            **payload.model_dump(exclude={"status", "expected_version"}, exclude_none=True),
        }
        record.status, record.updated_at = payload.status, utc_now()
        record.version += 1
        session.add(record)
        await session.commit()
        return record.model_dump(mode="json")
