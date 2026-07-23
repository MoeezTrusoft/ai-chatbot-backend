"""Regression guards for false extractions found in the extraction audit.

- Genre had NO negation guard: "not a children's book" extracted the genre.
- Phone validation was digit-count-only: ISBNs/SKUs were stored as phone numbers.
"""

from __future__ import annotations

from bookcraft.components.leads.contact_utils import is_identifier_number, is_valid_phone
from bookcraft.components.preprocessor.detectors.genre_detector import detect_genre


def test_genre_positive_still_detected() -> None:
    assert detect_genre("it's a fantasy novel") == "fantasy"


def test_genre_negation_suppressed() -> None:
    # detect_genre now honors negation (via the shared prefix/span machinery) — a
    # negated genre must not be extracted and mis-scope the conversation.
    for text in [
        "definitely not fantasy",
        "it's not a children's book",
        "no romance in it",
        "I don't want a thriller",
    ]:
        assert detect_genre(text) is None, text


def test_isbn13_not_a_phone() -> None:
    assert is_valid_phone("978-3-16-148410-0") is False
    assert is_valid_phone("9783161484100") is False


def test_real_phone_still_valid() -> None:
    assert is_valid_phone("(415) 555-2671") is True
    assert is_valid_phone("+1 415 555 2671") is True


def test_identifier_context_number_rejected() -> None:
    for text in [
        "My ISBN is 978-3-16-148410-0",
        "SKU 100200300400",
        "order number 4045551234567",
        "reference # 4045551234567",
    ]:
        # The number's start index (first digit run).
        import re

        m = re.search(r"\d", text)
        assert m is not None
        assert is_identifier_number(text, m.start()) is True, text


def test_plain_phone_context_not_flagged_as_identifier() -> None:
    text = "call me at 4155552671"
    import re

    m = re.search(r"\d", text)
    assert m is not None
    assert is_identifier_number(text, m.start()) is False
