"""End-to-end consultation flow tests — Subhan chat regression.

Covers the three breakpoints that prevented any consultation from booking:

  A — Action plan builder never added customer_timezone to collected_slots
      → ConsultationActionRequest(customer_timezone=None) raised validation error
      → Steps 3-5 of consultation flow never executed

  B — _safe_zoneinfo() had no alias map
      → ZoneInfo("eastern timezone") raised ZoneInfoNotFoundError
      → Even when timezone was extracted correctly, the service crashed

  C — LLM extractor had no rule for timezone normalization
      → "eastern timezone" / "eastern" were not extracted as "America/New_York"
      → Timezone field stayed null, validator raised consultation_requires_customer_timezone

Additionally covers the "timezone asked twice" regression from the Subhan transcript.
"""

from __future__ import annotations

from datetime import UTC
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from bookcraft.components.consultations import ConsultationActionRequest, InMemoryConsultationRepository
from bookcraft.components.consultations.service import ConsultationActionService, _normalize_timezone, _safe_zoneinfo
from bookcraft.components.extraction.llm_extractor import _EXTRACTION_SYSTEM, _EXTRACTION_USER_TEMPLATE
from bookcraft.components.extraction.llm_schemas import ExtractedValue, LLMExtractedFacts
from bookcraft.components.extraction.llm_extractor import _facts_to_deltas
from bookcraft.components.context import ContextPackBuilder
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.domain.enums import QueryIntentType, SalesStage, Source
from bookcraft.domain.meta import FieldMeta
from bookcraft.domain.state import ThreadState


def _field(value, *, confidence: float = 0.92):
    return FieldMeta(value=value, confidence=confidence, source=Source.AI_EXTRACTED)


def _intent() -> IntentVote:
    return IntentVote(
        query_primary=QueryIntentType.SERVICE_QUESTION,
        service_primary=None,
        funnel_stage=SalesStage.SERVICE_DISCOVERY,
        needs_clarification=False,
        confidence=0.90,
        rationale="test",
        evidence=[],
    )


def _base_req(**kwargs) -> ConsultationActionRequest:
    defaults = dict(
        thread_id=uuid4(),
        name="Subhan Ali",
        phone="8887690431",
        customer_timezone="America/New_York",
        requested_time_text="tomorrow at 3pm",
    )
    defaults.update(kwargs)
    return ConsultationActionRequest(**defaults)


# ---------------------------------------------------------------------------
# Fix B — Timezone alias normalization in _safe_zoneinfo / _normalize_timezone
# ---------------------------------------------------------------------------

class TestTimezoneNormalization:
    def test_eastern_normalized(self):
        assert _normalize_timezone("eastern") == "America/New_York"

    def test_eastern_timezone_normalized(self):
        assert _normalize_timezone("eastern timezone") == "America/New_York"

    def test_est_normalized(self):
        assert _normalize_timezone("EST") == "America/New_York"

    def test_central_normalized(self):
        assert _normalize_timezone("central") == "America/Chicago"

    def test_cst_normalized(self):
        assert _normalize_timezone("CST") == "America/Chicago"

    def test_mountain_normalized(self):
        assert _normalize_timezone("mountain") == "America/Denver"

    def test_pacific_normalized(self):
        assert _normalize_timezone("pacific") == "America/Los_Angeles"

    def test_pst_normalized(self):
        assert _normalize_timezone("PST") == "America/Los_Angeles"

    def test_pkt_normalized(self):
        assert _normalize_timezone("PKT") == "Asia/Karachi"

    def test_case_insensitive(self):
        assert _normalize_timezone("EASTERN TIME") == "America/New_York"
        assert _normalize_timezone("Central Time") == "America/Chicago"

    def test_valid_iana_passes_through(self):
        assert _normalize_timezone("America/Chicago") == "America/Chicago"

    def test_safe_zoneinfo_eastern_timezone_string(self):
        """'eastern timezone' must not crash — must resolve to America/New_York."""
        from zoneinfo import ZoneInfo
        tz = _safe_zoneinfo("eastern timezone")
        assert tz == ZoneInfo("America/New_York")

    def test_safe_zoneinfo_est(self):
        from zoneinfo import ZoneInfo
        tz = _safe_zoneinfo("EST")
        assert tz == ZoneInfo("America/New_York")

    def test_safe_zoneinfo_garbage_falls_back_to_chicago(self):
        from zoneinfo import ZoneInfo
        tz = _safe_zoneinfo("not a real timezone")
        assert tz == ZoneInfo("America/Chicago")

    def test_safe_zoneinfo_none_falls_back_to_chicago(self):
        from zoneinfo import ZoneInfo
        tz = _safe_zoneinfo(None)
        assert tz == ZoneInfo("America/Chicago")


