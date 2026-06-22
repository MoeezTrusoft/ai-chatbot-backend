from __future__ import annotations

import re
import structlog
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

from bookcraft.components.consultations.repository import ConsultationRepositoryProtocol
from bookcraft.components.consultations.schemas import (
    ConsultationActionRequest,
    ConsultationActionResult,
    CSRProfile,
)

logger = structlog.get_logger(__name__)


class RequestedTimeError(ValueError):
    """Base class for requested-time problems that are surfaced to the customer."""


class RequestedTimeInPastError(RequestedTimeError):
    """The customer asked to book a date/time that has already passed."""


class AmbiguousDateError(RequestedTimeError):
    """The customer's date is internally contradictory (e.g. weekday vs day-of-month)."""


DEFAULT_CSR_ROSTER = [
    CSRProfile(csr_id="jerry-miller", name="Jerry Miller", priority_rank=1),
    CSRProfile(csr_id="robert-williams", name="Robert Williams", priority_rank=2),
    CSRProfile(csr_id="alex-vartan", name="Alex Vartan", priority_rank=3),
]


@dataclass(slots=True)
class ConsultationActionService:
    repository: ConsultationRepositoryProtocol
    csr_roster: list[CSRProfile] = field(default_factory=lambda: list(DEFAULT_CSR_ROSTER))
    # Optional: direct push to CSR Node.js API so consultation appears on dashboard
    # immediately without depending on the action-event sync chain.
    # No token needed — CSR Node.js has no auth; CORS is browser-only and doesn't
    # apply to server-to-server calls. Uses localhost since both run on same server.
    csr_node_api_url: str | None = None
    csr_node_timeout: float = 10.0
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

        result = ConsultationActionResult(
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

        # Directly push the consultation to the CSR Node.js API so it appears
        # on the dashboard immediately — fire-and-forget, never blocks the response.
        if self.csr_node_api_url:
            await self._push_to_csr_api(request=request, result=result)

        return result

    async def _push_to_csr_api(
        self,
        *,
        request: ConsultationActionRequest,
        result: ConsultationActionResult,
    ) -> None:
        """POST consultation data directly to the CSR Node.js API.

        This is a reliability layer — the action-event sync already handles this,
        but a direct call guarantees the consultation shows on the CSR dashboard
        even if the event sync chain fails.
        """
        url = f"{self.csr_node_api_url.rstrip('/')}/api/consultations"
        # No auth header — CSR Node.js has no token-based auth.
        # This is server-to-server (localhost), so CORS doesn't apply.
        headers = {"Content-Type": "application/json"}

        payload = {
            "name": request.name,
            "phone": request.phone,
            "email": request.email,
            "customerTimezone": request.customer_timezone,
            "startsAtUtc": result.starts_at_utc.isoformat(),
            "endsAtUtc": result.ends_at_utc.isoformat(),
            "customerId": str(request.customer_id) if request.customer_id else None,
            "externalAppointmentId": str(result.appointment_id),
            "csrName": result.csr_name,
            "csrId": result.csr_id,
            "source": "ai_chatbot",
        }

        try:
            async with httpx.AsyncClient(timeout=self.csr_node_timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code >= 400:
                    logger.error(
                        "csr_api_consultation_push_failed",
                        status_code=resp.status_code,
                        response_body=resp.text[:500],
                    )
                else:
                    logger.info(
                        "csr_api_consultation_push_success",
                        status_code=resp.status_code,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "csr_api_consultation_push_failed",
                error=str(exc),
                appointment_id=str(result.appointment_id),
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


_TIMEZONE_ALIASES: dict[str, str] = {
    # US Eastern
    "eastern": "America/New_York",
    "eastern time": "America/New_York",
    "eastern timezone": "America/New_York",
    "eastern standard time": "America/New_York",
    "eastern daylight time": "America/New_York",
    "est": "America/New_York",
    "edt": "America/New_York",
    "et": "America/New_York",
    "us/eastern": "America/New_York",
    # US Central
    "central": "America/Chicago",
    "central time": "America/Chicago",
    "central timezone": "America/Chicago",
    "central standard time": "America/Chicago",
    "central daylight time": "America/Chicago",
    "cst": "America/Chicago",
    "cdt": "America/Chicago",
    "ct": "America/Chicago",
    "us/central": "America/Chicago",
    # US Mountain
    "mountain": "America/Denver",
    "mountain time": "America/Denver",
    "mountain timezone": "America/Denver",
    "mountain standard time": "America/Denver",
    "mountain daylight time": "America/Denver",
    "mst": "America/Denver",
    "mdt": "America/Denver",
    "mt": "America/Denver",
    "us/mountain": "America/Denver",
    # US Pacific
    "pacific": "America/Los_Angeles",
    "pacific time": "America/Los_Angeles",
    "pacific timezone": "America/Los_Angeles",
    "pacific standard time": "America/Los_Angeles",
    "pacific daylight time": "America/Los_Angeles",
    "pst": "America/Los_Angeles",
    "pdt": "America/Los_Angeles",
    "pt": "America/Los_Angeles",
    "us/pacific": "America/Los_Angeles",
    # Other common
    "alaska": "America/Anchorage",
    "akst": "America/Anchorage",
    "hawaii": "Pacific/Honolulu",
    "hst": "Pacific/Honolulu",
    "utc": "UTC",
    "gmt": "UTC",
    # Pakistan
    "pkt": "Asia/Karachi",
    "pakistan": "Asia/Karachi",
}


def _normalize_timezone(value: str | None) -> str | None:
    """Normalize common timezone aliases to IANA strings."""
    if not value:
        return None
    normalized = _TIMEZONE_ALIASES.get(value.strip().lower())
    return normalized or value


def _safe_zoneinfo(value: str | None) -> ZoneInfo:
    normalized = _normalize_timezone(value)
    try:
        return ZoneInfo(normalized or "America/Chicago")
    except Exception:
        return ZoneInfo("America/Chicago")


def _parse_requested_start(
    *,
    text: str,
    customer_tz: ZoneInfo,
    business_tz: ZoneInfo,
    business_start_hour: int,
    business_end_hour: int,
    now: datetime | None = None,
) -> datetime:
    now = now or datetime.now(customer_tz)
    lowered = text.casefold()

    target_date, kind = _resolve_target_date(text, now=now)

    requested_time = _time_from_text(lowered)
    has_explicit_time = requested_time is not None

    if target_date is None:
        # No date token at all — infer forward from "now".
        target_date = now.date()
        if "tomorrow" in lowered:
            target_date = target_date + timedelta(days=1)
        kind = "inferred"

    effective_time = requested_time or time(hour=business_start_hour)
    candidate = datetime.combine(target_date, effective_time, tzinfo=customer_tz)

    # Time/date awareness: never book in the past.
    if candidate <= now:
        if kind == "explicit":
            # The customer literally named a date (and/or time) that has passed.
            raise RequestedTimeInPastError(
                f"requested datetime {candidate.isoformat()} is in the past "
                f"(now={now.isoformat()})"
            )
        # Inferred (e.g. a bare clock time earlier today): roll forward one day.
        target_date = target_date + timedelta(days=1)
        candidate = datetime.combine(target_date, effective_time, tzinfo=customer_tz)
        if candidate <= now:  # pragma: no cover - defensive
            raise RequestedTimeInPastError(
                f"requested datetime {candidate.isoformat()} is still in the past"
            )

    del has_explicit_time  # captured for clarity; not needed beyond past-check
    # If the user gave a time without timezone, interpret it in the customer timezone.
    # If no customer timezone is known, customer_tz is already Houston/Chicago.
    business_candidate = candidate.astimezone(business_tz)
    return _normalize_to_business_window(
        business_candidate,
        business_start_hour=business_start_hour,
        business_end_hour=business_end_hour,
    )


def _resolve_target_date(text: str, *, now: datetime) -> tuple[date | None, str | None]:
    """Resolve a requested calendar date from free text.

    Returns ``(date, kind)`` where ``kind`` is:
      * ``"explicit"`` — the customer named a specific calendar date (ISO,
        ``Month DD``, or ``M/D``). A past explicit date is an error, not rolled.
      * ``"inferred"`` — derived from a weekday name, a bare ordinal day
        ("the 22nd"), or "tomorrow"/"today"; always resolved forward.
      * ``None`` — no date token present.

    Raises :class:`AmbiguousDateError` when a weekday name and a day-of-month
    disagree (e.g. "Tuesday the 22nd" when the next 22nd is a Monday).
    """
    lowered = text.casefold()
    explicit = _date_from_text(text, now=now)
    weekday = _weekday_from_text(lowered)
    ordinal_day = _ordinal_day_from_text(lowered)

    if explicit is not None:
        # Cross-check a stated weekday against the explicit calendar date.
        if weekday is not None and explicit.weekday() != weekday:
            raise AmbiguousDateError(
                f"weekday name does not match explicit date {explicit.isoformat()}"
            )
        return explicit, "explicit"

    if weekday is not None and ordinal_day is not None:
        wd_date = _next_weekday_date(weekday, now=now)
        ord_date = _next_ordinal_date(ordinal_day, now=now)
        if ord_date is None or wd_date != ord_date:
            raise AmbiguousDateError(
                "weekday and day-of-month do not agree on a single nearby date"
            )
        return wd_date, "inferred"

    if weekday is not None:
        return _next_weekday_date(weekday, now=now), "inferred"

    if ordinal_day is not None:
        ord_date = _next_ordinal_date(ordinal_day, now=now)
        if ord_date is None:
            return None, None
        return ord_date, "inferred"

    if "tomorrow" in lowered:
        return now.date() + timedelta(days=1), "inferred"
    if "today" in lowered:
        return now.date(), "inferred"

    return None, None


def _next_weekday_date(weekday: int, *, now: datetime) -> date:
    today = now.date()
    days_ahead = (weekday - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return today + timedelta(days=days_ahead)


def _next_ordinal_date(day: int, *, now: datetime) -> date | None:
    """Nearest date on or after today whose day-of-month is ``day``."""
    if not 1 <= day <= 31:
        return None
    year, month = now.year, now.month
    for _ in range(13):
        candidate = _safe_date(year, month, day)
        if candidate is not None and candidate >= now.date():
            return candidate
        month += 1
        if month > 12:
            month = 1
            year += 1
    return None


def _ordinal_day_from_text(text: str) -> int | None:
    """Extract a bare ordinal day-of-month like "the 22nd" / "22nd".

    Only matches when an ordinal suffix is present, so plain numbers in a time
    ("11 am") or year are not misread as a day.
    """
    match = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)\b", text)
    if not match:
        return None
    day = int(match.group(1))
    return day if 1 <= day <= 31 else None


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
        # A bare "Month DD" (no year) is treated as the current year and left as-is.
        # If that lands in the past, the caller raises RequestedTimeInPastError so the
        # bot can ask for a future date — we no longer silently roll to next year.
        return _safe_date(year, month, day)

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

        # As with "Month DD", a yearless numeric date stays in the current year;
        # a past result is surfaced to the customer rather than rolled forward.
        return _safe_date(year, month, day)

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
