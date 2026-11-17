"""Callback hours: when the team can phone a visitor within the promised time.

Rule set H-01 to H-04: the bot itself runs around the clock; only the call-now
offer depends on these hours. Decision on section 18.5 item 2: callbacks run
Monday to Friday only and never on a marked holiday (the same blackout list the
consultation calendar uses).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from bookcraft.components.consultations.holidays import is_blackout_date

DEFAULT_CALLBACK_TZ = "America/Chicago"


def safe_zone(name: str | None) -> ZoneInfo | None:
    """Return the IANA zone for *name*, or None when it is missing or unknown."""
    if not name or not isinstance(name, str):
        return None
    try:
        return ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None


def _hour_label(hour: int, minute: int = 0) -> str:
    suffix = "AM" if hour < 12 else "PM"
    display = hour % 12 or 12
    return f"{display}:{minute:02d} {suffix}" if minute else f"{display} {suffix}"


@dataclass(frozen=True, slots=True)
class CallbackHours:
    """The team's callback window, evaluated in the business time zone (follows DST)."""

    tz_name: str = DEFAULT_CALLBACK_TZ
    start_hour: int = 10
    end_hour: int = 19
    # CALL_OFFER_CUTOFF: the call-now offer stops this many minutes before the
    # window ends, so a call promised "within N minutes" cannot overrun it (H-03).
    cutoff_minutes: int = 10
    # CALLBACK_TIME: what the visitor is told to expect (M-09).
    callback_minutes: int = 5

    @property
    def tz(self) -> ZoneInfo:
        return safe_zone(self.tz_name) or ZoneInfo(DEFAULT_CALLBACK_TZ)

    def local(self, now_utc: datetime | None = None) -> datetime:
        moment = now_utc or datetime.now(UTC)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment.astimezone(self.tz)

    @staticmethod
    def is_callback_day(day: date) -> bool:
        """Monday to Friday, excluding marked holidays."""
        return day.weekday() < 5 and not is_blackout_date(day)

    def is_open(self, now_utc: datetime | None = None) -> bool:
        """True while the team is taking calls (start inclusive, end exclusive)."""
        local = self.local(now_utc)
        if not self.is_callback_day(local.date()):
            return False
        return self.start_hour <= local.hour < self.end_hour

    def call_now_available(self, now_utc: datetime | None = None) -> bool:
        """True when a call within ``callback_minutes`` can still be promised (H-02, H-03)."""
        local = self.local(now_utc)
        if not self.is_callback_day(local.date()):
            return False
        opens = local.replace(hour=self.start_hour, minute=0, second=0, microsecond=0)
        closes = local.replace(hour=self.end_hour, minute=0, second=0, microsecond=0)
        return opens <= local < closes - timedelta(minutes=self.cutoff_minutes)

    def hour_in_window(self, hour: int, minute: int = 0) -> bool:
        """True when a Central clock time falls inside callback hours."""
        return self.start_hour * 60 <= hour * 60 + minute < self.end_hour * 60

    def window_phrase(self) -> str:
        """Customer-facing description of the window, in the business time zone."""
        return (
            f"{_hour_label(self.start_hour)} to {_hour_label(self.end_hour)} Central, "
            "Monday to Friday"
        )

    def local_window_phrase(
        self, visitor_tz: str | None, now_utc: datetime | None = None
    ) -> str | None:
        """The same window in the visitor's own time zone, or None when it adds nothing.

        Returned only for a known zone whose offset differs from the business zone,
        so a visitor in another region is not left to do the arithmetic.
        """
        zone = safe_zone(visitor_tz)
        if zone is None:
            return None
        local = self.local(now_utc)
        opens = local.replace(hour=self.start_hour, minute=0, second=0, microsecond=0)
        closes = local.replace(hour=self.end_hour, minute=0, second=0, microsecond=0)
        v_open, v_close = opens.astimezone(zone), closes.astimezone(zone)
        if v_open.utcoffset() == opens.utcoffset():
            return None
        return (
            f"{_hour_label(v_open.hour, v_open.minute)} to "
            f"{_hour_label(v_close.hour, v_close.minute)} your time"
        )

    def to_business_clock(
        self, hour: int, minute: int, visitor_tz: str | None, now_utc: datetime | None = None
    ) -> tuple[int, int]:
        """Convert a visitor-local clock time to the business zone (today's offsets)."""
        zone = safe_zone(visitor_tz)
        if zone is None:
            return hour, minute
        base = (now_utc or datetime.now(UTC)).astimezone(zone)
        visitor_moment = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        business = visitor_moment.astimezone(self.tz)
        return business.hour, business.minute
