from __future__ import annotations

from bookcraft.components.context.delegation import SlotResolutionStatus
from bookcraft.components.context.pack_builder import ContextPackBuilder
from bookcraft.components.context.schemas import ContextPack
from bookcraft.components.context.slot_tracker import SlotTracker
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.response.planner import ResponsePlanner
from bookcraft.components.response.quality_gate import ResponseQualityGate
from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory, Source
from bookcraft.domain.meta import FieldMeta
from bookcraft.domain.state import ServiceInterest, ThreadState


def _state_with_cover_design() -> ThreadState:
    state = ThreadState()
    state.project.services_discussed.append(
        ServiceInterest(
            service=FieldMeta(
                value=ServiceCategory.COVER_DESIGN_ILLUSTRATION,
                confidence=0.9,
                source=Source.USER_STATED,
                extracted_by="test",
                raw_excerpt="cover design",
            ),
            confidence=0.9,
        )
    )
    return state


def _intent(service: ServiceCategory | None = None) -> IntentVote:
    return IntentVote(
        query_primary=QueryIntentType.SERVICE_QUESTION,
        service_primary=service,
        funnel_stage=SalesStage.SERVICE_DISCOVERY,
        needs_clarification=False,
        confidence=0.9,
        rationale="test",
    )


def _state_with_slot_status(slot: str, status: str) -> ThreadState:
    state = ThreadState()
    state.slot_resolution_statuses = [
        SlotResolutionStatus(
            slot=slot,
            status=status,  # type: ignore[arg-type]
            forbidden_reask=True,
            confidence=0.92,
        ).model_dump(mode="json")
    ]
    return state


# ---------------------------------------------------------------------------
# 1. cover_style delegated after "you decide"
# ---------------------------------------------------------------------------


def test_cover_style_delegated_after_user_says_you_decide() -> None:
    state = _state_with_cover_design()
    pack = ContextPackBuilder().build(
        state=state,
        intent=_intent(ServiceCategory.COVER_DESIGN_ILLUSTRATION),
    )
    tracker = SlotTracker()
    statuses = tracker.update(
        text="You decide, I trust your team.",
        state=state,
        response_plan_next_question="cover_style",
        context_pack=pack,
    )
    assert len(statuses) == 1
    assert statuses[0].slot == "cover_style"
    assert statuses[0].status == "delegated"
    assert statuses[0].forbidden_reask is True


# ---------------------------------------------------------------------------
# 2. word_count unknown after "no idea"
# ---------------------------------------------------------------------------


def test_word_count_unknown_after_no_idea() -> None:
    state = ThreadState()
    tracker = SlotTracker()
    statuses = tracker.update(
        text="I have no idea about the word count.",
        state=state,
        response_plan_next_question="word_or_page_count",
    )
    assert len(statuses) == 1
    assert statuses[0].slot == "word_or_page_count"
    assert statuses[0].status == "unknown_by_user"


# ---------------------------------------------------------------------------
# 3. genre declined after "just show me samples"
# ---------------------------------------------------------------------------


def test_genre_declined_after_just_show_samples() -> None:
    state = ThreadState()
    tracker = SlotTracker()
    statuses = tracker.update(
        text="Just show me samples, I don't know the category.",
        state=state,
        response_plan_next_question="genre",
    )
    # "just show me" matches declined; slot bound to genre
    assert len(statuses) == 1
    assert statuses[0].slot == "genre"
    assert statuses[0].status in ("declined", "unknown_by_user")


# ---------------------------------------------------------------------------
# 4. Delegated slot becomes forbidden_reask in ContextPack
# ---------------------------------------------------------------------------


def test_delegated_slot_becomes_forbidden_reask_in_context_pack() -> None:
    state = _state_with_slot_status("cover_style", "delegated")
    state.project.services_discussed.append(
        ServiceInterest(
            service=FieldMeta(
                value=ServiceCategory.COVER_DESIGN_ILLUSTRATION,
                confidence=0.9,
                source=Source.USER_STATED,
                extracted_by="test",
                raw_excerpt="cover design",
            ),
            confidence=0.9,
        )
    )
    pack = ContextPackBuilder().build(
        state=state,
        intent=_intent(ServiceCategory.COVER_DESIGN_ILLUSTRATION),
    )
    assert "cover_style" in pack.forbidden_reasks
    assert "cover_style" not in pack.missing_facts
    assert "cover_style" in pack.disallowed_next_questions
    assert any(s.slot == "cover_style" for s in pack.delegated_slots)


# ---------------------------------------------------------------------------
# 5. ResponsePlanner does not select delegated slot again
# ---------------------------------------------------------------------------


def test_response_planner_does_not_select_delegated_slot_again() -> None:
    state = _state_with_slot_status("cover_style", "delegated")
    state.project.services_discussed.append(
        ServiceInterest(
            service=FieldMeta(
                value=ServiceCategory.COVER_DESIGN_ILLUSTRATION,
                confidence=0.9,
                source=Source.USER_STATED,
                extracted_by="test",
                raw_excerpt="cover design",
            ),
            confidence=0.9,
        )
    )
    pack = ContextPackBuilder().build(
        state=state,
        intent=_intent(ServiceCategory.COVER_DESIGN_ILLUSTRATION),
    )
    plan = ResponsePlanner().plan(
        intent=_intent(ServiceCategory.COVER_DESIGN_ILLUSTRATION),
        state=state,
        context_pack=pack,
    )
    assert plan.next_question != "cover_style", (
        f"next_question must not be the delegated slot, got {plan.next_question}"
    )


# ---------------------------------------------------------------------------
# 6. Quality gate fails if delegated slot is re-asked
# ---------------------------------------------------------------------------


def test_quality_gate_fails_if_delegated_slot_reasked() -> None:
    pack = ContextPack(
        declined_slots=[
            SlotResolutionStatus(
                slot="cover_style",
                status="delegated",
                forbidden_reask=True,
                confidence=0.92,
            )
        ],
    )
    gate = ResponseQualityGate()
    intent = _intent(ServiceCategory.COVER_DESIGN_ILLUSTRATION)
    report = gate.evaluate(
        text="What cover style or visual direction would you like?",
        intent=intent,
        state=ThreadState(),
        context_pack=pack,
    )
    assert not report.passed
    assert any("delegated_slot_reask" in f for f in report.failures)


# ---------------------------------------------------------------------------
# 7. not_applicable deadline not in missing_facts
# ---------------------------------------------------------------------------


def test_not_applicable_deadline_not_missing() -> None:
    state = _state_with_slot_status("deadline", "not_applicable")
    pack = ContextPackBuilder().build(state=state, intent=_intent())
    assert "deadline" not in pack.missing_facts
    assert "deadline" in pack.forbidden_reasks
