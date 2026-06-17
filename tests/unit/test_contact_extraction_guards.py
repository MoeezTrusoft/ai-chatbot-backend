"""Regression guards for contact extraction, driven by REAL chat transcripts.

Chat 5979 (Ann): the bot captured "(1770-1810)" (a historical period) as a phone
number and "EST" (a timezone) as the customer's name.
Chat 5767 (Thomas Ray): "...I am in central time zone..." would yield a junk name.

These tests use the verbatim customer messages from those transcripts plus the
shared validators, on both the deterministic ContactCaptureDetector and the LLM
extractor's delta guard.
"""
from __future__ import annotations

from bookcraft.components.extraction.llm_extractor import _facts_to_deltas
from bookcraft.components.extraction.llm_schemas import ExtractedValue, LLMExtractedFacts
from bookcraft.components.leads.contact import ContactCaptureDetector
from bookcraft.components.leads.contact_utils import (
    is_non_name_token,
    is_timezone_token,
    is_valid_phone,
    looks_like_year_or_date_range,
)

# ── Verbatim transcript messages ────────────────────────────────────────────
ANN_MSG_1 = (
    "I am looking for a ghost or co-writer for an historic family novel. They must "
    "have experience writing back in the frontier days (1770-1810).  Experience "
    "writing about American Indians is desired.  And, can write about children (6-12) "
    "growing up during this time.  A tough mix of criteria, yet important to "
    "complement my writing."
)
ANN_MSG_TZ_EMAIL = "EST - clifford@safarisolutions.com"
ANN_MSG_NAME_PHONE = "Ann 317-413-4221"
THOMAS_MSG_PHONE_TZ = "205 499 3158 I am in central time zone I have 858p"


class TestSharedValidators:
    def test_timezone_token(self) -> None:
        assert is_timezone_token("EST") is True
        assert is_timezone_token("est.") is True
        assert is_timezone_token("Ann") is False

    def test_non_name_token(self) -> None:
        assert is_non_name_token("EST") is True          # timezone
        assert is_non_name_token("looking for") is True  # filler verb
        assert is_non_name_token("in central time") is True  # leading preposition
        assert is_non_name_token("Ann") is False
        assert is_non_name_token("John Smith") is False

    def test_year_range(self) -> None:
        assert looks_like_year_or_date_range("1770-1810") is True
        assert looks_like_year_or_date_range("1770 to 1810") is True
        assert looks_like_year_or_date_range("317-413-4221") is False

    def test_is_valid_phone(self) -> None:
        assert is_valid_phone("317-413-4221") is True       # 10 digits
        assert is_valid_phone("+1 555 123 4567") is True     # 11 digits
        assert is_valid_phone("1770-1810") is False          # year range, 8 digits
        assert is_valid_phone("6-12") is False               # age range
        assert is_valid_phone("12345") is False              # too short
        assert is_valid_phone("12345678901234567") is False  # too long (>15)


class TestDeterministicTranscriptMessages:
    def setup_method(self) -> None:
        self.d = ContactCaptureDetector()

    def test_period_not_phone_and_filler_not_name(self) -> None:
        r = self.d.extract(ANN_MSG_1)
        assert r.contact.phone is None, "historical period (1770-1810) must not be a phone"
        assert r.contact.name is None, "'looking for ...' must not be a name"

    def test_timezone_not_name_email_kept(self) -> None:
        r = self.d.extract(ANN_MSG_TZ_EMAIL)
        assert r.contact.name is None, "'EST' is a timezone, not a name"
        assert r.contact.email == "clifford@safarisolutions.com"
        assert r.has_email is True

    def test_real_name_and_phone_still_captured(self) -> None:
        r = self.d.extract(ANN_MSG_NAME_PHONE)
        assert r.contact.name == "Ann"
        assert r.contact.phone == "317-413-4221"
        assert r.lead_contact_ready is True

    def test_thomas_phone_kept_timezone_phrase_not_name(self) -> None:
        r = self.d.extract(THOMAS_MSG_PHONE_TZ)
        # The 10-digit phone is valid and captured…
        assert r.contact.phone is not None
        assert sum(c.isdigit() for c in r.contact.phone) >= 10
        # …but "in central time zone" must not become a name.
        assert r.contact.name is None


