"""Durable idempotency repository for SalesActionDispatcher.

Batch 4: replaces the in-process _dispatched dict so that multiple workers,
containers, and server restarts cannot double-dispatch the same action.

Contract:
- `claim(key, ...)` tries to INSERT a pending record. Returns True if claimed
  (caller should dispatch), False if the key already exists (skip dispatch).
  A key whose earlier attempt FAILED, or whose claim has EXPIRED, is claimed again:
  a failed booking must be retryable, and a completed one must not block the same
  request for ever.
- `mark_completed(key, ...)` updates status to completed + stores result_summary.
- `mark_failed(key, ...)` updates status to failed + stores error_code.
- `get_status(key)` returns the current status string or None if unknown.

All methods are async; in-process fallback is provided for test mode.

Engines compute. Claude writes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col, select


def _naive_utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class _InProcessFallback:
    """Thread-unsafe in-process fallback for test/dev mode.

    Safe for single-worker deployments only.  Replaced by the DB-backed
    implementation in staging/production (when session_factory is provided).
    """

    def __init__(self) -> None:
        self._keys: dict[str, str] = {}  # key → status
        self._expires: dict[str, datetime | None] = {}

    async def claim(
        self,
        *,
        idempotency_key: str,
        thread_id: UUID,
        action_type: str,
        slots_hash: str,
        expires_at: datetime | None,
    ) -> bool:
        if idempotency_key in self._keys and not self._reclaimable(idempotency_key):
            return False
        self._keys[idempotency_key] = "processing"
        self._expires[idempotency_key] = expires_at
        return True

    def _reclaimable(self, idempotency_key: str) -> bool:
        """A failed attempt, or a claim past its expiry, does not block a new one."""
        if self._keys.get(idempotency_key) == "failed":
            return True
        expires_at = self._expires.get(idempotency_key)
        return expires_at is not None and expires_at < _naive_utc_now()

    async def mark_completed(self, *, idempotency_key: str, result_summary: str | None) -> None:
        self._keys[idempotency_key] = "completed"

    async def mark_failed(self, *, idempotency_key: str, error_code: str) -> None:
        self._keys[idempotency_key] = "failed"

    async def get_status(self, *, idempotency_key: str) -> str | None:
        return self._keys.get(idempotency_key)


@dataclass(slots=True)
class ActionIdempotencyRepository:
    """DB-backed idempotency repository.

    When `session_factory` is None (test/dev mode), falls back to the in-process
    implementation which is NOT safe for multi-worker deployments.
    """

    session_factory: async_sessionmaker[AsyncSession] | None = None
    _fallback: _InProcessFallback = field(
        default_factory=_InProcessFallback, init=False, repr=False
    )

    async def claim(
        self,
        *,
        idempotency_key: str,
        thread_id: UUID,
        action_type: str,
        slots_hash: str,
        expires_at: datetime | None = None,
    ) -> bool:
        """Try to claim the idempotency key.

        Returns True if the key was successfully claimed (caller should dispatch).
        Returns False if the key already exists (skip dispatch — already in flight).
        """
        if self.session_factory is None:
            return await self._fallback.claim(
                idempotency_key=idempotency_key,
                thread_id=thread_id,
                action_type=action_type,
                slots_hash=slots_hash,
                expires_at=expires_at,
            )

        from bookcraft.components.storage.models import SalesActionRecord, utc_now

        record = SalesActionRecord(
            thread_id=thread_id,
            action_type=action_type,
            idempotency_key=idempotency_key,
            status="processing",
            slots_hash=slots_hash,
            created_at=utc_now(),
            expires_at=expires_at,
        )
        try:
            async with self.session_factory() as session:
                session.add(record)
                await session.commit()
            return True
        except IntegrityError:
            # The key exists. It still blocks this dispatch unless the earlier attempt
            # failed, or its claim has expired: a failed booking must be retryable and
            # an old completed one must not block the same request for ever.
            return await self._reclaim(
                idempotency_key=idempotency_key, slots_hash=slots_hash, expires_at=expires_at
            )

    async def _reclaim(
        self, *, idempotency_key: str, slots_hash: str, expires_at: datetime | None
    ) -> bool:
        from bookcraft.components.storage.models import SalesActionRecord, utc_now

        assert self.session_factory is not None  # noqa: S101 - narrowed by the caller
        now = utc_now()
        statement = (
            update(SalesActionRecord)
            .where(
                col(SalesActionRecord.idempotency_key) == idempotency_key,
                or_(
                    col(SalesActionRecord.status) == "failed",
                    and_(
                        col(SalesActionRecord.expires_at).is_not(None),
                        col(SalesActionRecord.expires_at) < now,
                    ),
                ),
            )
            .values(
                status="processing",
                slots_hash=slots_hash,
                created_at=now,
                expires_at=expires_at,
                completed_at=None,
                error_code=None,
                result_summary=None,
            )
            .returning(col(SalesActionRecord.id))
        )
        async with self.session_factory() as session:
            result = await session.execute(statement)
            reclaimed = result.scalar_one_or_none() is not None
            await session.commit()
        return reclaimed

    async def mark_completed(
        self,
        *,
        idempotency_key: str,
        result_summary: str | None = None,
    ) -> None:
        if self.session_factory is None:
            await self._fallback.mark_completed(
                idempotency_key=idempotency_key,
                result_summary=result_summary,
            )
            return

        from bookcraft.components.storage.models import SalesActionRecord, utc_now

        async with self.session_factory() as session:
            result = await session.execute(
                select(SalesActionRecord).where(
                    SalesActionRecord.idempotency_key == idempotency_key
                )
            )
            record = result.scalar_one_or_none()
            if record is not None:
                record.status = "completed"
                record.completed_at = utc_now()
                record.result_summary = (result_summary or "")[:512]
                await session.commit()

    async def mark_failed(
        self,
        *,
        idempotency_key: str,
        error_code: str,
    ) -> None:
        if self.session_factory is None:
            await self._fallback.mark_failed(
                idempotency_key=idempotency_key,
                error_code=error_code,
            )
            return

        from bookcraft.components.storage.models import SalesActionRecord, utc_now

        async with self.session_factory() as session:
            result = await session.execute(
                select(SalesActionRecord).where(
                    SalesActionRecord.idempotency_key == idempotency_key
                )
            )
            record = result.scalar_one_or_none()
            if record is not None:
                record.status = "failed"
                record.completed_at = utc_now()
                record.error_code = error_code[:64]
                await session.commit()

    async def get_status(self, *, idempotency_key: str) -> str | None:
        if self.session_factory is None:
            return await self._fallback.get_status(idempotency_key=idempotency_key)

        from bookcraft.components.storage.models import SalesActionRecord

        async with self.session_factory() as session:
            result = await session.execute(
                select(SalesActionRecord.status).where(
                    SalesActionRecord.idempotency_key == idempotency_key
                )
            )
            row = result.scalar_one_or_none()
            return str(row) if row is not None else None


def make_slots_hash(slots: dict[str, Any]) -> str:
    """Return a deterministic SHA-256 hash of the action slots for audit purposes."""
    stable = {k: v for k, v in sorted(slots.items()) if k not in ("requested_time_text",)}
    raw = json.dumps(stable, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]
