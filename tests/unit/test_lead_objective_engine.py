from bookcraft.components.attachments.intake import AttachmentIntakeResult
from bookcraft.components.context.schemas import ContextPack
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.leads.contact import ContactCaptureDetector
from bookcraft.components.leads.objective import LeadObjectiveEngine
from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory
from bookcraft.domain.state import ThreadState


def _intent(query: QueryIntentType, service: ServiceCategory | None = None) -> IntentVote:
    return IntentVote(
        query_primary=query,
        service_primary=service,
        funnel_stage=SalesStage.SERVICE_DISCOVERY,
        needs_clarification=False,
        confidence=0.9,
        rationale="test",
        evidence=[],
    )


def test_service_request_moves_to_contact_capture() -> None:
    d = LeadObjectiveEngine().decide(
        message="I need editing help",
        intent=_intent(QueryIntentType.SERVICE_QUESTION, ServiceCategory.EDITING_PROOFREADING),
        state=ThreadState(),
    )
    assert d.objective_move in {"continue_light_discovery", "ask_contact"}


def test_pricing_request_moves_to_contact_capture_not_quote_loop() -> None:
    # turn_count=2: pricing intent on non-first turns routes to contact capture.
    d = LeadObjectiveEngine().decide(
        message="How much does ghostwriting cost?",
        intent=_intent(QueryIntentType.PRICING_QUESTION, ServiceCategory.GHOSTWRITING),
        state=ThreadState(),
        turn_count=2,
    )
    assert d.stop_discovery is True
    assert d.objective_move == "ask_contact"


def test_attachment_assessment_moves_to_specialist_handoff() -> None:
    ai = AttachmentIntakeResult(assessment_type="editorial_assessment", specialist_role="editor")
    d = LeadObjectiveEngine().decide(
        message="Please check my manuscript",
        intent=_intent(QueryIntentType.SERVICE_QUESTION, ServiceCategory.EDITING_PROOFREADING),
        state=ThreadState(),
        attachment_intake=ai,
    )
    assert d.stop_discovery is True
    assert d.next_question == "name_and_email_or_phone"


def test_contact_ready_moves_to_create_lead() -> None:
    cc = ContactCaptureDetector().extract("my name is Sarah Khan and email sarah@example.com")
    d = LeadObjectiveEngine().decide(
        message="my name is Sarah Khan and email sarah@example.com",
        intent=_intent(QueryIntentType.CONTACT_INFO_PROVIDED),
        state=ThreadState(),
        contact_capture=cc,
    )
    assert d.objective_move == "create_lead"


def test_flexible_discretion_moves_to_consultation_handoff() -> None:
    class _F:
        detected = True
        mode = "bookcraft_discretion"

    # turn_count=2: discretion delegation on non-first turns routes to consultation.
    d = LeadObjectiveEngine().decide(
        message="you decide what is best",
        intent=_intent(QueryIntentType.SERVICE_QUESTION),
        state=ThreadState(),
        flexible_intent=_F(),
        turn_count=2,
    )
    assert d.objective_move == "offer_consultation"


def test_lead_created_stops_discovery() -> None:
    s = ThreadState(lead_created=True)
    d = LeadObjectiveEngine().decide(
        message="thanks",
        intent=_intent(QueryIntentType.SERVICE_QUESTION),
        state=s,
    )
    assert d.stop_discovery is True
    assert d.stage == "lead_created"


def test_light_unknown_message_continues_discovery() -> None:
    d = LeadObjectiveEngine().decide(
        message="okay",
        intent=_intent(QueryIntentType.GREETING),
        state=ThreadState(),
        context_pack=ContextPack(),
    )
    assert d.objective_move == "continue_light_discovery"
