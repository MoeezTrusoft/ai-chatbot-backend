from __future__ import annotations

from bookcraft.components.context.delegation import SlotResolutionStatus
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.portfolio.fallback_policy import PortfolioFallbackPolicy
from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory, Source
from bookcraft.domain.meta import FieldMeta
from bookcraft.domain.state import ServiceInterest, ThreadState

_policy = PortfolioFallbackPolicy()


def _intent(
    service: ServiceCategory | None = None,
    query: QueryIntentType = QueryIntentType.PORTFOLIO_REQUEST,
) -> IntentVote:
    return IntentVote(
        query_primary=query,
        service_primary=service,
        funnel_stage=SalesStage.SERVICE_DISCOVERY,
        needs_clarification=False,
        confidence=0.9,
        rationale="test",
    )


def _state() -> ThreadState:
    return ThreadState()


def _state_with_service(service: ServiceCategory) -> ThreadState:
    state = ThreadState()
    state.project.services_discussed.append(
        ServiceInterest(
            service=FieldMeta(
                value=service,
                confidence=0.9,
                source=Source.USER_STATED,
                extracted_by="test",
                raw_excerpt="cover design",
            ),
            confidence=0.9,
        )
    )
    return state


def _state_with_declined_genre() -> ThreadState:
    state = ThreadState()
    state.slot_resolution_statuses = [
        SlotResolutionStatus(
            slot="genre",
            status="declined",
            forbidden_reask=True,
            confidence=0.85,
        ).model_dump(mode="json")
    ]
    return state


def _state_with_fallback_allowed() -> ThreadState:
    state = ThreadState()
    state.portfolio_filter_state = {"asked_count": 1, "fallback_allowed": True, "declined": False}
    return state


# ---------------------------------------------------------------------------
# 1. First sample request without context → ask_filter_once
# ---------------------------------------------------------------------------


def test_first_sample_request_without_context_asks_filter_once() -> None:
    d = _policy.decide(
        message="Show me samples.",
        intent=_intent(),
        state=_state(),
    )
    assert d is not None
    assert d.strategy == "ask_filter_once"
    assert d.reason == "portfolio_filter_missing_first_request"


# ---------------------------------------------------------------------------
# 2. Second request after unknown → general fallback
# ---------------------------------------------------------------------------


def test_second_sample_request_after_unknown_uses_general_fallback() -> None:
    state = _state_with_fallback_allowed()
    d = _policy.decide(
        message="Show me samples.",
        intent=_intent(),
        state=state,
    )
    assert d is not None
    assert d.strategy == "fallback_general_samples"


# ---------------------------------------------------------------------------
# 3. Active service → service fallback
# ---------------------------------------------------------------------------


def test_active_service_uses_service_fallback() -> None:
    state = _state_with_service(ServiceCategory.COVER_DESIGN_ILLUSTRATION)
    state.portfolio_filter_state = {"asked_count": 1, "fallback_allowed": True}
    d = _policy.decide(
        message="I don't know, just show me samples.",
        intent=_intent(ServiceCategory.COVER_DESIGN_ILLUSTRATION),
        state=state,
    )
    assert d is not None
    assert d.strategy == "fallback_service_samples"
    assert d.filters.get("service") == "cover_design_illustration"


# ---------------------------------------------------------------------------
# 4. Known genre → use context filter
# ---------------------------------------------------------------------------


def test_known_genre_uses_context_filter() -> None:
    from bookcraft.components.context.schemas import ContextPack

    state = _state_with_service(ServiceCategory.COVER_DESIGN_ILLUSTRATION)
    pack = ContextPack(
        active_service="cover_design_illustration",
        active_genre="fantasy",
    )
    d = _policy.decide(
        message="Show me cover samples.",
        intent=_intent(ServiceCategory.COVER_DESIGN_ILLUSTRATION),
        state=state,
        context_pack=pack,
    )
    assert d is not None
    assert d.strategy == "use_context_filter"
    assert d.filters.get("service") == "cover_design_illustration"
    assert d.filters.get("genre") == "fantasy"


# ---------------------------------------------------------------------------
# 5. Delegated category → fallback allowed
# ---------------------------------------------------------------------------


def test_delegated_category_allows_fallback() -> None:
    state = _state_with_service(ServiceCategory.COVER_DESIGN_ILLUSTRATION)
    state.slot_resolution_statuses = [
        SlotResolutionStatus(
            slot="genre",
            status="delegated",
            forbidden_reask=True,
            confidence=0.9,
        ).model_dump(mode="json")
    ]
    d = _policy.decide(
        message="Show me samples.",
        intent=_intent(ServiceCategory.COVER_DESIGN_ILLUSTRATION),
        state=state,
    )
    assert d is not None
    assert d.strategy == "fallback_service_samples"
    assert "genre" not in d.filters


# ---------------------------------------------------------------------------
# 6. Declined category → fallback allowed
# ---------------------------------------------------------------------------


def test_declined_category_allows_fallback() -> None:
    state = _state_with_declined_genre()
    d = _policy.decide(
        message="Show me samples please.",
        intent=_intent(),
        state=state,
    )
    assert d is not None
    assert d.strategy == "fallback_general_samples"
    assert d.reason == "user_declined_filter"


# ---------------------------------------------------------------------------
# 7. Insistence phrase triggers fallback
# ---------------------------------------------------------------------------


def test_insistence_phrase_allows_fallback() -> None:
    state = _state()
    state.portfolio_filter_state = {"asked_count": 0}
    d = _policy.decide(
        message="I don't know, just show me any samples.",
        intent=_intent(),
        state=state,
    )
    assert d is not None
    assert d.strategy in ("fallback_general_samples", "fallback_service_samples")


# ---------------------------------------------------------------------------
# 8. Filters do not include declined/unknown slot
# ---------------------------------------------------------------------------


def test_filters_do_not_include_unknown_or_declined_slot() -> None:
    state = _state_with_service(ServiceCategory.EDITING_PROOFREADING)
    state.slot_resolution_statuses = [
        SlotResolutionStatus(
            slot="genre",
            status="unknown_by_user",
            forbidden_reask=True,
        ).model_dump(mode="json")
    ]
    d = _policy.decide(
        message="Show me editing samples.",
        intent=_intent(ServiceCategory.EDITING_PROOFREADING),
        state=state,
    )
    assert d is not None
    assert "genre" not in d.filters, f"Declined genre must not appear in filters, got {d.filters}"


# ---------------------------------------------------------------------------
# Extra: non-portfolio message returns None
# ---------------------------------------------------------------------------


def test_non_portfolio_message_returns_none() -> None:
    d = _policy.decide(
        message="I need ghostwriting for my fantasy novel.",
        intent=_intent(query=QueryIntentType.SERVICE_QUESTION),
        state=_state(),
    )
    assert d is None
