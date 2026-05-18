from __future__ import annotations

from uuid import uuid4

import pytest

from bookcraft.components.consultations import (
    ConsultationActionRequest,
    ConsultationActionService,
    InMemoryConsultationRepository,
)


@pytest.mark.asyncio
async def test_consultation_schedules_with_first_priority_csr() -> None:
    repository = InMemoryConsultationRepository()
    service = ConsultationActionService(repository=repository)

    result = await service.schedule(
        ConsultationActionRequest(
            customer_id=uuid4(),
            thread_id=uuid4(),
            name="Maya Author",
            email="maya@example.com",
            phone="+1 555 123 4567",
            services=["editing_proofreading"],
            requested_time_text="tomorrow at 4pm",
            business_timezone="America/Chicago",
        )
    )

    assert result.csr_name == "Jerry Miller"
    assert result.status == "scheduled"
    assert result.houston_display_time


@pytest.mark.asyncio
async def test_consultation_conflict_uses_next_priority_csr() -> None:
    repository = InMemoryConsultationRepository()
    service = ConsultationActionService(repository=repository)

    first = await service.schedule(
        ConsultationActionRequest(
            customer_id=uuid4(),
            thread_id=uuid4(),
            name="Maya Author",
            email="maya@example.com",
            services=["editing_proofreading"],
            requested_time_text="tomorrow at 4pm",
            business_timezone="America/Chicago",
        )
    )

    second = await service.schedule(
        ConsultationActionRequest(
            customer_id=uuid4(),
            thread_id=uuid4(),
            name="Nora Writer",
            email="nora@example.com",
            services=["interior_formatting"],
            requested_time_text="tomorrow at 4pm",
            business_timezone="America/Chicago",
        )
    )

    assert first.csr_name == "Jerry Miller"
    assert second.csr_name == "Robert Williams"


@pytest.mark.asyncio
async def test_consultation_requires_contact() -> None:
    repository = InMemoryConsultationRepository()
    service = ConsultationActionService(repository=repository)

    with pytest.raises(ValueError, match="consultation_requires_email_or_phone"):
        await service.schedule(
            ConsultationActionRequest(
                customer_id=uuid4(),
                thread_id=uuid4(),
                name="Maya Author",
                requested_time_text="tomorrow at 4pm",
            )
        )


def test_consultation_parser_respects_absolute_houston_date() -> None:
    from zoneinfo import ZoneInfo

    from bookcraft.components.consultations.service import _parse_requested_start

    business_tz = ZoneInfo("America/Chicago")

    parsed = _parse_requested_start(
        text="Please schedule a consultation on May 20, 2026 at 11:00 AM Houston time.",
        customer_tz=business_tz,
        business_tz=business_tz,
        business_start_hour=10,
        business_end_hour=19,
    )

    assert parsed.year == 2026
    assert parsed.month == 5
    assert parsed.day == 20
    assert parsed.hour == 11
    assert parsed.minute == 0
