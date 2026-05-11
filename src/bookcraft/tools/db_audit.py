from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bookcraft.components.storage.models import ToolInvocationLog
from bookcraft.domain.enums import ToolInvocationStatus
from bookcraft.tools.dispatcher import AuditSink
from bookcraft.tools.schemas import ToolContext


@dataclass(slots=True)
class DbToolAuditSink(AuditSink):
    session_factory: async_sessionmaker[AsyncSession]

    async def record(
        self,
        *,
        context: ToolContext,
        tool_name: str,
        params_hash: str,
        params: dict[str, object],
        status: ToolInvocationStatus,
        result: dict[str, object] | None = None,
        error: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        async with self.session_factory() as session:
            session.add(
                ToolInvocationLog(
                    correlation_id=context.correlation_id,
                    tool_name=tool_name,
                    thread_id=context.thread_id,
                    turn_sequence=context.turn_sequence,
                    invoked_by=context.invoked_by,
                    idempotency_key=context.idempotency_key,
                    params_hash=params_hash,
                    params=_json_dict(params),
                    status=status.value,
                    result=_json_dict(result) if result is not None else None,
                    error_kind=_error_kind(error) if error else None,
                    error_detail=error,
                    duration_ms=duration_ms,
                    completed_at=datetime.now(UTC),
                )
            )
            await session.commit()


def _json_dict(value: dict[str, Any] | dict[str, object]) -> dict[str, Any]:
    return dict(value)


def _error_kind(error: str) -> str:
    first_line = error.splitlines()[0] if error else "unknown"
    return first_line[:64] or "unknown"