"""Holiday blackout dates for consultation scheduling.

Covers the three enforcement points that share one source of truth (the holiday
cache), plus the cache helpers and the /api/v1/holidays CRUD surface:
  * an *explicit* request for a closed day is declined (HolidayError),
  * an *inferred* time that lands on a closed day is rolled forward, and
  * offered slots never fall on a closed day.
"""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from bookcraft.components.consultations.holidays import (
    add_to_cache,
    cached_holidays,
    is_blackout_date,
    remove_from_cache,
    set_holiday_cache,
)
from bookcraft.components.consultations.service import (
    HolidayError,
    _normalize_to_business_window,
    _parse_requested_start,
)
from bookcraft.components.consultations.slots import suggest_consultation_slots

CT = ZoneInfo("America/Chicago")
# 2026-08-04 is a Tuesday; 08-03 a Monday, 08-05 a Wednesday.
AUG_4 = "2026-08-04"


@pytest.fixture(autouse=True)
def _clear_holiday_cache():
    # The cache is process-global; isolate every test.
    set_holiday_cache(set())
    yield
    set_holiday_cache(set())


def _parse(text: str, now: datetime):
    return _parse_requested_start(
        text=text,
        customer_tz=CT,
        business_tz=CT,
        business_start_hour=10,
        business_end_hour=19,
        now=now,
    )


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def test_cache_set_add_remove() -> None:
    assert is_blackout_date(date(2026, 8, 4)) is False
    set_holiday_cache({AUG_4})
    assert is_blackout_date(date(2026, 8, 4)) is True
    assert is_blackout_date(date(2026, 8, 5)) is False
    add_to_cache(date(2026, 12, 25))
    assert cached_holidays() == {AUG_4, "2026-12-25"}
    remove_from_cache(AUG_4)
    assert is_blackout_date(date(2026, 8, 4)) is False


def test_cache_accepts_date_or_string() -> None:
    set_holiday_cache([date(2026, 8, 4), "2026-12-25"])
    assert is_blackout_date(date(2026, 8, 4)) is True
    assert is_blackout_date(date(2026, 12, 25)) is True


# ---------------------------------------------------------------------------
# Enforcement: explicit request for a closed day is declined
# ---------------------------------------------------------------------------


def test_explicit_holiday_request_raises() -> None:
    set_holiday_cache({AUG_4})
    now = datetime(2026, 7, 30, 9, 0, tzinfo=CT)  # before the holiday
    with pytest.raises(HolidayError):
        _parse("let's do august 4, 2026 at 2pm", now)


def test_explicit_non_holiday_request_is_unaffected() -> None:
    set_holiday_cache({AUG_4})
    now = datetime(2026, 7, 30, 9, 0, tzinfo=CT)
    parsed = _parse("august 5, 2026 at 2pm", now)  # the day after the holiday
    assert (parsed.year, parsed.month, parsed.day) == (2026, 8, 5)
    assert parsed.hour == 14


def test_no_holiday_configured_allows_the_day() -> None:
    # Empty cache → the date engine behaves exactly as before.
    now = datetime(2026, 7, 30, 9, 0, tzinfo=CT)
    parsed = _parse("august 4, 2026 at 2pm", now)
    assert (parsed.year, parsed.month, parsed.day) == (2026, 8, 4)


# ---------------------------------------------------------------------------
# Enforcement: an inferred time that lands on a closed day rolls forward
# ---------------------------------------------------------------------------


def test_normalize_rolls_off_holiday_to_next_open_day() -> None:
    set_holiday_cache({AUG_4})
    on_holiday = datetime(2026, 8, 4, 14, 0, tzinfo=CT)  # Tue 2 PM, a holiday
    rolled = _normalize_to_business_window(
        on_holiday, business_start_hour=10, business_end_hour=19
    )
    assert (rolled.year, rolled.month, rolled.day) == (2026, 8, 5)  # Wednesday
    assert rolled.hour == 10 and rolled.minute == 0


