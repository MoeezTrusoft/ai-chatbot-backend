from bookcraft.components.context import ContextPackBuilder
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.trg.schemas import TRGContext
from bookcraft.domain.enums import (
    ManuscriptStatus,
    QueryIntentType,
    SalesStage,
    ServiceCategory,
    Source,
)
from bookcraft.domain.meta import FieldMeta
from bookcraft.domain.state import ServiceInterest, ThreadState


def test_context_pack_for_cover_design_finished_children_fiction() -> None:
    state = ThreadState()
    state.project.genre = _field("children's fiction", confidence=0.91)
    state.project.manuscript_status = _field(ManuscriptStatus.COMPLETED_DRAFT)
    state.project.services_discussed.append(
        ServiceInterest(
            service=_field(ServiceCategory.COVER_DESIGN_ILLUSTRATION),
            confidence=0.94,
        )
    )

    pack = ContextPackBuilder().build(
        state=state,
        intent=_intent(service=ServiceCategory.COVER_DESIGN_ILLUSTRATION),
    )

    assert pack.active_service == "cover_design_illustration"
    assert pack.active_genre == "children's fiction"
    assert pack.manuscript_status == "completed_draft"

    known_paths = {fact.path for fact in pack.known_facts}
    assert "project.genre" in known_paths
    assert "project.manuscript_status" in known_paths

    assert "word_or_page_count" in pack.missing_facts
    assert "cover_style" in pack.missing_facts

    assert "genre" in pack.forbidden_reasks
    assert "manuscript_stage" in pack.forbidden_reasks or "draft status" in pack.forbidden_reasks

    assert pack.response_hint is not None
    hint = pack.response_hint.casefold()
    assert "do not ask" in hint or "don't ask" in hint


def test_context_pack_empty_state_has_basic_missing_facts() -> None:
    pack = ContextPackBuilder().build(state=ThreadState(), intent=_intent())

    assert "genre" in pack.missing_facts
    assert "manuscript_stage" in pack.missing_facts
    assert "word_or_page_count" in pack.missing_facts


def test_context_pack_does_not_mark_word_or_page_count_missing_when_word_count_exists() -> None:
    state = ThreadState()
    state.project.word_count = _field(50000)

    pack = ContextPackBuilder().build(state=state, intent=_intent())

    assert "word_or_page_count" not in pack.missing_facts


def test_context_pack_does_not_mark_word_or_page_count_missing_when_page_count_exists() -> None:
    state = ThreadState()
    state.project.page_count = _field(220)

    pack = ContextPackBuilder().build(state=state, intent=_intent())

    assert "word_or_page_count" not in pack.missing_facts


def test_context_pack_includes_trg_outstanding_questions() -> None:
    trg_context = TRGContext(
        outstanding_questions=["What genre is the book?"],
        repeated_user_messages=["children book"],
        contradiction_count=1,
    )

    pack = ContextPackBuilder().build(
        state=ThreadState(),
        intent=_intent(),
        trg_context=trg_context,
    )

    assert "What genre is the book?" in pack.outstanding_questions
    assert pack.repeated_user_info == ["children book"]
    assert pack.contradiction_warnings == ["trg_contradiction_warning"]


def test_context_pack_pricing_intent_requires_deadline() -> None:
    pack = ContextPackBuilder().build(
        state=ThreadState(),
        intent=_intent(query=QueryIntentType.PRICING_QUESTION),
    )

    assert "deadline" in pack.missing_facts


def _intent(
    *,
    query: QueryIntentType = QueryIntentType.SERVICE_QUESTION,
    service: ServiceCategory | None = None,
) -> IntentVote:
    return IntentVote(
        query_primary=query,
        service_primary=service,
        funnel_stage=SalesStage.SERVICE_DISCOVERY,
        needs_clarification=True,
        confidence=0.9,
        rationale="test",
        evidence=["test"],
    )


def _field[T](value: T, *, confidence: float = 0.9) -> FieldMeta[T]:
    return FieldMeta(
        value=value,
        confidence=confidence,
        source=Source.USER_STATED,
        extracted_by="test",
        raw_excerpt=str(value),
    )