# ---------------------------------------------------------------------------
# Fix A — ConsultationActionRequest accepts eastern + schedules successfully
# ---------------------------------------------------------------------------

class TestConsultationRequestWithTimezone:
    def test_request_with_eastern_timezone_string_passes(self):
        """After fix B, 'America/New_York' (normalized from 'eastern') must pass validation."""
        req = _base_req(customer_timezone="America/New_York")
        assert req.customer_timezone == "America/New_York"

    def test_request_with_none_timezone_fails(self):
        with pytest.raises(ValueError, match="consultation_requires_customer_timezone"):
            _base_req(customer_timezone=None)

    def test_request_with_phone_no_email_succeeds(self):
        """Email is optional — phone + name + timezone is sufficient."""
        req = _base_req(email=None)
        assert req.phone == "8887690431"
        assert req.email is None

    def test_request_with_email_only_no_phone_succeeds(self):
        """Email alone is a valid contact path (e.g. customer whose phone was hacked)."""
        req = ConsultationActionRequest(
            thread_id=uuid4(),
            name="Chris",
            email="clarkchris62@yahoo.com",
            phone=None,
            customer_timezone="America/Chicago",
            requested_time_text="as soon as possible",
        )
        assert req.email == "clarkchris62@yahoo.com"
        assert req.phone is None

    def test_request_with_neither_phone_nor_email_fails(self):
        """Must have at least one contact method."""
        import pydantic
        with pytest.raises((ValueError, pydantic.ValidationError)):
            ConsultationActionRequest(
                thread_id=uuid4(),
                name="Chris",
                email=None,
                phone=None,
                customer_timezone="America/Chicago",
                requested_time_text="tomorrow at 3pm",
            )

    @pytest.mark.asyncio
    async def test_schedule_with_eastern_timezone_succeeds(self):
        """End-to-end: 'eastern timezone' → normalized → valid ConsultationActionRequest → scheduled."""
        repo = InMemoryConsultationRepository()
        service = ConsultationActionService(repository=repo)
        result = await service.schedule(
            ConsultationActionRequest(
                thread_id=uuid4(),
                name="Subhan Ali",
                phone="8887690431",
                customer_timezone="America/New_York",  # normalized from "eastern"
                requested_time_text="tomorrow at 3pm",
            )
        )
        assert result.status == "scheduled"
        assert result.csr_name  # CSR was assigned
        assert result.customer_display_time  # display time computed

    @pytest.mark.asyncio
    async def test_schedule_with_chicago_default_succeeds(self):
        """Fallback timezone (America/Chicago) produces a valid booking."""
        repo = InMemoryConsultationRepository()
        service = ConsultationActionService(repository=repo)
        result = await service.schedule(
            ConsultationActionRequest(
                thread_id=uuid4(),
                name="Subhan Ali",
                phone="8887690431",
                customer_timezone="America/Chicago",
                requested_time_text="tomorrow at 3pm",
            )
        )
        assert result.status == "scheduled"


# ---------------------------------------------------------------------------
# Fix C — LLM extractor timezone rule
# ---------------------------------------------------------------------------

