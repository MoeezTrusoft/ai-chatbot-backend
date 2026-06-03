from __future__ import annotations

from uuid import uuid4

import pytest

from bookcraft.components.consultations import (
    ConsultationActionRequest,
    ConsultationActionService,
    InMemoryConsultationRepository,
)

# ---------------------------------------------------------------------------
# Helper: minimal valid request (phone + timezone now required; email optional)
# ---------------------------------------------------------------------------

def _req(**overrides) -> ConsultationActionRequest:
    defaults = dict(
        customer_id=uuid4(),
        thread_id=uuid4(),
        name="Maya Author",
        phone="+1 555 123 4567",
        customer_timezone="America/Chicago",
        services=["editing_proofreading"],
        requested_time_text="tomorrow at 4pm",
        business_timezone="America/Chicago",
    )
    defaults.update(overrides)
    return ConsultationActionRequest(**defaults)


# ---------------------------------------------------------------------------
# Scheduling behaviour
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_consultation_schedules_with_first_priority_csr() -> None:
    service = ConsultationActionService(repository=InMemoryConsultationRepository())
    result = await service.schedule(_req())
    assert result.csr_name == "Jerry Miller"
    assert result.status == "scheduled"
    assert result.houston_display_time


@pytest.mark.asyncio
async def test_consultation_conflict_uses_next_priority_csr() -> None:
    repo = InMemoryConsultationRepository()
    service = ConsultationActionService(repository=repo)

    first = await service.schedule(_req(name="Maya Author", phone="+1 555 000 0001"))
    second = await service.schedule(_req(name="Nora Writer", phone="+1 555 000 0002"))

    assert first.csr_name == "Jerry Miller"
    assert second.csr_name == "Robert Williams"


# ---------------------------------------------------------------------------
# Validation — new required fields: phone + customer_timezone
# ---------------------------------------------------------------------------

def test_consultation_requires_phone() -> None:
    with pytest.raises(ValueError, match="consultation_requires_phone"):
        ConsultationActionRequest(
            thread_id=uuid4(),
            name="Maya Author",
            phone=None,
            customer_timezone="America/Chicago",
            requested_time_text="tomorrow at 4pm",
        )


def test_consultation_requires_customer_timezone() -> None:
    with pytest.raises(ValueError, match="consultation_requires_customer_timezone"):
        ConsultationActionRequest(
            thread_id=uuid4(),
            name="Maya Author",
            phone="+1 555 123 4567",
            customer_timezone=None,
            requested_time_text="tomorrow at 4pm",
        )


def test_consultation_requires_name() -> None:
    # name: str is a required non-optional field — any falsy value (empty/whitespace)
    # is stripped to None by the validator and Pydantic raises a type error.
    import pydantic
    with pytest.raises((ValueError, pydantic.ValidationError)):
        ConsultationActionRequest(
            thread_id=uuid4(),
            name="",
            phone="+1 555 123 4567",
            customer_timezone="America/Chicago",
            requested_time_text="tomorrow at 4pm",
        )


def test_consultation_email_is_optional() -> None:
    """Email must NOT be required — consultation succeeds with only phone + timezone."""
    req = ConsultationActionRequest(
        thread_id=uuid4(),
        name="Maya Author",
        phone="+1 555 123 4567",
        customer_timezone="America/Chicago",
        email=None,
        requested_time_text="tomorrow at 4pm",
    )
    assert req.email is None
    assert req.phone == "+1 555 123 4567"


def test_consultation_accepts_email_when_provided() -> None:
    req = ConsultationActionRequest(
        thread_id=uuid4(),
        name="Maya Author",
        phone="+1 555 123 4567",
        customer_timezone="America/Chicago",
        email="maya@example.com",
        requested_time_text="tomorrow at 4pm",
    )
    assert req.email == "maya@example.com"


# ---------------------------------------------------------------------------
# Time parsing
# ---------------------------------------------------------------------------

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
