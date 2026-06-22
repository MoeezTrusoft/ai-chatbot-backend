import pytest

from bookcraft.components.extraction.schemas import CombinedExtraction
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.components.response.generator import SonnetResponseGenerator
from bookcraft.domain.enums import QueryIntentType, SalesStage
from bookcraft.domain.state import ThreadState


@pytest.mark.asyncio
async def test_portfolio_pricing_nda_mixed_request_uses_deterministic_guard() -> None:
    generator = SonnetResponseGenerator(adapter=None)

    draft = await generator.generate(
        message=ProcessedMessage(
            raw="I need pricing, samples, and NDA, but do not invent links or numbers.",
            normalized="I need pricing, samples, and NDA, but do not invent links or numbers.",
            language="en",
            tokens=[],
            negation_spans=[],
            hedge_spans=[],
            counterfactual_spans=[],
            deterministic_atoms={},
            embedding=[],
            char_count=len("I need pricing, samples, and NDA, but do not invent links or numbers."),
        ),
        state=ThreadState(),
        intent=IntentVote(
            query_primary=QueryIntentType.PORTFOLIO_REQUEST,
            service_primary=None,
            funnel_stage=SalesStage.NDA_REQUESTED,
            confidence=1.0,
            needs_clarification=False,
            rationale="test",
            evidence=["test"],
        ),
        extraction=CombinedExtraction(),
        rag_chunks=[],
        portfolio_response=None,
        document_status_message=None,
    )

    assert draft.source == "deterministic_mixed_request_guard"
    assert "without guessing or sending anything generic" in draft.text
    assert "service and genre" in draft.text
    assert "word or page count" in draft.text
    assert "author name, email, phone" in draft.text


def test_system_prompt_includes_current_date_and_past_rule() -> None:
    """The bot must be told 'now' and must be forbidden from booking the past."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from bookcraft.components.response.generator import (
        _current_datetime_line,
        _response_system_prompt,
    )

    now = datetime(2026, 6, 22, 15, 14, tzinfo=ZoneInfo("America/Chicago"))

    line = _current_datetime_line(now)
    assert "Monday, June 22, 2026" in line
    assert "2026-06-22" in line
    assert "past" in line.lower()

    prompt = _response_system_prompt(now=now)
    assert "June 22, 2026" in prompt
    # The date block is wired in ahead of the consultation flow.
    assert "Current date and time" in prompt


def test_current_datetime_line_defaults_to_now() -> None:
    """With no explicit 'now', it still renders a valid, non-empty date line."""
    from bookcraft.components.response.generator import _current_datetime_line

    line = _current_datetime_line()
    assert "Current date and time" in line
    assert "Today is" in line


def test_confirmed_consultation_clause_grounds_specialist_and_time() -> None:
    """Once booked, the user prompt pins the exact specialist + date/time (audit C1)."""
    from bookcraft.components.response.generator import _confirmed_consultation_clause
    from bookcraft.domain.state import ThreadState

    state = ThreadState()
    assert _confirmed_consultation_clause(state) == ""  # nothing booked yet

    c = state.sales_actions.consultation
    c.confirmed_appointment_id = "appt-9"
    c.csr_name = "Robert Williams"
    c.confirmed_display_time = "Monday, June 22, 2026 11:00 AM CDT"

    clause = _confirmed_consultation_clause(state)
    assert "Robert Williams" in clause
    assert "Monday, June 22, 2026 11:00 AM CDT" in clause
    assert "do not alter" in clause.lower()
    assert "never invent" in clause.lower()


def test_confirmed_consultation_clause_needs_both_id_and_time() -> None:
    from bookcraft.components.response.generator import _confirmed_consultation_clause
    from bookcraft.domain.state import ThreadState

    state = ThreadState()
    # appointment id without a captured display time should not assert a date
    state.sales_actions.consultation.confirmed_appointment_id = "appt-9"
    assert _confirmed_consultation_clause(state) == ""
