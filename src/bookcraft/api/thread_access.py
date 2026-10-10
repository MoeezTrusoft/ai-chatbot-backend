"""Bind requests to authenticated identities before running any conversation work."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from fastapi import HTTPException

from bookcraft.api.auth import AuthPrincipal
from bookcraft.components.storage.thread_repository import ThreadAccessDeniedError
from bookcraft.infra.config import Settings

if TYPE_CHECKING:
    from bookcraft.services.chat import ChatService


async def authorize_chat(
    principal: AuthPrincipal,
    settings: Settings,
    service: ChatService,
    thread_id: UUID | None,
    customer_id: UUID | None,
) -> tuple[UUID, UUID | None]:
    resolved_id = thread_id or uuid4()
    if settings.api_auth_mode == "off" or "chat:manage" in principal.scopes:
        return resolved_id, customer_id or principal.customer_id
    if settings.sales_integration_mode == "erp_mcp" and principal.customer_id is None:
        raise HTTPException(403, "sales_customer_identity_required")
    if customer_id is not None and customer_id != principal.customer_id:
        raise HTTPException(status_code=403, detail={"error": "customer_identity_mismatch"})
    customer_id = principal.customer_id
    if service.thread_repository is not None:
        try:
            async with asyncio.timeout(settings.chat_turn_timeout_seconds):
                await service.thread_repository.authorize(
                    thread_id=resolved_id, subject=principal.subject, customer_id=customer_id
                )
        except ThreadAccessDeniedError as exc:
            raise HTTPException(status_code=403, detail={"error": "thread_not_accessible"}) from exc
    else:
        owner = service.thread_owners.get(resolved_id)
        if owner is None and resolved_id in service.threads:
            raise HTTPException(status_code=403, detail={"error": "thread_not_accessible"})
        if owner is not None and owner != principal.subject:
            raise HTTPException(status_code=403, detail={"error": "thread_not_accessible"})
        service.thread_owners[resolved_id] = principal.subject
    return resolved_id, customer_id
