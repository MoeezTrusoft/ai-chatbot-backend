from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from bookcraft.components.consultations.repository import ConsultationRepositoryProtocol
from bookcraft.components.consultations.schemas import (
    ConsultationActionRequest,
    ConsultationActionResult,
    CSRProfile,
)

DEFAULT_CSR_ROSTER = [
    CSRProfile(csr_id="jerry-miller", name="Jerry Miller", priority_rank=1),
    CSRProfile(csr_id="robert-williams", name="Robert Williams", priority_rank=2),
    CSRProfile(csr_id="alex-vartan", name="Alex Vartan", priority_rank=3),
]


@dataclass(slots=True)
class ConsultationActionService:
    repository: ConsultationRepositoryProtocol
    csr_roster: list[CSRProfile] = field(default_factory=lambda: list(DEFAULT_CSR_ROSTER))
    business_start_hour: int = 10
    business_end_hour: int = 19

    async def schedule(
        self,
        request: ConsultationActionRequest,
    ) -> ConsultationActionResult:
        customer_tz = _safe_zoneinfo(request.customer_timezone or request.business_timezone)
        business_tz = _safe_zoneinfo(request.business_timezone)

        requested_start = _parse_requested_start(
            text=request.requested_time_text,
            customer_tz=customer_tz,
            business_tz=business_tz,
            business_start_hour=self.business_start_hour,
            business_end_hour=self.business_end_hour,
        )

        slot_start = _normalize_to_business_window(
            requested_start.astimezone(business_tz),
            business_start_hour=self.business_start_hour,
            business_end_hour=self.business_end_hour,
        )
        slot_end = slot_start + timedelta(minutes=request.duration_minutes)

        csr = await self._select_csr(
            starts_at_utc=slot_start.astimezone(UTC),
            ends_at_utc=slot_end.astimezone(UTC),
        )

        record = await self.repository.create_appointment(
            customer_id=request.customer_id,
            lead_id=request.lead_id,
            thread_id=request.thread_id,
            customer_name=request.name,
            customer_email=request.email,
            customer_phone=request.phone,
            services=request.services,
            csr_id=csr.csr_id,
            csr_name=csr.name,
            priority_rank=csr.priority_rank,
            requested_time_text=request.requested_time_text,
            customer_timezone=request.customer_timezone,
            business_timezone=request.business_timezone,
            starts_at_utc=slot_start.astimezone(UTC),
            ends_at_utc=slot_end.astimezone(UTC),
            houston_display_time=_display_time(slot_start),
            customer_display_time=_display_time(slot_start.astimezone(customer_tz)),
            duration_minutes=request.duration_minutes,
            status="scheduled",
            metadata={
                **request.metadata,
                "csr_priority_order": [profile.name for profile in self._active_roster()],
                "requested_time_interpreted": _display_time(requested_start),
            },
        )

        return ConsultationActionResult(
            appointment_id=record.id,
            lead_id=record.lead_id,
            csr_id=record.csr_id,
            csr_name=record.csr_name,
            priority_rank=record.priority_rank,
            starts_at_utc=slot_start.astimezone(UTC),
            ends_at_utc=slot_end.astimezone(UTC),
            houston_display_time=record.houston_display_time,
            customer_display_time=record.customer_display_time,
            status=record.status,
            customer_safe_summary=(
                f"You're booked with {record.csr_name} for a 30-minute consultation "
                f"at {record.houston_display_time} Houston time."
            ),
            metadata=record.metadata_,
        )

    async def _select_csr(
        self,
        *,
        starts_at_utc: datetime,
        ends_at_utc: datetime,
    ) -> CSRProfile:
        active_roster = self._active_roster()
        if not active_roster:
            raise ValueError("no_active_csr_available")

        for profile in active_roster:
            if not await self.repository.has_conflict(
                csr_id=profile.csr_id,
                starts_at_utc=starts_at_utc,
                ends_at_utc=ends_at_utc,
            ):
                return profile

        raise ValueError("no_csr_available_for_requested_slot")

    def _active_roster(self) -> list[CSRProfile]:
        return sorted(
            [profile for profile in self.csr_roster if profile.active],
            key=lambda profile: profile.priority_rank,
        )