def test_normalize_rolls_off_holiday_bridged_to_weekend() -> None:
    # Friday holiday → Saturday/Sunday skipped → next open day is Monday.
    set_holiday_cache({"2026-08-07"})  # Friday
    on_holiday = datetime(2026, 8, 7, 11, 0, tzinfo=CT)
    rolled = _normalize_to_business_window(
        on_holiday, business_start_hour=10, business_end_hour=19
    )
    assert rolled.weekday() == 0  # Monday
    assert (rolled.year, rolled.month, rolled.day) == (2026, 8, 10)


# ---------------------------------------------------------------------------
# Enforcement: offered slots never fall on a closed day
# ---------------------------------------------------------------------------


def test_offered_slots_skip_holiday() -> None:
    set_holiday_cache({AUG_4})
    now = datetime(2026, 8, 3, 9, 0, tzinfo=CT)  # Monday morning
    slots = suggest_consultation_slots(now=now, count=8)
    assert slots, "expected some slots"
    for slot in slots:
        assert slot.start.date() != date(2026, 8, 4), (
            f"slot fell on the holiday: {slot.label}"
        )
        assert slot.start.weekday() < 5


def test_offered_slots_unaffected_without_holiday() -> None:
    now = datetime(2026, 8, 3, 9, 0, tzinfo=CT)
    slots = suggest_consultation_slots(now=now, count=8)
    # With no holiday configured, Aug 4 (a Tuesday) is a perfectly valid day and
    # normally appears among the openings — proving the skip above is holiday-driven.
    assert any(slot.start.date() == date(2026, 8, 4) for slot in slots)


# ---------------------------------------------------------------------------
# API: /api/v1/holidays CRUD roundtrip (write-through cache included)
# ---------------------------------------------------------------------------


class _FakeHolidayRepo:
    """In-memory stand-in for HolidayRepository (no DB needed for the API test)."""

    def __init__(self) -> None:
        self._rows: dict[date, SimpleNamespace] = {}

    async def list_all(self):
        return [self._rows[k] for k in sorted(self._rows)]

    async def add(self, *, holiday_date, label=None, created_by=None):
        row = self._rows.get(holiday_date)
        if row is None:
            row = SimpleNamespace(
                holiday_date=holiday_date, label=label, created_by=created_by
            )
            self._rows[holiday_date] = row
        elif label is not None:
            row.label = label
        return row

    async def remove(self, *, holiday_date):
        return self._rows.pop(holiday_date, None) is not None


def _client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import bookcraft.api.holidays as holidays_api

    # The auth path (JWT/settings) is exercised elsewhere; stub it here so the
    # test focuses on CRUD + cache behavior.
    monkeypatch.setattr(holidays_api, "require_http_auth", lambda request, settings: None)

    app = FastAPI()
    app.include_router(holidays_api.router)
    app.state.settings = SimpleNamespace()
    app.state.holiday_repository = _FakeHolidayRepo()
    return TestClient(app)


def test_api_crud_roundtrip(monkeypatch) -> None:
    client = _client(monkeypatch)

    assert client.get("/api/v1/holidays").json() == {"holidays": []}

    created = client.post(
        "/api/v1/holidays",
        json={"date": AUG_4, "label": "Company holiday", "created_by": "csr:jane"},
    )
    assert created.status_code == 200
    assert created.json()["date"] == AUG_4
    # Write-through: the scheduling cache reflects it immediately.
    assert is_blackout_date(date(2026, 8, 4)) is True

    listed = client.get("/api/v1/holidays").json()["holidays"]
    assert [h["date"] for h in listed] == [AUG_4]
    assert listed[0]["label"] == "Company holiday"

    deleted = client.request("DELETE", f"/api/v1/holidays/{AUG_4}")
    assert deleted.status_code == 200
    assert deleted.json() == {"date": AUG_4, "removed": True}
    assert is_blackout_date(date(2026, 8, 4)) is False
    assert client.get("/api/v1/holidays").json() == {"holidays": []}


def test_api_rejects_bad_date(monkeypatch) -> None:
    client = _client(monkeypatch)
    assert client.post("/api/v1/holidays", json={"date": "2026-8-4"}).status_code == 422
    assert client.post("/api/v1/holidays", json={"date": "2026-13-40"}).status_code == 422
    assert client.request("DELETE", "/api/v1/holidays/not-a-date").status_code == 422