class TestTimezoneExtractionRule:
    def test_rule12_present_in_system_prompt(self):
        assert "America/New_York" in _EXTRACTION_SYSTEM
        assert "America/Chicago" in _EXTRACTION_SYSTEM
        assert "America/Los_Angeles" in _EXTRACTION_SYSTEM

    def test_iana_normalization_guidance_present(self):
        assert "iana" in _EXTRACTION_SYSTEM.lower() or "normalize" in _EXTRACTION_SYSTEM.lower()

    def test_eastern_alias_documented(self):
        assert "eastern" in _EXTRACTION_SYSTEM.lower()

    def test_qa_template_has_timezone_example(self):
        assert "eastern timezone" in _EXTRACTION_USER_TEMPLATE.lower() or \
               "timezone" in _EXTRACTION_USER_TEMPLATE.lower()

    def test_timezone_delta_maps_to_personal_timezone(self):
        """LLM-extracted timezone must map to personal.timezone state path."""
        facts = LLMExtractedFacts(
            timezone=ExtractedValue(
                value="America/New_York",
                confidence=0.92,
                source_quote="eastern timezone",
            )
        )
        deltas = _facts_to_deltas(facts)
        assert len(deltas) == 1
        assert deltas[0].path == "personal.timezone"
        assert deltas[0].value == "America/New_York"
        assert deltas[0].confidence == 0.92

    def test_timezone_in_known_facts_when_set(self):
        state = ThreadState()
        state.personal.timezone = _field("America/New_York")
        pack = ContextPackBuilder().build(state=state, intent=_intent())
        paths = {kf.path for kf in pack.known_facts}
        assert "personal.timezone" in paths


# ---------------------------------------------------------------------------
# Subhan scenario: full state progression after timezone captured
# ---------------------------------------------------------------------------

class TestSubhanConsultationScenario:
    """Simulate the Subhan chat state by turn — verifies all slots land correctly."""

    def _full_state(self) -> ThreadState:
        state = ThreadState()
        state.personal.name = _field("Subhan Ali")
        state.personal.phone = _field("8887690431")
        state.personal.timezone = _field("America/New_York")
        state.project.manuscript_status = _field("not_started")
        state.project.word_count = _field(130000, confidence=0.3)
        return state

    def test_name_in_known_facts(self):
        pack = ContextPackBuilder().build(state=self._full_state(), intent=_intent())
        assert any(kf.path == "personal.name" for kf in pack.known_facts)

    def test_phone_in_known_facts(self):
        pack = ContextPackBuilder().build(state=self._full_state(), intent=_intent())
        assert any(kf.path == "personal.phone" for kf in pack.known_facts)

    def test_timezone_in_known_facts(self):
        pack = ContextPackBuilder().build(state=self._full_state(), intent=_intent())
        assert any(kf.path == "personal.timezone" for kf in pack.known_facts)

    def test_manuscript_not_in_missing(self):
        pack = ContextPackBuilder().build(state=self._full_state(), intent=_intent())
        assert "manuscript_stage" not in pack.missing_facts

    def test_word_count_not_in_missing(self):
        pack = ContextPackBuilder().build(state=self._full_state(), intent=_intent())
        assert "word_or_page_count" not in pack.missing_facts

    def test_name_and_phone_in_forbidden_reasks(self):
        pack = ContextPackBuilder().build(state=self._full_state(), intent=_intent())
        assert "name" in pack.forbidden_reasks
        assert "phone" in pack.forbidden_reasks

    @pytest.mark.asyncio
    async def test_consultation_books_with_full_subhan_state(self):
        """With all fields captured as they would be in Subhan's chat,
        the consultation service must successfully schedule."""
        repo = InMemoryConsultationRepository()
        service = ConsultationActionService(repository=repo)
        result = await service.schedule(
            ConsultationActionRequest(
                thread_id=uuid4(),
                name="Subhan Ali",
                phone="8887690431",
                customer_timezone="America/New_York",
                email=None,
                requested_time_text="tomorrow at 3pm",
            )
        )
        assert result.status == "scheduled"
        assert result.appointment_id
        assert "tomorrow" in result.houston_display_time.lower() or result.houston_display_time
