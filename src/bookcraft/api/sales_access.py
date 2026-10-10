"""Shared locking and authorization for the Sales-backed customer routes."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast
from uuid import UUID

from fastapi import HTTPException, Request

from bookcraft.api.management_access import factory, owned_thread
from bookcraft.components.storage.thread_repository import ThreadRepository
from bookcraft.services.sales import SalesService


def sales_enabled(request: Request) -> bool:
    return bool(request.app.state.settings.sales_integration_mode == "erp_mcp")


def sales_service(request: Request) -> SalesService:
    service = getattr(request.app.state, "sales_service", None)
    if service is None:
        raise HTTPException(503, "sales_integration_unavailable")
    return cast(SalesService, service)


@asynccontextmanager
async def sales_access(request: Request, thread_id: UUID) -> AsyncIterator[SalesService]:
    sessions = factory(request)
    async with ThreadRepository(sessions).serialize(thread_id), sessions() as session:
        await owned_thread(request, session, thread_id)
        yield sales_service(request)
