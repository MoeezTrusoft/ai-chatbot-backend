"""Step 3 unit tests: welcome-first, answer-before-ask, and contact-ask backoff.

These cover the four incident behaviors from the production review:
1. First turn with a high-intent message → must NOT ask for contact.
2. "Tell me how you can help?" → must answer the question, not ask for contact.
3. Turn after a deflected contact ask → must back off, not repeat the ask.
4. Regression: explicit book-a-call / pricing / contact-provided → still routes correctly.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from bookcraft.components.leads.objective import LeadObjectiveEngine
from bookcraft.domain.enums import QueryIntentType
from bookcraft.domain.state import ThreadState


def _intent(
    query: QueryIntentType = QueryIntentType.SERVICE_QUESTION,
    service: str | None = "publishing_distribution",
) -> MagicMock:
    m = MagicMock()
    m.query_primary = query
    m.service_primary = service
    return m


def _state(
    *,
    lead_created: bool = False,
    last_turn_asked_contact: bool = False,
    manuscript_status: str | None = None,
) -> ThreadState:
    s = ThreadState()
    s.lead_created = lead_created
    s.last_turn_asked_contact = last_turn_asked_contact
    if manuscript_status:
        from bookcraft.domain.enums import Source
        from bookcraft.domain.meta import FieldMeta

        s.project.manuscript_status = FieldMeta[str](
            value=manuscript_status,
            confidence=0.9,
            source=Source.USER_STATED,
        )
    return s


def _no_contact() -> MagicMock:
    m = MagicMock()
    m.lead_contact_ready = False
    return m


def _contact_ready() -> MagicMock:
    m = MagicMock()
    m.lead_contact_ready = True
    m.contact = MagicMock()
    m.contact.name = "Maya Author"
    m.contact.email = "maya@example.com"
    m.contact.phone = None
    return m


engine = LeadObjectiveEngine()


# ---------------------------------------------------------------------------
# Incident case 1: first turn must not ask for contact
# ---------------------------------------------------------------------------


def test_first_turn_high_intent_does_not_ask_contact() -> None:
    """Turn 0 with 'I finished my manuscript, just publish it' must not ask for contact."""
    decision = engine.decide(
        message="I finished my manuscript, just publish it",
        intent=_intent(QueryIntentType.SERVICE_QUESTION, "publishing_distribution"),
        state=_state(manuscript_status="completed"),
        contact_capture=_no_contact(),
        turn_count=0,
    )
    assert decision.objective_move != "ask_contact", (
        f"First turn must not ask for contact, got: {decision.objective_move} "
        f"(reason: {decision.reason})"
    )
    assert decision.stop_discovery is False


def test_first_turn_any_service_does_not_ask_contact() -> None:
    """First turn, any service signal — always engage first."""
    decision = engine.decide(
        message="I need help with cover design for my thriller novel",
        intent=_intent(QueryIntentType.SERVICE_QUESTION, "cover_design_illustration"),
        state=_state(),
        contact_capture=_no_contact(),
        turn_count=0,
    )
    assert decision.objective_move != "ask_contact"
    assert decision.recommended_primary_goal == "greeting_welcome"


def test_second_turn_informational_does_not_ask_contact() -> None:
    """Turn 1 with a service/informational intent stays in engage mode."""
    decision = engine.decide(
        message="I need publishing help for my novel",
        intent=_intent(QueryIntentType.SERVICE_QUESTION, "publishing_distribution"),
        state=_state(manuscript_status="completed"),
        contact_capture=_no_contact(),
        turn_count=1,
    )
    assert decision.objective_move != "ask_contact"


# ---------------------------------------------------------------------------
# Incident case 2: answer the user's question before any contact ask
# ---------------------------------------------------------------------------


def test_tell_me_how_you_help_does_not_ask_contact() -> None:
    """'tell me how you can help?' must route to answer_current_question."""
    decision = engine.decide(
        message="tell me how you can help?",
        intent=_intent(QueryIntentType.SERVICE_QUESTION),
        state=_state(),
        contact_capture=_no_contact(),
        turn_count=3,  # beyond first turn
    )
    assert decision.objective_move != "ask_contact", (
        f"Direct question must not trigger contact ask, got: {decision.objective_move}"
    )
    assert decision.recommended_primary_goal in {
        "answer_current_question",
        None,
        "continue_light_discovery",
    }


def test_what_can_you_do_does_not_ask_contact() -> None:
    """'What can you do for my book?' → answer first."""
    decision = engine.decide(
        message="What can you do for my book?",
        intent=_intent(QueryIntentType.SERVICE_QUESTION),
        state=_state(),
        contact_capture=_no_contact(),
        turn_count=2,
    )
    assert decision.objective_move != "ask_contact"


def test_how_does_it_work_does_not_ask_contact() -> None:
    decision = engine.decide(
        message="How does the editing process work?",
        intent=_intent(QueryIntentType.SERVICE_QUESTION, "editing_proofreading"),
        state=_state(),
        contact_capture=_no_contact(),
        turn_count=4,
    )
    assert decision.objective_move != "ask_contact"


# ---------------------------------------------------------------------------
# Incident case 3: back off after a deflected contact ask
# ---------------------------------------------------------------------------


def test_contact_ask_backoff_after_deflection() -> None:
    """When bot asked for contact last turn and user didn't provide it → back off."""
    decision = engine.decide(
        message="Ok ok hold on, tell me more about your services first",
        intent=_intent(QueryIntentType.SERVICE_QUESTION),
        state=_state(last_turn_asked_contact=True),
        contact_capture=_no_contact(),
        turn_count=2,
    )
    assert decision.objective_move != "ask_contact", (
        "Bot must not repeat the contact ask in the very next turn after deflection"
    )
    assert decision.stop_discovery is False


