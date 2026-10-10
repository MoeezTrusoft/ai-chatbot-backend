"""Shared authorization for persisted customer and staff workflows."""

from typing import cast
from uuid import UUID

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bookcraft.api.auth import AuthPrincipal, require_http_auth, require_scope
from bookcraft.components.storage.models import ThreadRecord


def factory(request: Request) -> async_sessionmaker[AsyncSession]:
    value = getattr(request.app.state, "session_factory", None)
    if value is None:
        raise HTTPException(503, "persistent_storage_required")
    return cast(async_sessionmaker[AsyncSession], value)


def staff(request: Request, scope: str = "chat:manage") -> AuthPrincipal:
    return require_scope(request, request.app.state.settings, scope)


async def owned_thread(
    request: Request, session: AsyncSession, thread_id: UUID, *, allow_deleted: bool = False
) -> ThreadRecord:
    principal = require_http_auth(request, request.app.state.settings)
    record = await session.get(ThreadRecord, thread_id)
    if record is None or (record.deleted_at is not None and not allow_deleted):
        raise HTTPException(404, "thread_not_found")
    if request.app.state.settings.api_auth_mode == "off" or "chat:manage" in principal.scopes:
        return record
    if request.app.state.settings.sales_integration_mode == "erp_mcp" and (
        principal.customer_id is None or record.customer_id != principal.customer_id
    ):
        raise HTTPException(403, "thread_not_accessible")
    if record.owner_subject == principal.subject and (
        principal.customer_id is None or record.customer_id == principal.customer_id
    ):
        return record
    if (
        record.owner_subject is None
        and principal.customer_id is not None
        and record.customer_id == principal.customer_id
    ):
        return record
    raise HTTPException(403, "thread_not_accessible")