def _safe_zoneinfo(value: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(value or "America/Chicago")
    except Exception:
        return ZoneInfo("America/Chicago")


def _parse_requested_start(
    *,
    text: str,
    customer_tz: ZoneInfo,
    business_tz: ZoneInfo,
    business_start_hour: int,
    business_end_hour: int,
) -> datetime:
    now = datetime.now(customer_tz)
    lowered = text.casefold()

    target_date = _date_from_text(text, now=now)

    if target_date is None:
        target_date = now.date()
        if "tomorrow" in lowered:
            target_date = target_date + timedelta(days=1)
        else:
            weekday = _weekday_from_text(lowered)
            if weekday is not None:
                days_ahead = (weekday - target_date.weekday()) % 7
                if days_ahead == 0:
                    days_ahead = 7
                target_date = target_date + timedelta(days=days_ahead)

    requested_time = _time_from_text(lowered) or time(hour=business_start_hour)
    candidate = datetime.combine(target_date, requested_time, tzinfo=customer_tz)

    # If the user gave a time without timezone, interpret it in the customer timezone.
    # If no customer timezone is known, customer_tz is already Houston/Chicago.
    business_candidate = candidate.astimezone(business_tz)
    return _normalize_to_business_window(
        business_candidate,
        business_start_hour=business_start_hour,
        business_end_hour=business_end_hour,
    )


def _date_from_text(text: str, *, now: datetime) -> date | None:
    lowered = text.casefold()

    iso_match = re.search(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", lowered)
    if iso_match:
        year = int(iso_match.group(1))
        month = int(iso_match.group(2))
        day = int(iso_match.group(3))
        return _safe_date(year, month, day)

    month_names = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }

    month_pattern = "|".join(sorted(month_names, key=len, reverse=True))
    month_match = re.search(
        rf"\b({month_pattern})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(20\d{{2}}))?\b",
        lowered,
    )
    if month_match:
        month = month_names[month_match.group(1).rstrip(".")]
        day = int(month_match.group(2))
        year = int(month_match.group(3) or now.year)
        parsed = _safe_date(year, month, day)

        if parsed is not None and month_match.group(3) is None and parsed < now.date():
            parsed = _safe_date(year + 1, month, day)

        return parsed

    numeric_match = re.search(
        r"\b(\d{1,2})/(\d{1,2})(?:/(20\d{2}|\d{2}))?\b",
        lowered,
    )
    if numeric_match:
        month = int(numeric_match.group(1))
        day = int(numeric_match.group(2))
        raw_year = numeric_match.group(3)

        if raw_year is None:
            year = now.year
        elif len(raw_year) == 2:
            year = 2000 + int(raw_year)
        else:
            year = int(raw_year)

        parsed = _safe_date(year, month, day)

        if parsed is not None and raw_year is None and parsed < now.date():
            parsed = _safe_date(year + 1, month, day)

        return parsed

    return None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _weekday_from_text(text: str) -> int | None:
    weekdays = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    for name, index in weekdays.items():
        if name in text:
            return index
    return None


def _time_from_text(text: str) -> time | None:
    match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text, re.IGNORECASE)
    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    meridiem = match.group(3).casefold()

    if meridiem == "pm" and hour != 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0

    if hour > 23 or minute > 59:
        return None

    return time(hour=hour, minute=minute)


def _normalize_to_business_window(
    value: datetime,
    *,
    business_start_hour: int,
    business_end_hour: int,
) -> datetime:
    normalized = value.replace(second=0, microsecond=0)

    while normalized.weekday() >= 5:
        normalized = (normalized + timedelta(days=1)).replace(
            hour=business_start_hour,
            minute=0,
        )

    if normalized.hour < business_start_hour:
        normalized = normalized.replace(hour=business_start_hour, minute=0)

    if normalized.hour >= business_end_hour:
        normalized = (normalized + timedelta(days=1)).replace(
            hour=business_start_hour,
            minute=0,
        )
        return _normalize_to_business_window(
            normalized,
            business_start_hour=business_start_hour,
            business_end_hour=business_end_hour,
        )

    return normalized


def _display_time(value: datetime) -> str:
    return value.strftime("%A, %B %-d, %Y at %-I:%M %p %Z")
