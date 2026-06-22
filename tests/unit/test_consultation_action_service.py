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
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from bookcraft.components.consultations.service import _parse_requested_start

    business_tz = ZoneInfo("America/Chicago")
    # Deterministic "now" earlier than the requested date so the absolute date is future.
    now = datetime(2026, 5, 1, 9, 0, tzinfo=business_tz)
    parsed = _parse_requested_start(
        text="Please schedule a consultation on May 20, 2026 at 11:00 AM Houston time.",
        customer_tz=business_tz,
        business_tz=business_tz,
        business_start_hour=10,
        business_end_hour=19,
        now=now,
    )
    assert parsed.year == 2026
    assert parsed.month == 5
    assert parsed.day == 20
    assert parsed.hour == 11
    assert parsed.minute == 0


# ---------------------------------------------------------------------------
# Date/time awareness: forward resolution, ordinals, past-date and ambiguity
# ---------------------------------------------------------------------------

_TZ_NAME = "America/Chicago"


def _parse(text: str, now):
    from zoneinfo import ZoneInfo
    from bookcraft.components.consultations.service import _parse_requested_start

    tz = ZoneInfo(_TZ_NAME)
    return _parse_requested_start(
        text=text,
        customer_tz=tz,
        business_tz=tz,
        business_start_hour=10,
        business_end_hour=19,
        now=now,
    )


def _wed_jun_17_2026():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    # Wednesday, June 17, 2026, 8:00 PM Central — matches the audited transcript.
    return datetime(2026, 6, 17, 20, 0, tzinfo=ZoneInfo(_TZ_NAME))


def test_weekday_plus_ordinal_agree_resolves_correctly() -> None:
    # "Monday the 22nd" from Wed Jun 17 -> Mon Jun 22 (the transcript's intended date).
    parsed = _parse("monday the 22nd at 11am", _wed_jun_17_2026())
    assert (parsed.year, parsed.month, parsed.day) == (2026, 6, 22)
    assert parsed.weekday() == 0  # Monday
    assert parsed.hour == 11


def test_bare_ordinal_day_is_parsed() -> None:
    # Regression: "the 22nd" used to be ignored entirely (weekday-only fallback).
    parsed = _parse("the 22nd at 11am", _wed_jun_17_2026())
    assert (parsed.year, parsed.month, parsed.day) == (2026, 6, 22)


def test_weekday_and_ordinal_disagree_raises_ambiguous() -> None:
    from bookcraft.components.consultations.service import AmbiguousDateError

    # The next 22nd is a Monday, so "Tuesday the 22nd" is contradictory.
    with pytest.raises(AmbiguousDateError):
        _parse("tuesday the 22nd", _wed_jun_17_2026())


def test_weekday_contradicting_explicit_date_raises_ambiguous() -> None:
    from bookcraft.components.consultations.service import AmbiguousDateError

    # June 22, 2026 is a Monday; calling it "Tuesday" is contradictory.
    with pytest.raises(AmbiguousDateError):
        _parse("tuesday june 22 at 11am", _wed_jun_17_2026())


def test_explicit_past_date_raises_in_past() -> None:
    from bookcraft.components.consultations.service import RequestedTimeInPastError

    # June 2 is before the current date (June 17) — no silent roll to next year.
    with pytest.raises(RequestedTimeInPastError):
        _parse("june 2 at 11am", _wed_jun_17_2026())


def test_explicit_past_date_with_year_raises_in_past() -> None:
    from bookcraft.components.consultations.service import RequestedTimeInPastError

    with pytest.raises(RequestedTimeInPastError):
        _parse("january 5, 2026 at 2pm", _wed_jun_17_2026())


def test_time_earlier_today_rolls_forward_not_past() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    # 3pm "now"; a bare "11am" (no date) should roll to the next day, not error.
    now = datetime(2026, 6, 22, 15, 0, tzinfo=ZoneInfo(_TZ_NAME))
    parsed = _parse("11am", now)
    assert parsed > now
    assert parsed.hour == 11


def test_weekday_only_resolves_to_next_occurrence() -> None:
    parsed = _parse("monday at 11am", _wed_jun_17_2026())
    assert (parsed.year, parsed.month, parsed.day) == (2026, 6, 22)


@pytest.mark.asyncio
async def test_schedule_rejects_past_date_via_exception() -> None:
    # End-to-end through schedule(): an explicit past date raises, so the dispatcher
    # can surface a "please pick a future time" message instead of booking.
    from bookcraft.components.consultations.service import RequestedTimeInPastError

    # Use a far-past explicit year so it is past regardless of wall clock.
    service = ConsultationActionService(repository=InMemoryConsultationRepository())
    with pytest.raises(RequestedTimeInPastError):
        await service.schedule(_req(requested_time_text="January 5, 2020 at 2pm"))
