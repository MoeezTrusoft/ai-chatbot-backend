"""Callback hours (Visitor Messaging Rule Set v2.4: H-02, H-03; decision: Monday to Friday,
marked holidays excluded)."""
from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from bookcraft.components.callbacks import CallbackHours
from bookcraft.components.consultations.holidays import set_holiday_cache


@pytest.fixture(autouse=True)
def _no_holidays():
    set_holiday_cache([])
    yield
    set_holiday_cache([])


def _central(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """A Central Time wall clock as an aware UTC datetime (follows daylight saving)."""
    local = datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("America/Chicago"))
    return local.astimezone(UTC)


HOURS = CallbackHours()


def test_call_now_is_available_inside_callback_hours_on_a_weekday():
    assert HOURS.call_now_available(_central(2026, 11, 18, 14))  # Wednesday 2 PM


def test_call_now_starts_at_ten_and_not_before():
    assert not HOURS.call_now_available(_central(2026, 11, 18, 9, 59))
    assert HOURS.call_now_available(_central(2026, 11, 18, 10, 0))


def test_call_now_stops_ten_minutes_before_the_end():
    # H-03: a call within 5 minutes can no longer be promised after 6:50 PM.
    assert HOURS.call_now_available(_central(2026, 11, 18, 18, 49))
    assert not HOURS.call_now_available(_central(2026, 11, 18, 18, 50))
    assert not HOURS.call_now_available(_central(2026, 11, 18, 18, 55))
    # The team is still open until 7 PM for everything else.
    assert HOURS.is_open(_central(2026, 11, 18, 18, 55))
    assert not HOURS.is_open(_central(2026, 11, 18, 19, 0))


def test_no_call_now_at_night():
    assert not HOURS.call_now_available(_central(2026, 11, 18, 23))
    assert not HOURS.call_now_available(_central(2026, 11, 18, 3))


@pytest.mark.parametrize("day", [21, 22])  # Saturday, Sunday
def test_no_call_now_at_the_weekend(day: int):
    assert not HOURS.call_now_available(_central(2026, 11, day, 14))


def test_no_call_now_on_a_marked_holiday():
    set_holiday_cache([date(2026, 11, 26)])  # a Thursday
    assert not HOURS.call_now_available(_central(2026, 11, 26, 14))
    assert HOURS.call_now_available(_central(2026, 11, 25, 14))


def test_hours_follow_daylight_saving():
    # July: CDT = UTC-5, so 10 AM Central is 15:00 UTC.
    assert HOURS.call_now_available(datetime(2026, 7, 15, 15, 0, tzinfo=UTC))
    assert not HOURS.call_now_available(datetime(2026, 7, 15, 14, 59, tzinfo=UTC))


def test_window_phrase_names_hours_and_days():
    assert HOURS.window_phrase() == "10 AM to 7 PM Central, Monday to Friday"


def test_local_window_only_when_the_visitor_is_in_another_zone():
    now = _central(2026, 11, 18, 14)
    assert HOURS.local_window_phrase("America/Chicago", now) is None
    assert HOURS.local_window_phrase(None, now) is None
    assert HOURS.local_window_phrase("Not/AZone", now) is None
    assert HOURS.local_window_phrase("America/New_York", now) == "11 AM to 8 PM your time"


def test_visitor_clock_is_converted_to_the_business_zone():
    now = _central(2026, 11, 18, 14)
    assert HOURS.to_business_clock(15, 0, "America/New_York", now) == (14, 0)
    assert HOURS.to_business_clock(15, 0, None, now) == (15, 0)
