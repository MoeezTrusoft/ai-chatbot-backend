from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from bookcraft.components.leads.schemas import (
    CreateOrUpdateLeadRequest,
    LeadOperationResult,
    LeadView,
)
from bookcraft.components.storage.models import SalesLeadRecord


class LeadRepositoryProtocol(Protocol):
    async def find_by_contact(
        self,
        *,
        email: str | None,
        phone: str | None,
    ) -> SalesLeadRecord | None: ...

    async def create(self, request: CreateOrUpdateLeadRequest) -> SalesLeadRecord: ...

    async def update(
        self,
        record: SalesLeadRecord,
        request: CreateOrUpdateLeadRequest,
        *,
        services: list[str],
    ) -> SalesLeadRecord: ...

    def to_view(self, record: SalesLeadRecord) -> LeadView: ...


@dataclass(slots=True)
class LeadService:
    repository: LeadRepositoryProtocol

    async def create_or_update(
        self,
        request: CreateOrUpdateLeadRequest,
    ) -> LeadOperationResult:
        if not request.email and not request.phone:
            raise ValueError("Lead requires at least email or phone.")

        existing = await self.repository.find_by_contact(
            email=request.email,
            phone=request.phone,
        )

        if existing is None:
            created = await self.repository.create(request)
            return LeadOperationResult(
                lead=self.repository.to_view(created),
                created=True,
                updated_fields=[],
            )

        merged_services = _merge_services(existing.services, request.services)
        updated_fields = _changed_fields(existing, request, merged_services)
        updated = await self.repository.update(
            existing,
            request,
            services=merged_services,
        )
        return LeadOperationResult(
            lead=self.repository.to_view(updated),
            created=False,
            updated_fields=updated_fields,
        )


def _merge_services(existing: list[str], incoming: list[str]) -> list[str]:
    merged: list[str] = []
    for service in [*existing, *incoming]:
        if service and service not in merged:
            merged.append(service)
    return merged


def _changed_fields(
    existing: SalesLeadRecord,
    request: CreateOrUpdateLeadRequest,
    merged_services: list[str],
) -> list[str]:
    changed: list[str] = []

    for field_name in [
        "customer_id",
        "thread_id",
        "name",
        "email",
        "phone",
        "preferred_contact_method",
        "genre",
        "word_count",
        "page_count",
        "manuscript_status",
        "deadline",
        "notes",
    ]:
        value = getattr(request, field_name)
        if value is not None and getattr(existing, field_name) != value:
            changed.append(field_name)

    if merged_services != existing.services:
        changed.append("services")

    if request.metadata:
        changed.append("metadata")

    return changed
