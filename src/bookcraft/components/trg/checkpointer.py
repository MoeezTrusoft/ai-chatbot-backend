"""Conversation checkpointer — writes permanent state snapshots at milestone events.

Checkpoints survive indefinitely (no TTL). They give the bot permanent memory of
key events even after multiple Redis cycles. The milestone predicate determines
which events trigger a checkpoint; each milestone is only written once per thread.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bookcraft.components.storage.models import ConversationCheckpoint
from bookcraft.domain.state import ThreadState

logger = structlog.get_logger(__name__)

# Each entry: milestone_key → predicate that returns True when the milestone is reached.
_MILESTONES: dict[str, Callable[[ThreadState], bool]] = {
    "lead_created": lambda s: s.lead_created,
    "service_confirmed": lambda s: bool(s.project.services_discussed),
    "consultation_scheduled": lambda s: bool(
        s.sales_actions.consultation.confirmed_appointment_id
    ),
}


class ConversationCheckpointer:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def maybe_checkpoint(
        self,
        thread_id: UUID,
        state: ThreadState,
        turn_index: int,
    ) -> list[str]:
        """Write a checkpoint for any newly reached milestone. Returns written milestone keys."""
        written: list[str] = []
        for milestone, predicate in _MILESTONES.items():
            try:
                if predicate(state) and not await self._already_checkpointed(thread_id, milestone):
                    await self._write_checkpoint(thread_id, milestone, state, turn_index)
                    written.append(milestone)
            except Exception as exc:
                logger.warning(
                    "checkpoint_failed",
                    thread_id=str(thread_id),
                    milestone=milestone,
                    exception_class=exc.__class__.__name__,
                )
        return written

    async def force_checkpoint(
        self,
        thread_id: UUID,
        state: ThreadState,
        milestone: str,
        turn_index: int = 0,
    ) -> None:
        """Write a checkpoint unconditionally (e.g. on CSR handover return)."""
        try:
            await self._write_checkpoint(thread_id, milestone, state, turn_index)
        except Exception as exc:
            logger.warning(
                "force_checkpoint_failed",
                thread_id=str(thread_id),
                milestone=milestone,
                exception_class=exc.__class__.__name__,
            )

    async def _already_checkpointed(self, thread_id: UUID, milestone: str) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ConversationCheckpoint.id).where(
                    ConversationCheckpoint.thread_id == thread_id,
                    ConversationCheckpoint.milestone == milestone,
                )
            )
            return result.scalar_one_or_none() is not None

    async def _write_checkpoint(
        self,
        thread_id: UUID,
        milestone: str,
        state: ThreadState,
        turn_index: int,
    ) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                session.add(
                    ConversationCheckpoint(
                        thread_id=thread_id,
                        milestone=milestone,
                        state_snapshot=state.model_dump(mode="json"),
                        turn_index=turn_index,
                    )
                )
        logger.info(
            "conversation_checkpoint_written",
            thread_id=str(thread_id),
            milestone=milestone,
            turn_index=turn_index,
        )
