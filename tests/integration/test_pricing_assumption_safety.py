from uuid import uuid4

import pytest

from bookcraft.api.main import build_chat_service
from bookcraft.domain.enums import ServiceCategory
from bookcraft.domain.meta import FieldMeta
from bookcraft.domain.state import ThreadState
from bookcraft.infra.config import Settings


def state_with_word_count(word_count: int = 50000) -> ThreadState:
    state = ThreadState()
    state.project.word_count = FieldMeta[int](
        value=word_count,
        confidence=0.95,
        source="user_stated",
        raw_excerpt=f"{word_count} words",
    )
    return state


@pytest.mark.asyncio
async def test_price_turn_requires_confirmation_before_hidden_ghostwriting_defaults() -> None:
    service = build_chat_service(Settings(app_env="test"))
    state = state_with_word_count()

    quote, timeline, question = await service._price_turn(
        thread_id=uuid4(),
        customer_id=None,
        turn_sequence=1,
        correlation_id="pricing-safety-1",
        state=state,
        intent_service=ServiceCategory.GHOSTWRITING,
        message="How much for ghostwriting a 50000 word book?",
        confidence=0.95,
    )

    assert quote is None
    assert timeline is None
    assert question is not None
    lowered = question.lower()
    assert "confirm" in lowered
    assert "hidden assumptions" in lowered
    assert "ghostwriting scope" in lowered
    assert "manuscript status" in lowered
    assert "genre" in lowered


@pytest.mark.asyncio
async def test_price_turn_blocks_silent_cover_complexity_defaults() -> None:
    service = build_chat_service(Settings(app_env="test"))
    state = state_with_word_count()

    quote, timeline, question = await service._price_turn(
        thread_id=uuid4(),
        customer_id=None,
        turn_sequence=1,
        correlation_id="pricing-safety-2",
        state=state,
        intent_service=ServiceCategory.COVER_DESIGN_ILLUSTRATION,
        message="What is the cost for a book cover?",
        confidence=0.95,
    )

    assert quote is None
    assert timeline is None
    assert question is not None
    lowered = question.lower()
    assert "confirm" in lowered
    assert "cover complexity" in lowered
    assert "cover scope" in lowered


@pytest.mark.asyncio
async def test_price_turn_with_explicit_inputs_reaches_engine_gate() -> None:
    service = build_chat_service(Settings(app_env="test"))
    state = state_with_word_count()

    quote, timeline, question = await service._price_turn(
        thread_id=uuid4(),
        customer_id=None,
        turn_sequence=1,
        correlation_id="pricing-safety-3",
        state=state,
        intent_service=ServiceCategory.GHOSTWRITING,
        message=(
            "How much for full ghostwriting from scratch for a fiction book, "
            "50000 words, outline ready?"
        ),
        confidence=0.95,
    )

    assert timeline is None
    assert question is None
    assert quote is not None
    assert any(warning.code == "VALUES_NOT_APPROVED" for warning in quote.warnings)
    assert quote.audit_trace["blocked_reason"] == "pricing_values_not_approved"
