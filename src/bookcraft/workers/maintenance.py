"""Bounded maintenance batches for retention and retryable privacy cleanup."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col, select

from bookcraft.components.analysis import LiveTraceStore
from bookcraft.components.storage.management_models import WorkflowRecord
from bookcraft.components.storage.models import ThreadRecord, utc_now
from bookcraft.components.storage.thread_repository import ThreadRepository
from bookcraft.infra.config import Settings
from bookcraft.services.privacy import erase_thread


async def maintenance_once(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    traces: LiveTraceStore,
    cache: Any,
    client: httpx.AsyncClient,
) -> None:
    cutoff = utc_now() - timedelta(days=settings.conversation_retention_days)
    async with sessions() as session:
        ids = (
            (
                await session.execute(
                    select(ThreadRecord.id)
                    .where(col(ThreadRecord.deleted_at).is_(None), ThreadRecord.updated_at < cutoff)
                    .limit(50)
                )
            )
            .scalars()
            .all()
        )
    for tid in ids:
        async with ThreadRepository(sessions).serialize(tid), sessions() as session:
            thread = await session.get(ThreadRecord, tid)
            if thread is not None and thread.deleted_at is None and thread.updated_at < cutoff:
                thread.deleted_at, thread.deletion_reason = utc_now(), "retention_expired"
                session.add(thread)
                session.add(WorkflowRecord(thread_id=tid, kind="deletion", status="queued"))
                await session.commit()
    async with sessions() as session:
        jobs = (
            (
                await session.execute(
                    select(WorkflowRecord)
                    .where(WorkflowRecord.kind == "deletion", WorkflowRecord.status != "completed")
                    .limit(50)
                )
            )
            .scalars()
            .all()
        )
    for job in jobs:
        if job.thread_id is None:
            continue
        try:
            await erase_thread(sessions, settings, traces, cache, client, job.thread_id)
        except Exception as exc:
            structlog.get_logger(__name__).warning(
                "deletion_retry_pending", error=type(exc).__name__
            )
    await asyncio.to_thread(traces.prune)


async def run_maintenance(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    traces: LiveTraceStore,
    cache: Any,
) -> None:
    async with httpx.AsyncClient(timeout=15) as client:
        while True:
            try:
                await maintenance_once(sessions, settings, traces, cache, client)
            except Exception as exc:
                structlog.get_logger(__name__).warning(
                    "maintenance_retry_pending", error=type(exc).__name__
                )
            await asyncio.sleep(60)
