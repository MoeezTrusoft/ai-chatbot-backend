from __future__ import annotations

import pytest
from pydantic import BaseModel

from bookcraft.components.intent import LLMIntentProvider
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.domain.enums import QueryIntentType, SalesStage
from bookcraft.domain.state import ThreadState


class FakeStructuredAdapter:
    name = "fake"

    async def structured(
        self,
        *,
        system: str,
        user: str,
        output_model: type[BaseModel],
        purpose: str,
    ) -> BaseModel:
        assert "Do not call tools" in system
        assert "Normalized message" in user
        assert purpose == "intent"
        return output_model.model_validate(
            {
                "query_primary": "pricing_question",
                "query_secondary": [],
                "service_primary": None,
                "service_secondary": [],
                "funnel_stage": "quote_requested",
                "needs_clarification": True,
                "confidence": 0.74,
                "rationale": "test",
                "evidence": ["test"],
            }
        )


@pytest.mark.asyncio
async def test_live_intent_provider_uses_structured_adapter() -> None:
    provider = LLMIntentProvider(name="fake_provider", adapter=FakeStructuredAdapter())
    message = ProcessedMessage(
        raw="How much?",
        normalized="How much?",
        tokens=[],
        negation_spans=[],
        hedge_spans=[],
        counterfactual_spans=[],
        deterministic_atoms={},
        embedding=[],
        language="en",
        char_count=9,
    )

    vote = await provider.classify(message, ThreadState())

    assert isinstance(vote, IntentVote)
    assert vote.query_primary == QueryIntentType.PRICING_QUESTION
    assert vote.funnel_stage == SalesStage.QUOTE_REQUESTED
