from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory
from bookcraft.domain.state import ThreadState
from bookcraft.services.chat import (
    ChatService,
    _active_service_from_state,
    _append_service_focus,
)


def test_active_service_is_retained_when_later_turn_has_no_explicit_service() -> None:
    state = ThreadState()
    _append_service_focus(state, ServiceCategory.COVER_DESIGN_ILLUSTRATION)

    service = ChatService.__new__(ChatService)

    processed = ProcessedMessage(
        raw_text="Its fiction children book as I told you.",
        normalized="Its fiction children book as I told you.",
        language="en",
        embedding=[1.0],
        deterministic_atoms={"genre": "children's fiction"},
    )

    intent = IntentVote(
        query_primary=QueryIntentType.SERVICE_QUESTION,
        service_primary=ServiceCategory.GHOSTWRITING,
        service_secondary=[],
        funnel_stage=SalesStage.SERVICE_DISCOVERY,
        needs_clarification=True,
        confidence=0.74,
        rationale="test",
        evidence=[],
    )

    stabilized = service._stabilize_service_context(
        intent=intent,
        processed=processed,
        state=state,
    )

    assert _active_service_from_state(state) == ServiceCategory.COVER_DESIGN_ILLUSTRATION
    assert stabilized.service_primary == ServiceCategory.COVER_DESIGN_ILLUSTRATION
    assert ServiceCategory.GHOSTWRITING in stabilized.service_secondary
    assert "state_service_inertia" in stabilized.evidence