class TestGenuineContactsUnaffected:
    def setup_method(self) -> None:
        self.d = ContactCaptureDetector()

    def test_structured_name_and_email(self) -> None:
        r = self.d.extract("My name is Sarah Khan and my email is sarah@example.com")
        assert r.contact.name == "Sarah Khan"
        assert r.contact.email == "sarah@example.com"

    def test_bare_block_name_email_phone(self) -> None:
        r = self.d.extract("John Smith john@example.com 5551234567")
        assert r.contact.name == "John Smith"
        assert r.contact.phone == "5551234567"

    def test_formatted_intl_phone(self) -> None:
        r = self.d.extract("I am Sarah Khan. Call me at +1 555 123 4567")
        assert r.contact.name == "Sarah Khan"
        assert r.has_phone is True


class TestDeterministicPreextractorPhone:
    """The deterministic CombinedExtractor must also reject range-shaped 'phones'
    from the preprocessor atoms (the (1770-1810) production bug entered here)."""

    def _msg(self, raw: str, phones: list[str]):
        import pytest as _pytest

        from bookcraft.components.preprocessor.schemas import ProcessedMessage, TokenInfo

        toks = [TokenInfo(text=w, lemma=w.lower(), start=i, end=i + len(w))
                for i, w in enumerate(raw.split())]
        return ProcessedMessage(
            raw=raw, normalized=raw, tokens=toks, negation_spans=[], hedge_spans=[],
            counterfactual_spans=[], deterministic_atoms={"phones": phones}, embedding=[1.0],
            language="en", char_count=len(raw),
        )

    def test_range_atom_rejected(self) -> None:
        import asyncio

        from bookcraft.components.extraction.extractor import CombinedExtractor
        from bookcraft.domain.state import ThreadState

        msg = self._msg("frontier days 1770-1810", ["1770-1810"])
        result = asyncio.run(CombinedExtractor().extract(msg, ThreadState()))
        assert result.contact.phone is None
        assert not any(d.path == "personal.phone" for d in result.state_deltas)

    def test_real_phone_atom_kept(self) -> None:
        import asyncio

        from bookcraft.components.extraction.extractor import CombinedExtractor
        from bookcraft.domain.state import ThreadState

        msg = self._msg("call me at 3174134221", ["3174134221"])
        result = asyncio.run(CombinedExtractor().extract(msg, ThreadState()))
        assert result.contact.phone == "3174134221"
        assert any(d.path == "personal.phone" for d in result.state_deltas)


class TestLLMExtractorGuards:
    def test_timezone_name_dropped(self) -> None:
        facts = LLMExtractedFacts(
            name=ExtractedValue(value="EST", source_quote="EST - clifford@safarisolutions.com"),
            email=ExtractedValue(value="clifford@safarisolutions.com"),
        )
        paths = {d.path: d.value for d in _facts_to_deltas(facts)}
        assert "personal.name" not in paths
        assert paths.get("personal.email") == "clifford@safarisolutions.com"

    def test_period_phone_dropped(self) -> None:
        facts = LLMExtractedFacts(
            phone=ExtractedValue(value="1770-1810", source_quote="frontier days (1770-1810)"),
        )
        assert "personal.phone" not in {d.path for d in _facts_to_deltas(facts)}

    def test_age_range_phone_dropped(self) -> None:
        facts = LLMExtractedFacts(phone=ExtractedValue(value="6-12", source_quote="children (6-12)"))
        assert "personal.phone" not in {d.path for d in _facts_to_deltas(facts)}

    def test_valid_name_and_phone_pass(self) -> None:
        facts = LLMExtractedFacts(
            name=ExtractedValue(value="Ann", source_quote="Ann 317-413-4221"),
            phone=ExtractedValue(value="317-413-4221", source_quote="Ann 317-413-4221"),
        )
        paths = {d.path: d.value for d in _facts_to_deltas(facts)}
        assert paths.get("personal.name") == "Ann"
        assert paths.get("personal.phone") == "317-413-4221"
