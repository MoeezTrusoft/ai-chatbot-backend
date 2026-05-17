from __future__ import annotations

from uuid import uuid4

import pytest

from bookcraft.components.leads import CreateOrUpdateLeadRequest, LeadService
from bookcraft.components.leads.repository import InMemoryLeadRepository


@pytest.mark.asyncio
async def test_create_lead_with_email_only() -> None:
    repository = InMemoryLeadRepository()
    service = LeadService(repository=repository)

    result = await service.create_or_update(
        CreateOrUpdateLeadRequest(
            customer_id=uuid4(),
            thread_id=uuid4(),
            email="author@example.com",
            services=["editing_proofreading"],
        )
    )

    assert result.created is True
    assert result.lead.email == "author@example.com"
    assert result.lead.services == ["editing_proofreading"]


@pytest.mark.asyncio
async def test_create_lead_with_phone_only() -> None:
    repository = InMemoryLeadRepository()
    service = LeadService(repository=repository)

    result = await service.create_or_update(
        CreateOrUpdateLeadRequest(
            customer_id=uuid4(),
            thread_id=uuid4(),
            phone="+1 555 123 4567",
            services=["interior_formatting"],
        )
    )

    assert result.created is True
    assert result.lead.phone == "+1 555 123 4567"


@pytest.mark.asyncio
async def test_dedupe_by_email_and_merge_services() -> None:
    repository = InMemoryLeadRepository()
    service = LeadService(repository=repository)

    first = await service.create_or_update(
        CreateOrUpdateLeadRequest(
            customer_id=uuid4(),
            thread_id=uuid4(),
            email="author@example.com",
            services=["editing_proofreading"],
        )
    )

    second = await service.create_or_update(
        CreateOrUpdateLeadRequest(
            customer_id=uuid4(),
            thread_id=uuid4(),
            name="Maya Author",
            email="author@example.com",
            phone="+1 555 999 0000",
            services=["interior_formatting"],
        )
    )

    assert second.created is False
    assert second.lead.id == first.lead.id
    assert second.lead.name == "Maya Author"
    assert second.lead.phone == "+1 555 999 0000"
    assert second.lead.services == ["editing_proofreading", "interior_formatting"]
    assert "name" in second.updated_fields
    assert "phone" in second.updated_fields
    assert "services" in second.updated_fields


@pytest.mark.asyncio
async def test_lead_requires_email_or_phone() -> None:
    repository = InMemoryLeadRepository()
    service = LeadService(repository=repository)

    with pytest.raises(ValueError, match="Lead requires at least email or phone"):
        await service.create_or_update(
            CreateOrUpdateLeadRequest(
                customer_id=uuid4(),
                thread_id=uuid4(),
                name="No Contact",
            )
        )
