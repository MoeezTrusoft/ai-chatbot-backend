"""Canonical call-time parser (chat 6816).

The bot asked Priya for a call time, she answered four times, and the bot
re-asked every single time — both engines' regexes required an am/pm suffix, so
"9-12 works best" and "probably 12 would work the best" extracted nothing at all.
"""

from bookcraft.components.sales.call_time import (
    extract_call_time,
    is_definite_call_time,
    parse_call_time,
)


class TestBareClockHours:
    """Bare hours are how people actually answer "what time?"."""

    def test_bare_hour_range_is_parsed(self) -> None:
        parts = parse_call_time("9-12 works best", allow_numeric=True)
        assert parts.clock == "9am"
        assert parts.clock_end == "12pm"
        assert parts.is_range is True

    def test_standalone_bare_hour_is_parsed(self) -> None:
        assert parse_call_time(
            "probably 12 would work the best", allow_numeric=True
        ).clock == "12pm"

    def test_preposed_bare_hour_is_parsed(self) -> None:
        assert parse_call_time("Friday works best at 2", allow_numeric=True).clock == "2pm"

    def test_between_and_range_is_parsed(self) -> None:
        parts = parse_call_time("any time between 9 and 3", allow_numeric=True)
        assert (parts.clock, parts.clock_end) == ("9am", "3pm")

    def test_noon_is_a_clock_time(self) -> None:
        assert parse_call_time("noon works", allow_numeric=False).clock == "12pm"

    def test_unqualified_hour_resolves_into_business_hours(self) -> None:
        # Consultations run 10 AM – 7 PM: "at 2" is 2pm, never 2am.
        assert parse_call_time("at 2", allow_numeric=True).clock == "2pm"
        assert parse_call_time("at 10", allow_numeric=True).clock == "10am"


class TestNumericGating:
    """A bare number is only a time when we actually asked for one."""

    def test_bare_hours_ignored_when_not_soliciting_a_time(self) -> None:
        assert extract_call_time("probably 12 would work the best", allow_numeric=False) is None
        assert extract_call_time("9-12 works best", allow_numeric=False) is None

    def test_explicit_ampm_parses_even_when_not_soliciting(self) -> None:
        assert extract_call_time("Friday at 2pm", allow_numeric=False) == "Friday at 2pm"

    def test_page_and_word_counts_are_never_times(self) -> None:
        # "i only have 24 pages" must not become a call time.
        for msg in (
            "i'm still working on it i only have 24 pages",
            "Chapter one page 7-9",
            "He was in prison for 35 years",
        ):
            assert extract_call_time(msg, allow_numeric=True) is None, msg

    def test_long_prose_never_yields_a_standalone_hour(self) -> None:
        # The customer pasted ~5k words of manuscript while the bot was awaiting a
        # time; a lone integer in prose must not book a call.
        prose = (
            "a dragon book where the jem is stolen and they have "
            "to find 6 dragonets to find it"
        )
        assert extract_call_time(prose, allow_numeric=True) is None


class TestDefiniteness:
    """A range is a window to narrow, not an appointment to book."""

    def test_range_is_not_definite(self) -> None:
        assert is_definite_call_time("Wednesday between 9am and 12pm") is False

    def test_day_plus_single_clock_is_definite(self) -> None:
        assert is_definite_call_time("Wednesday at 12pm") is True

    def test_day_without_clock_is_not_definite(self) -> None:
        assert is_definite_call_time("Friday") is False

    def test_clock_without_day_is_not_definite(self) -> None:
        assert is_definite_call_time("2pm") is False


class TestMergeAcrossTurns:
    """Customers give the day and the hour on different turns."""

    def test_day_then_hour_merges_into_a_definite_time(self) -> None:
        after_day = extract_call_time("Friday works best", allow_numeric=True)
        merged = extract_call_time(
            "probably 12 would work the best", existing=after_day, allow_numeric=True
        )
        assert merged == "Friday at 12pm"
        assert is_definite_call_time(merged) is True

    def test_silent_turn_retains_known_time(self) -> None:
        # The old `message or state` precedence dropped the time on any turn whose
        # sentence didn't restate it, which re-opened the ask.
        assert extract_call_time("ok thanks", existing="Friday at 12pm") == "Friday at 12pm"

    def test_new_day_overrides_stale_day(self) -> None:
        merged = extract_call_time("actually Friday is better", existing="Wednesday at 12pm")
        assert merged == "Friday at 12pm"

    def test_range_then_pick_resolves_to_definite(self) -> None:
        window = extract_call_time("9-12 works best", existing="Wednesday", allow_numeric=True)
        assert is_definite_call_time(window) is False
        picked = extract_call_time("10:30 works", existing=window, allow_numeric=True)
        assert picked == "Wednesday at 10:30am"
        assert is_definite_call_time(picked) is True


class TestTimezoneIsNotATime:
    """The objective engine's old regex matched bare timezone tokens as times."""

    def test_timezone_answer_is_not_a_call_time(self) -> None:
        assert extract_call_time(
            "my time zone zone is in the central time zone", allow_numeric=True
        ) is None

    def test_bare_direction_word_is_not_a_call_time(self) -> None:
        assert extract_call_time("west coast", allow_numeric=False) is None