def test_contact_ask_backoff_moves_to_discovery() -> None:
    decision = engine.decide(
        message="I just want to understand the services better",
        intent=_intent(QueryIntentType.SERVICE_QUESTION),
        state=_state(last_turn_asked_contact=True),
        contact_capture=_no_contact(),
        turn_count=3,
    )
    assert decision.objective_move in {
        "continue_light_discovery",
        "answer_then_consultation",
    }


# ---------------------------------------------------------------------------
# Regression: correct paths still fire when appropriate
# ---------------------------------------------------------------------------


def test_explicit_book_a_call_routes_to_contact() -> None:
    """'Book a call' must still trigger the consultation / contact flow."""
    from bookcraft.domain.enums import QueryIntentType

    decision = engine.decide(
        message="book a call",
        intent=_intent(QueryIntentType.CONSULTATION_REQUEST),
        state=_state(),
        contact_capture=_no_contact(),
        turn_count=3,
    )
    assert decision.objective_move in {"ask_contact", "offer_consultation"}, (
        f"Explicit consultation request must still route to contact, got: {decision.objective_move}"
    )


def test_contact_provided_with_explicit_intent_creates_lead() -> None:
    """When contact is provided AND there is explicit lead intent → create lead."""
    decision = engine.decide(
        message="I want to start, my name is Maya maya@example.com",
        intent=_intent(QueryIntentType.READY_TO_BUY),
        state=_state(),
        contact_capture=_contact_ready(),
        turn_count=4,
    )
    assert decision.objective_move == "create_lead"


def test_pricing_question_beyond_first_two_turns_can_ask_contact() -> None:
    """Pricing question on turn 3+ (with no backoff signal) → allowed to ask contact."""
    decision = engine.decide(
        message="How much does ghostwriting cost?",
        intent=_intent(QueryIntentType.PRICING_QUESTION, "ghostwriting"),
        state=_state(last_turn_asked_contact=False),
        contact_capture=_no_contact(),
        turn_count=3,
    )
    # This is a lead-capture intent so it should route to ask_contact
    assert decision.objective_move in {
        "ask_contact",
        "offer_consultation",
        "continue_light_discovery",
    }


def test_first_turn_with_contact_provided_does_not_block_lead() -> None:
    """Even on turn 0, if contact is already provided and there is explicit intent → create lead."""
    decision = engine.decide(
        message="I want to start, Maya Author maya@example.com",
        intent=_intent(QueryIntentType.READY_TO_BUY),
        state=_state(),
        contact_capture=_contact_ready(),
        turn_count=0,
    )
    # contact_ready skips the first-turn guard
    assert decision.objective_move == "create_lead"
