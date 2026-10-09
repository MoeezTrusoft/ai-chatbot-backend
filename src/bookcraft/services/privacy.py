"""Retryable deletion and retention. A thread is inaccessible before cleanup begins."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col, select

from bookcraft.components.analysis import LiveTraceStore
from bookcraft.components.storage import models
from bookcraft.components.storage.management_models import FeedbackRecord, WorkflowRecord
from bookcraft.components.storage.thread_repository import ThreadRepository
from bookcraft.domain.state import ThreadState
from bookcraft.infra.cache import CacheKeyBuilder
from bookcraft.infra.config import Settings


async def erase_thread(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    trace_store: LiveTraceStore,
    cache: Any,
    client: httpx.AsyncClient,
    thread_id: UUID,
) -> None:
    async with ThreadRepository(sessions).serialize(thread_id), sessions() as session:
        thread = await session.get(models.ThreadRecord, thread_id)
        if thread is None or thread.deleted_at is None:
            return
        if settings.csr_node_api_url:
            response = await client.delete(
                settings.csr_node_api_url.rstrip("/") + f"/api/threads/{thread_id}/data",
                headers={"Authorization": f"Bearer {settings.csr_node_api_token}"},
            )
            response.raise_for_status()
        intakes = (
            (
                await session.execute(
                    select(WorkflowRecord).where(
                        WorkflowRecord.thread_id == thread_id, WorkflowRecord.kind == "intake"
                    )
                )
            )
            .scalars()
            .all()
        )
        # The upload service owns original bytes. Require its erasure acknowledgment
        # before removing references or reporting the deletion as complete.
        has_external_files = bool(thread.state.get("attachments_received")) or any(
            row.payload.get("storage_key") for row in intakes
        )
        if has_external_files:
            if not settings.attachment_cleanup_url or not settings.attachment_cleanup_token:
                raise RuntimeError("attachment_cleanup_not_configured")
            cleanup = await client.delete(
                settings.attachment_cleanup_url.rstrip("/") + f"/api/threads/{thread_id}/data",
                headers={"Authorization": f"Bearer {settings.attachment_cleanup_token}"},
            )
            cleanup.raise_for_status()
            if cleanup.json().get("status") != "deleted":
                raise RuntimeError("attachment_cleanup_not_acknowledged")
        documents = (
            (
                await session.execute(
                    select(models.SalesDocumentRequestRecord).where(
                        models.SalesDocumentRequestRecord.thread_id == thread_id
                    )
                )
            )
            .scalars()
            .all()
        )
        document_root = await asyncio.to_thread(Path(settings.document_output_dir).resolve)
        for document in documents:
            for value in (document.html_path, document.pdf_path):
                if value:
                    path = await asyncio.to_thread(Path(value).resolve)
                    if not path.is_relative_to(document_root):
                        raise ValueError("document_path_outside_configured_root")
                    await asyncio.to_thread(path.unlink, missing_ok=True)
        tools = (
            (
                await session.execute(
                    select(models.ToolInvocationLog).where(
                        models.ToolInvocationLog.thread_id == thread_id
                    )
                )
            )
            .scalars()
            .all()
        )
        if cache is not None:
            keys = CacheKeyBuilder(settings.app_env)
            for key in (
                keys.thread_state(str(thread_id)),
                keys.thread_graph(str(thread_id)),
                *(keys.idempotency(row.idempotency_key) for row in tools),
            ):
                await cache.delete(key)
        await asyncio.to_thread(trace_store.delete_thread, str(thread_id))
        appointment_ids = select(models.SalesConsultationRecord.id).where(
            models.SalesConsultationRecord.thread_id == thread_id
        )
        await session.execute(
            delete(models.DeliveryOutboxRecord).where(
                col(models.DeliveryOutboxRecord.aggregate_id).in_(appointment_ids)
            )
        )
        # Delete dependent rows before leads referenced by those rows.
        for model in (
            models.ThreadEvent,
            models.IntentClassificationLog,
            models.ToolInvocationLog,
            models.DeferredToolInvocation,
            models.GraphEdgeRecord,
            models.GraphNodeRecord,
            models.TRGFactRecord,
            models.ConversationCheckpoint,
            models.SalesActionRecord,
            models.SalesConsultationRecord,
            models.SalesDocumentRequestRecord,
            models.SalesPricingQuoteRecord,
            models.PortfolioSampleViewRecord,
            models.SalesLeadRecord,
            FeedbackRecord,
        ):
            await session.execute(delete(model).where(col(model.thread_id) == thread_id))
        await session.execute(
            delete(WorkflowRecord).where(
                col(WorkflowRecord.thread_id) == thread_id, col(WorkflowRecord.kind) != "deletion"
            )
        )
        thread.state = ThreadState().model_dump(mode="json")
        thread.customer_id = None
        thread.version += 1
        session.add(thread)
        jobs = (
            (
                await session.execute(
                    select(WorkflowRecord).where(
                        WorkflowRecord.thread_id == thread_id, WorkflowRecord.kind == "deletion"
                    )
                )
            )
            .scalars()
            .all()
        )
        for job in jobs:
            job.status, job.payload = "completed", {}
            session.add(job)
        await session.commit()
