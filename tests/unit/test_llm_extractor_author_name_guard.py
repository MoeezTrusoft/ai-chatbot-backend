"""Regression: a book's author must never be saved as the customer's name.

Bug (chat 5767): the customer answered the title prompt with
"the past & the future written by Thomas ray". The LLM extractor mapped
``name = "Thomas Ray"`` → ``personal.name``, so the bot addressed the customer as
"Thomas", skipped asking for the real name during consultation booking, and pushed
the wrong name downstream. These tests lock the deterministic guard in
``_facts_to_deltas`` (testable without a live LLM) plus the title cleanup.
"""
from __future__ import annotations

from bookcraft.components.extraction.llm_extractor import (
    _facts_to_deltas,
    _name_is_book_authorship,
    _strip_authorship_from_title,
)
from bookcraft.components.extraction.llm_schemas import ExtractedValue, LLMExtractedFacts


class TestNameIsBookAuthorship:
    def test_written_by_quote_is_authorship(self) -> None:
        assert _name_is_book_authorship(
            "Thomas Ray", "the past & the future written by Thomas ray", None
        ) is True

    def test_bare_by_name_quote_is_authorship(self) -> None:
        assert _name_is_book_authorship("Thomas Ray", "a memoir by Thomas Ray", None) is True

    def test_authored_by_is_authorship(self) -> None:
        assert _name_is_book_authorship("Jane Doe", "authored by Jane Doe", None) is True

    def test_name_inside_book_title_is_authorship(self) -> None:
        assert _name_is_book_authorship(
            "Thomas Ray", None, "The Past & The Future written by Thomas Ray"
        ) is True

    def test_genuine_self_identification_is_not_authorship(self) -> None:
        assert _name_is_book_authorship("Gina", "my name is Gina", None) is False

    def test_bare_contact_quote_is_not_authorship(self) -> None:
        assert _name_is_book_authorship("Gina", "Gina gina@example.com", None) is False

    def test_empty_name_is_not_authorship(self) -> None:
        assert _name_is_book_authorship("", "written by someone", None) is False
        assert _name_is_book_authorship(None, None, None) is False

    def test_unrelated_name_not_in_title(self) -> None:
        # Name genuinely given; a different book title exists — must not reject.
        assert _name_is_book_authorship("Sarah", "I'm Sarah", "The Past & The Future") is False


class TestStripAuthorshipFromTitle:
    def test_strips_written_by_clause(self) -> None:
        assert (
            _strip_authorship_from_title("The Past & The Future written by Thomas Ray")
            == "The Past & The Future"
        )

    def test_strips_penned_by(self) -> None:
        assert _strip_authorship_from_title("Midnight penned by A. Cole") == "Midnight"

    def test_does_not_truncate_title_with_bare_by(self) -> None:
        # No authorship verb → leave intact (these are real titles).
        assert _strip_authorship_from_title("Death by Chocolate") == "Death by Chocolate"
        assert _strip_authorship_from_title("Gone by Midnight") == "Gone by Midnight"

    def test_plain_title_unchanged(self) -> None:
        assert _strip_authorship_from_title("The Great Novel") == "The Great Novel"

    def test_non_string_unchanged(self) -> None:
        assert _strip_authorship_from_title(123) == 123
        assert _strip_authorship_from_title(None) is None

    def test_stripping_to_empty_keeps_original(self) -> None:
        # Defensive: if the whole value is an authorship clause, keep it rather than ""
        out = _strip_authorship_from_title("written by Thomas Ray")
        assert out == "written by Thomas Ray"


class TestFactsToDeltasGuard:
    def test_author_name_dropped_and_title_cleaned(self) -> None:
        facts = LLMExtractedFacts(
            name=ExtractedValue(
                value="Thomas Ray", confidence=0.92,
                source_quote="the past & the future written by Thomas ray",
            ),
            book_title=ExtractedValue(
                value="The Past & The Future written by Thomas Ray", confidence=0.92,
                source_quote="the past & the future written by Thomas ray",
            ),
        )
        deltas = _facts_to_deltas(facts)
        paths = {d.path: d.value for d in deltas}

        assert "personal.name" not in paths, "author name must NOT become the customer name"
        assert paths.get("project.title") == "The Past & The Future"

    def test_genuine_name_still_extracted(self) -> None:
        facts = LLMExtractedFacts(
            name=ExtractedValue(value="Gina", confidence=0.92, source_quote="my name is Gina"),
        )
        deltas = _facts_to_deltas(facts)
        paths = {d.path: d.value for d in deltas}
        assert paths.get("personal.name") == "Gina"

    def test_name_in_title_dropped_even_when_title_separate(self) -> None:
        # Model split fields but the name is still the author inside the raw title.
        facts = LLMExtractedFacts(
            name=ExtractedValue(value="Thomas Ray", confidence=0.92, source_quote="Thomas Ray"),
            book_title=ExtractedValue(
                value="The Past & The Future written by Thomas Ray", confidence=0.92,
            ),
        )
        deltas = _facts_to_deltas(facts)
        assert "personal.name" not in {d.path for d in deltas}

    def test_plain_title_no_author_keeps_name(self) -> None:
        # A real self-identified name with an unrelated clean title is untouched.
        facts = LLMExtractedFacts(
            name=ExtractedValue(value="Sarah Khan", confidence=0.92, source_quote="I'm Sarah Khan"),
            book_title=ExtractedValue(value="The Great Novel", confidence=0.9),
        )
        deltas = _facts_to_deltas(facts)
        paths = {d.path: d.value for d in deltas}
        assert paths.get("personal.name") == "Sarah Khan"
        assert paths.get("project.title") == "The Great Novel"
