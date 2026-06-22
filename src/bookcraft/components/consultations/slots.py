"""Consultation slot suggestions.

When a customer gives an *indefinite* call time ("anytime", "next week",
"Friday", "afternoon"), we don't silently coerce it into a booking. Instead we
offer a handful of concrete, bookable half-hour slots inside the business window
(Mon–Fri, 10 AM–7 PM Central by default) so the customer can pick one and the
indefinite answer is narrowed to a definite one.

Pure functions — no I/O, no CSR-roster/conflict lookups. The actual booking
(`ConsultationActionService.schedule`) still applies the authoritative window
and CSR selection; these suggestions only have to be *plausible* openings.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DEFAULT_BUSINESS_TZ = "America/Chicago"
DEFAULT_BUSINESS_START_HOUR = 10
DEFAULT_BUSINESS_END_HOUR = 19
# Half-hour granularity for offered slots.
SLOT_GRANULARITY_MINUTES = 30
# Gap between successive suggestions so they spread across morning/afternoon and
# roll onto the next business day rather than clustering back-to-back.
DEFAULT_SLOT_STEP_MINUTES = 150


@dataclass(slots=True, frozen=True)
class ConsultationSlot:
    start: datetime
    label: str


def _round_up_to_half_hour(value: datetime) -> datetime:
    value = value.replace(second=0, microsecond=0)
    if value.minute == 0 or value.minute == 30:
        # Already on a boundary — push to the *next* one so we never suggest "now".
        return value + timedelta(minutes=SLOT_GRANULARITY_MINUTES)
    if value.minute < 30:
        return value.replace(minute=30)
    return (value + timedelta(hours=1)).replace(minute=0)


def _next_business_half_hour(
    value: datetime,
    *,
    business_start_hour: int,
    business_end_hour: int,
) -> datetime:
    """Advance ``value`` to the next valid half-hour start inside the business window.

    A 30-minute consultation must finish by close, so the last bookable start is
    ``business_end_hour - 0:30`` (e.g. 18:30 for a 19:00 close).
    """
    candidate = _round_up_to_half_hour(value)
    # Bounded loop — at most a few iterations to roll past weekends / after-hours.
    for _ in range(14):
        if candidate.weekday() >= 5:  # Saturday/Sunday
            candidate = (candidate + timedelta(days=1)).replace(
                hour=business_start_hour, minute=0
            )
            continue
        if candidate.hour < business_start_hour:
            candidate = candidate.replace(hour=business_start_hour, minute=0)
            continue
        # Past the last bookable start (>= close, leaving no room for 30 min).
        if candidate.hour >= business_end_hour:
            candidate = (candidate + timedelta(days=1)).replace(
                hour=business_start_hour, minute=0
            )
            continue
        return candidate
    return candidate


def _format_slot(value: datetime, *, tz_label: str) -> str:
    # e.g. "Friday, Jun 20 at 2:30 PM CT"
    return f"{value.strftime('%A, %b %-d at %-I:%M %p')} {tz_label}"


def suggest_consultation_slots(
    *,
    now: datetime,
    count: int = 3,
    business_start_hour: int = DEFAULT_BUSINESS_START_HOUR,
    business_end_hour: int = DEFAULT_BUSINESS_END_HOUR,
    business_tz: ZoneInfo | None = None,
    step_minutes: int = DEFAULT_SLOT_STEP_MINUTES,
    tz_label: str = "CT",
) -> list[ConsultationSlot]:
    """Return ``count`` concrete half-hour openings inside the business window.

    Slots start from the next valid half-hour after ``now`` and step forward so
    they spread across times and roll onto subsequent business days.
    """
    tz = business_tz or ZoneInfo(DEFAULT_BUSINESS_TZ)
    cursor = now.astimezone(tz)
    slots: list[ConsultationSlot] = []
    seen: set[datetime] = set()
    for _ in range(max(count, 0)):
        start = _next_business_half_hour(
            cursor,
            business_start_hour=business_start_hour,
            business_end_hour=business_end_hour,
        )
        if start in seen:
            # Defensive: never emit duplicates if the step lands on the same slot.
            cursor = start + timedelta(minutes=SLOT_GRANULARITY_MINUTES)
            start = _next_business_half_hour(
                cursor,
                business_start_hour=business_start_hour,
                business_end_hour=business_end_hour,
            )
        seen.add(start)
        slots.append(ConsultationSlot(start=start, label=_format_slot(start, tz_label=tz_label)))
        cursor = start + timedelta(minutes=step_minutes)
    return slots


def suggest_consultation_slot_labels(
    *,
    now: datetime,
    count: int = 3,
    business_start_hour: int = DEFAULT_BUSINESS_START_HOUR,
    business_end_hour: int = DEFAULT_BUSINESS_END_HOUR,
    business_tz: ZoneInfo | None = None,
    tz_label: str = "CT",
) -> list[str]:
    """Convenience wrapper returning just the display labels for the slots."""
    return [
        slot.label
        for slot in suggest_consultation_slots(
            now=now,
            count=count,
            business_start_hour=business_start_hour,
            business_end_hour=business_end_hour,
            business_tz=business_tz,
            tz_label=tz_label,
        )
    ]
