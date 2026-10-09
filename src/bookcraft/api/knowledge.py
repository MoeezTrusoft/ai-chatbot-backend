"""Immutable knowledge releases, review, preview, publication and rollback."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import col, select

from bookcraft.api.management_access import factory, staff
from bookcraft.components.rag.index import RagIndexManager
from bookcraft.components.rag.schemas import RagChunk
from bookcraft.components.rag.verifier import RagVerifier
from bookcraft.components.storage.management_models import WorkflowRecord
from bookcraft.components.storage.models import utc_now
from bookcraft.components.storage.thread_repository import ThreadRepository

router = APIRouter(prefix="/api/v1/staff/knowledge", tags=["knowledge-management"])
PUBLICATION_LOCK = UUID("de16f2ae-0d7b-48aa-aa83-5eb0121b6a52")


class ReleaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=255)
    chunks: list[RagChunk] = Field(min_length=1, max_length=10000)
    expires_at: datetime


class PreviewInput(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    generate_answer: bool = False


def validate_release(payload: ReleaseInput) -> None:
    if payload.expires_at.tzinfo is None or payload.expires_at <= datetime.now(UTC):
        raise HTTPException(422, "future_timezone_aware_expiry_required")
    seen = set()
    for chunk in payload.chunks:
        metadata = chunk.metadata
        if not all(
            value.strip()
            for value in (
                chunk.chunk_id,
                metadata.source_id,
                metadata.title,
                metadata.section,
                metadata.source_filename,
                metadata.content_version,
                chunk.content,
            )
        ):
            raise HTTPException(422, "required_source_metadata_missing")
        if len(chunk.content) > 20000 or chunk.chunk_id in seen:
            raise HTTPException(422, "duplicate_or_oversized_chunk")
        seen.add(chunk.chunk_id)
        if RagVerifier._first_forbidden(chunk.content):
            raise HTTPException(422, "knowledge_contains_gated_prices_or_timelines")
        if not chunk.allowed_for_response:
            raise HTTPException(422, "unapproved_chunk_in_release")
        if chunk.checksum != hashlib.sha256(chunk.content.encode()).hexdigest():
            raise HTTPException(422, "chunk_checksum_mismatch")


@router.get("")
async def releases(
    request: Request, limit: int = Query(default=30, ge=1, le=100)
) -> list[dict[str, Any]]:
    staff(request, "knowledge:manage")
    async with factory(request)() as session:
        rows = (
            (
                await session.execute(
                    select(WorkflowRecord)
                    .where(WorkflowRecord.kind == "knowledge")
                    .order_by(col(WorkflowRecord.created_at).desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": str(row.id),
                "status": row.status,
                "title": row.payload["title"],
                "expires_at": row.payload["expires_at"],
                "expired": datetime.fromisoformat(row.payload["expires_at"]) <= datetime.now(UTC),
                "created_by": row.payload.get("created_by"),
                "approved_by": row.payload.get("approved_by"),
            }
            for row in rows
        ]


@router.post("")
async def create_release(payload: ReleaseInput, request: Request) -> dict[str, Any]:
    principal = staff(request, "knowledge:manage")
    validate_release(payload)
    data = payload.model_dump(mode="json")
    # Embeddings must come from the configured model, never from the editor.
    for chunk in data["chunks"]:
        chunk["content_vector"] = []
    record = WorkflowRecord(
        kind="knowledge", status="draft", payload={**data, "created_by": principal.subject}
    )
    async with factory(request)() as session:
        session.add(record)
        await session.commit()
    return {"id": str(record.id), "status": record.status}


@router.get("/{release_id}")
async def release(release_id: UUID, request: Request) -> dict[str, Any]:
    staff(request, "knowledge:manage")
    async with factory(request)() as session:
        record = await session.get(WorkflowRecord, release_id)
        if record is None or record.kind != "knowledge":
            raise HTTPException(404, "release_not_found")
        return record.model_dump(mode="json")


@router.post("/{release_id}/preview")
async def preview(release_id: UUID, payload: PreviewInput, request: Request) -> dict[str, Any]:
    data = await release(release_id, request)
    candidate = ReleaseInput.model_validate(
        {key: data["payload"][key] for key in ("title", "chunks", "expires_at")}
    )
    validate_release(candidate)
    terms = set(re.findall(r"\w+", payload.query.casefold()))
    ranked = sorted(
        candidate.chunks,
        key=lambda c: len(terms & set(re.findall(r"\w+", c.content.casefold()))),
        reverse=True,
    )
    matches = [
        chunk for chunk in ranked[:5] if terms & set(re.findall(r"\w+", chunk.content.casefold()))
    ]
    result: dict[str, Any] = {
        "mode": "source_evidence_preview",
        "answer_preview": "\n\n".join(chunk.content[:600] for chunk in matches)
        or "No supporting source was found; the chatbot should request clarification.",
        "sources": [
            {
                "source_id": c.metadata.source_id,
                "title": c.metadata.title,
                "section": c.metadata.section,
                "checksum": c.checksum,
            }
            for c in matches
        ],
    }
    if payload.generate_answer:
        from bookcraft.services.knowledge_preview import generate_preview

        service = getattr(request.app.state, "chat_service", None)
        generator = getattr(service, "response_generator", None)
        if generator is None or getattr(generator, "adapter", None) is None:
            raise HTTPException(503, "configured_response_provider_required")
        if not matches:
            return result
        result.update(await generate_preview(generator, payload.query, matches))
        result["mode"] = "generated_answer_preview"
    return result


@router.post("/{release_id}/approve")
async def approve(release_id: UUID, request: Request) -> dict[str, str]:
    principal = staff(request, "knowledge:manage")
    async with factory(request)() as session:
        record = await session.get(WorkflowRecord, release_id, with_for_update=True)
        if record is None or record.kind != "knowledge":
            raise HTTPException(404, "release_not_found")
        if record.status != "draft":
            raise HTTPException(409, "release_is_immutable_after_review")
        validate_release(
            ReleaseInput.model_validate(
                {key: record.payload[key] for key in ("title", "chunks", "expires_at")}
            )
        )
        record.status = "approved"
        record.payload = {
            **record.payload,
            "approved_by": principal.subject,
            "approved_at": utc_now().isoformat(),
        }
        session.add(record)
        await session.commit()
    return {"status": "approved"}


@router.post("/{release_id}/publish")
async def publish(release_id: UUID, request: Request) -> dict[str, str]:
    staff(request, "knowledge:manage")
    settings = request.app.state.settings
    client = getattr(request.app.state, "elasticsearch_client", None)
    if client is None:
        raise HTTPException(503, "search_service_required")
    sessions = factory(request)
    async with ThreadRepository(sessions).serialize(PUBLICATION_LOCK), sessions() as session:
        record = await session.get(WorkflowRecord, release_id)
        if record is None or record.kind != "knowledge":
            raise HTTPException(404, "release_not_found")
        if record.status not in {"approved", "published"}:
            raise HTTPException(409, "review_required")
        candidate = ReleaseInput.model_validate(
            {key: record.payload[key] for key in ("title", "chunks", "expires_at")}
        )
        validate_release(candidate)
        index_name = record.payload.get("index_name") or (
            settings.rag_index_alias + "-release-" + release_id.hex + "-" + uuid4().hex[:12]
        )
        manager = RagIndexManager(
            client, index_name, settings.rag_index_alias, settings.embedding_dimensions
        )
        # A published immutable index can be reactivated without rewriting live content.
        if not record.payload.get("index_name"):
            embedder = request.app.state.chat_service.preprocessor.embedding_client
            for chunk in candidate.chunks:
                chunk.content_vector = await embedder.embed(chunk.content, "en")
                if not any(chunk.content_vector):
                    raise HTTPException(503, "valid_embeddings_required")
                chunk.expires_at = candidate.expires_at
            await manager.create_index()
            await manager.index_chunks(candidate.chunks)
            # Persist the fully built immutable index before switching the alias.
            # A crash after alias activation can then retry without rewriting it.
            record.payload = {**record.payload, "index_name": index_name}
            session.add(record)
            await session.commit()
        await manager.promote_alias()
        record.status, record.updated_at = "published", utc_now()
        record.payload = {**record.payload, "index_name": index_name}
        session.add(record)
        await session.commit()
    return {"status": "published", "index": index_name}


@router.post("/{release_id}/rollback")
async def rollback(release_id: UUID, request: Request) -> dict[str, str]:
    # Reactivate only a previously published immutable, unexpired release.
    data = await release(release_id, request)
    if data["status"] != "published":
        raise HTTPException(409, "rollback_requires_published_release")
    return await publish(release_id, request)
