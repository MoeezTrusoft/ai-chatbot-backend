"""Canonical consultation call-time parsing.

Single source of truth for turning free text into a bookable call time. Replaces
the two divergent regexes that used to live in ``consultation_state.py`` and
``consultation_objective.py`` — they disagreed on the same message, so one engine
could advance the stage while the other re-asked (chat 6816).

Two properties matter here:

1. **Bare clock hours must parse.** Both old regexes required an ``am``/``pm``
   suffix, so "9-12 works best", "probably 12 would work the best" and "Friday
   works best at 2" all extracted *nothing*. The customer answered the time
   question four times and the bot re-asked every time, because from the engine's
   point of view no time was ever given.

2. **Bare numbers are only times in context.** "24 pages", "page 2-4", "6
   dragonets" must never become a call time. Numeric parsing is therefore gated
   on ``allow_numeric`` (the bot actually asked for a time this turn) *and*
   guarded by a length/noise check, so a pasted manuscript can't book a call.

A time is bookable-*definite* only when it pins BOTH a specific day and a single
clock time. A range ("9-12") is a window, not an appointment — it yields concrete
slot offers instead of a silent booking.

Engines compute. Claude writes.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

# ---------------------------------------------------------------------------
# Component patterns
# ---------------------------------------------------------------------------

_MONTHS = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
_WEEKDAYS = r"monday|tuesday|wednesday|thursday|friday|saturday|sunday"

# A specific calendar day. Ordered longest-first so "next Friday" wins over "Friday".
_DAY_RE = re.compile(
    r"\b(?:"
    rf"(?:this|next)\s+(?:{_WEEKDAYS})|"
    rf"(?:{_MONTHS})\s+\d{{1,2}}(?:st|nd|rd|th)?|"
    rf"\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS})|"
    r"\d{4}-\d{1,2}-\d{1,2}|"
    r"\d{1,2}/\d{1,2}|"
    rf"{_WEEKDAYS}|"
    r"tomorrow|today"
    r")\b",
    re.IGNORECASE,
)

# Explicit clock time with a meridiem — unambiguous, always trusted.
_CLOCK_AMPM_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s?m\.?\b", re.IGNORECASE)

_NOON_RE = re.compile(r"\b(?:noon|midday|mid-day)\b", re.IGNORECASE)

# An hour RANGE — "9-12", "between 9 and 3", "9 to 12", "10am-2pm". A window, not
# a bookable instant.
_HOUR_RANGE_RE = re.compile(
    r"\b(?:between\s+|from\s+)?"
    r"(\d{1,2})(?::(\d{2}))?\s*(?:([ap])\.?\s?m\.?)?\s*"
    r"(?:-|–|—|\bto\b|\band\b)\s*"
    r"(\d{1,2})(?::(\d{2}))?\s*(?:([ap])\.?\s?m\.?)?\b",
    re.IGNORECASE,
)

# A bare hour introduced by a time preposition — "at 2", "around 12", "by 10:30".
_PREPOSED_HOUR_RE = re.compile(
    r"\b(?:at|around|about|by)\s+(\d{1,2})(?::(\d{2}))?\b", re.IGNORECASE
)

# A bare standalone hour — "probably 12 would work the best". Only consulted when
# numeric parsing is allowed AND the message is short (see _numeric_time_allowed).
_STANDALONE_HOUR_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\b")

# A vague window — no clock, no day.
_WINDOW_RE = re.compile(
    r"\b(?:morning|afternoon|evening|anytime|any\s+time|whenever|any\s+day)\b",
    re.IGNORECASE,
)

# Numeric noise that is emphatically NOT a time. A pasted manuscript is full of
# "page 2-4" / "Chapter 5" / "24 pages"; without this guard the standalone-hour
# and range patterns would mine a booking out of a book (chat 6816, where the
# customer pasted ~5k words while the bot was awaiting a call time).
_TIME_NOISE_RE = re.compile(
    r"\b(?:page|pages|chapter|chapters|word|words|book|volume|part|line|verse)\s*\d"
    r"|\d+\s*(?:pages?|chapters?|words?|years?|months?)\b",
    re.IGNORECASE,
)

# Above this many words a message is prose, not a time answer.
_MAX_WORDS_FOR_NUMERIC_TIME = 20

# A *standalone* bare hour ("probably 12 would work") is the loosest signal we
# accept — any small integer qualifies. Ranges ("9-12") and preposed hours ("at
# 2") carry their own syntactic evidence, but a lone number does not, so it needs
# a much tighter budget: "find 6 dragonets to find it" is 17 words and would
# otherwise book a 6pm call.
_MAX_WORDS_FOR_STANDALONE_HOUR = 8


def _numeric_time_allowed(text: str) -> bool:
    """True when bare numbers in *text* may be read as clock hours.

    Requires a short, noise-free message. Callers must ALSO have established that
    a call time was actually being solicited (``allow_numeric``).
    """
    if _TIME_NOISE_RE.search(text):
        return False
    return len(text.split()) <= _MAX_WORDS_FOR_NUMERIC_TIME


def _standalone_hour_allowed(text: str) -> bool:
    """True when a lone integer in *text* may be read as a clock hour."""
    return _numeric_time_allowed(text) and len(text.split()) <= _MAX_WORDS_FOR_STANDALONE_HOUR


def _valid_hour(hour: int) -> bool:
    return 0 <= hour <= 23


def _normalize_meridiem(hour: int, meridiem: str | None) -> str:
    """Render an hour as a canonical clock string, inferring am/pm when absent.

    Consultations run 10 AM – 7 PM, so an unqualified hour maps to the only
    reading that falls in business hours: 12 → noon, 1–6 → pm, 7–11 → am.
    """
    if meridiem:
        return f"{hour}{meridiem.lower()}m"
    if hour == 0:
        return "12am"
    if hour == 12:
        return "12pm"
    if 13 <= hour <= 23:
        return f"{hour - 12}pm"
    if 1 <= hour <= 6:
        return f"{hour}pm"
    return f"{hour}am"


def _format_clock(hour: int, minute: str | None, meridiem: str | None) -> str:
    base = _normalize_meridiem(hour, meridiem)
    if minute and minute != "00":
        # "2pm" + "30" → "2:30pm"
        return re.sub(r"^(\d{1,2})", rf"\1:{minute}", base)
    return base


class CallTimeParts(BaseModel):
    """A call time decomposed into independently-captured components.

    Customers give a day and a clock time on *different* turns ("Friday works
    best" … "probably 12"). Keeping the parts separate lets the reducer merge
    across turns instead of discarding the half it already had.
    """

    model_config = ConfigDict(extra="forbid")

    day: str | None = None
    clock: str | None = None
    clock_end: str | None = None  # set only for ranges ("9am" → "12pm")
    window: str | None = None

    @property
    def is_range(self) -> bool:
        return self.clock_end is not None

    @property
    def is_definite(self) -> bool:
        """True when this pins a specific day AND a single clock time."""
        return bool(self.day and self.clock and not self.is_range)

    @property
    def is_empty(self) -> bool:
        return not (self.day or self.clock or self.window)

    def to_text(self) -> str | None:
        """Render the canonical display string, round-trippable via ``parse``."""
        if self.is_empty:
            return None
        if self.day and self.clock and self.clock_end:
            return f"{self.day} between {self.clock} and {self.clock_end}"
        if self.clock and self.clock_end:
            return f"between {self.clock} and {self.clock_end}"
        if self.day and self.clock:
            return f"{self.day} at {self.clock}"
        if self.day and self.window:
            return f"{self.day} {self.window}"
        return self.day or self.clock or self.window


def parse_call_time(text: str, *, allow_numeric: bool = False) -> CallTimeParts:
    """Extract call-time components from *text*.

    ``allow_numeric`` opens up bare-hour readings ("at 2", "9-12", "12"). Pass it
    only when the bot solicited a call time — otherwise "24 pages" becomes 2pm.
    """
    if not text or not text.strip():
        return CallTimeParts()

    day_match = _DAY_RE.search(text)
    day = day_match.group(0).strip() if day_match else None

    window_match = _WINDOW_RE.search(text)
    window = window_match.group(0).strip().lower() if window_match else None
    if window in {"any time", "any day", "whenever"}:
        window = "anytime"

    clock: str | None = None
    clock_end: str | None = None

    numeric_ok = allow_numeric and _numeric_time_allowed(text)

    # 1. An explicit am/pm range ("10am-2pm") or, when numerics are allowed, a
    #    bare-hour range ("9-12", "between 9 and 3").
    range_match = _HOUR_RANGE_RE.search(text)
    if range_match:
        h1, m1, mer1, h2, m2, mer2 = range_match.groups()
        has_meridiem = bool(mer1 or mer2)
        if (has_meridiem or numeric_ok) and _valid_hour(int(h1)) and _valid_hour(int(h2)):
            # A range shares its meridiem when only one side states it ("9-12pm").
            clock = _format_clock(int(h1), m1, mer1 or mer2)
            clock_end = _format_clock(int(h2), m2, mer2 or mer1)

    # 2. A single explicit clock time.
    if clock is None:
        ampm_match = _CLOCK_AMPM_RE.search(text)
        if ampm_match and _valid_hour(int(ampm_match.group(1))):
            clock = _format_clock(
                int(ampm_match.group(1)), ampm_match.group(2), ampm_match.group(3)
            )

    # 3. "noon".
    if clock is None and _NOON_RE.search(text):
        clock = "12pm"

    # 4. A preposition-introduced bare hour ("at 2").
    if clock is None and numeric_ok:
        prep_match = _PREPOSED_HOUR_RE.search(text)
        if prep_match and _valid_hour(int(prep_match.group(1))):
            clock = _format_clock(int(prep_match.group(1)), prep_match.group(2), None)

    # 5. A standalone bare hour ("probably 12 would work the best"). Last resort:
    #    only for short, noise-free messages where a time was solicited.
    if clock is None and allow_numeric and _standalone_hour_allowed(text):
        for m in _STANDALONE_HOUR_RE.finditer(text):
            hour = int(m.group(1))
            # Skip a number that is part of the day we already captured ("July 15").
            if day and m.group(0) in day:
                continue
            if _valid_hour(hour):
                clock = _format_clock(hour, m.group(2), None)
                break

    # A window is redundant once an exact clock is known.
    if clock and window and window != "anytime":
        window = None

    return CallTimeParts(day=day, clock=clock, clock_end=clock_end, window=window)


def merge_call_time(existing: CallTimeParts, incoming: CallTimeParts) -> CallTimeParts:
    """Merge a newly-parsed time over what we already knew, field by field.

    The customer narrows a time across turns ("Friday works best" → "probably
    12"). Newer information wins per component; components the new message is
    silent about are retained. A fresh *range* clears a stale single clock and
    vice versa, so the two never blend into a nonsense window.
    """
    day = incoming.day or existing.day
    window = incoming.window or existing.window

    if incoming.clock:
        clock, clock_end = incoming.clock, incoming.clock_end
    else:
        clock, clock_end = existing.clock, existing.clock_end

    if clock and window and window != "anytime":
        window = None

    return CallTimeParts(day=day, clock=clock, clock_end=clock_end, window=window)


def extract_call_time(
    text: str,
    *,
    existing: str | None = None,
    allow_numeric: bool = False,
) -> str | None:
    """Parse *text*, merge over *existing*, and return the canonical display string.

    This is the entry point both consultation engines use, so they can no longer
    disagree about what the customer said.
    """
    incoming = parse_call_time(text, allow_numeric=allow_numeric)
    prior = parse_call_time(existing or "", allow_numeric=True)
    merged = merge_call_time(prior, incoming)
    return merged.to_text()


def is_definite_call_time(text: str | None) -> bool:
    """True when *text* names BOTH a specific day and a single clock time.

    Ranges ("Wednesday between 9am and 12pm") are windows, not appointments, and
    are deliberately NOT definite — they must resolve to a concrete slot first.
    """
    if not text:
        return False
    return parse_call_time(text, allow_numeric=True).is_definite
