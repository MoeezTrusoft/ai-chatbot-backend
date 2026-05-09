from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from bookcraft.components.intent import DecisionLayer, EnsembleIntentClassifier, MockIntentProvider
from bookcraft.components.intent.schemas import IntentProviderStatus, IntentVote, ProviderIntentVote
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.components.trimatch.schemas import TriMatchMode, TriMatchResult
from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory
from bookcraft.domain.state import ThreadState


def processed(text: str) -> ProcessedMessage:
    return ProcessedMessage(
        raw=text,
        normalized=text.lower(),
        tokens=[],
        negation_spans=[],
        hedge_spans=[],
        counterfactual_spans=[],
        deterministic_atoms={"services": ["ghostwriting"]} if "ghostwriting" in text else {},
        embedding=[],
        language="en",
        char_count=len(text),
    )


@dataclass(slots=True)
class StaticProvider:
    name: str
    vote: IntentVote

    async def classify(self, message: ProcessedMessage, state: ThreadState) -> IntentVote:
        del message, state
        return self.vote


@dataclass(slots=True)
class SlowProvider:
    name: str = "slow_provider"

    async def classify(self, message: ProcessedMessage, state: ThreadState) -> IntentVote:
        del message, state
        await asyncio.sleep(0.1)
        return vote(QueryIntentType.SERVICE_QUESTION, SalesStage.SERVICE_DISCOVERY)


@dataclass(slots=True)
class FailingProvider:
    name: str = "failing_provider"

    async def classify(self, message: ProcessedMessage, state: ThreadState) -> IntentVote:
        del message, state
        raise RuntimeError("provider down")


def vote(
    query: QueryIntentType,
    stage: SalesStage,
    *,
    service: ServiceCategory | None = None,
    confidence: float = 0.9,
) -> IntentVote:
    return IntentVote(
        query_primary=query,
        service_primary=service,
        funnel_stage=stage,
        needs_clarification=query
        in {QueryIntentType.PRICING_QUESTION, QueryIntentType.TIMELINE_QUESTION},
        confidence=confidence,
        rationale="test",
        evidence=["test"],
    )


@pytest.mark.asyncio
async def test_ensemble_collects_three_provider_votes_and_decides() -> None:
    classifier = EnsembleIntentClassifier(
        providers=[
            MockIntentProvider("claude_haiku"),
            MockIntentProvider("openai_gpt_5_4_mini"),
            MockIntentProvider("deepseek_v3"),
        ],
        decision_layer=DecisionLayer(),
        timeout_seconds=1.0,
    )

    result = await classifier.classify(processed("how much does ghostwriting cost"), ThreadState())

    assert result.query_primary == QueryIntentType.PRICING_QUESTION
    assert result.service_primary == ServiceCategory.GHOSTWRITING
    assert classifier.last_decision is not None
    assert len(classifier.last_decision.provider_votes) == 3
    assert {
        provider_vote.provider for provider_vote in classifier.last_decision.provider_votes
    } == {"claude_haiku", "openai_gpt_5_4_mini", "deepseek_v3"}
    assert "provider_query_quorum:pricing_question" in classifier.last_decision.audit_trail


def test_decision_layer_keeps_trimatch_funnel_stage_shadow_weight_zero() -> None:
    provider_vote = ProviderIntentVote(
        provider="claude_haiku",
        status=IntentProviderStatus.SUCCEEDED,
        vote=vote(QueryIntentType.SERVICE_QUESTION, SalesStage.SERVICE_DISCOVERY),
    )
    trimatch = TriMatchResult(
        funnel_stage=SalesStage.QUOTE_REQUESTED,
        confidence=1.0,
        mode=TriMatchMode.SHADOW,
        shadow_only_dimensions=["funnel_stage"],
    )

    result = DecisionLayer(trimatch_funnel_stage_weight=0.0).decide(
        provider_votes=[provider_vote],
        trimatch_result=trimatch,
    )

    assert result.final_vote.funnel_stage == SalesStage.SERVICE_DISCOVERY
    assert "trimatch_funnel_stage_shadow_weight_zero" in result.audit_trail


def test_decision_layer_uses_trimatch_when_all_providers_fail() -> None:
    trimatch = TriMatchResult(
        query_primary=QueryIntentType.PRICING_QUESTION,
        service_primary=ServiceCategory.GHOSTWRITING,
        funnel_stage=SalesStage.QUOTE_REQUESTED,
        confidence=0.88,
        mode=TriMatchMode.SHADOW,
        shadow_only_dimensions=["funnel_stage"],
    )

    result = DecisionLayer(trimatch_funnel_stage_weight=0.0).decide(
        provider_votes=[
            ProviderIntentVote(
                provider="claude_haiku",
                status=IntentProviderStatus.FAILED,
                error="schema",
            )
        ],
        trimatch_result=trimatch,
    )

    assert result.final_vote.query_primary == QueryIntentType.PRICING_QUESTION
    assert result.final_vote.service_primary == ServiceCategory.GHOSTWRITING
    assert result.final_vote.funnel_stage == SalesStage.NEW
    assert "trimatch_query_service_fallback" in result.audit_trail
    assert "trimatch_funnel_stage_shadow_weight_zero" in result.audit_trail


@pytest.mark.asyncio
async def test_ensemble_timeout_and_provider_down_still_decide_from_remaining_vote() -> None:
    classifier = EnsembleIntentClassifier(
        providers=[
            StaticProvider(
                "claude_haiku",
                vote(QueryIntentType.PRICING_QUESTION, SalesStage.QUOTE_REQUESTED),
            ),
            SlowProvider(),
            FailingProvider(),
        ],
        decision_layer=DecisionLayer(),
        timeout_seconds=0.01,
    )

    result = await classifier.classify(processed("quote please"), ThreadState())

    assert result.query_primary == QueryIntentType.PRICING_QUESTION
    assert classifier.last_decision is not None
    statuses = {vote.status for vote in classifier.last_decision.provider_votes}
    assert IntentProviderStatus.SUCCEEDED in statuses
    assert IntentProviderStatus.TIMED_OUT in statuses
    assert IntentProviderStatus.FAILED in statuses


@pytest.mark.asyncio
async def test_ensemble_rejects_greeting_vote_for_substantive_message() -> None:
    classifier = EnsembleIntentClassifier(
        providers=[
            StaticProvider(
                "claude_haiku",
                vote(
                    QueryIntentType.GREETING,
                    SalesStage.SERVICE_DISCOVERY,
                    service=ServiceCategory.GHOSTWRITING,
                ),
            )
        ],
        decision_layer=DecisionLayer(),
        timeout_seconds=1.0,
    )

    result = await classifier.classify(
        processed("Hi, I need ghostwriting help for a fantasy novel."),
        ThreadState(),
    )

    assert result.query_primary == QueryIntentType.SERVICE_QUESTION
    assert result.service_primary == ServiceCategory.GHOSTWRITING
    assert classifier.last_decision is not None
    provider_vote = classifier.last_decision.provider_votes[0].vote
    assert provider_vote is not None
    assert "greeting_vote_rejected_for_substantive_message" in provider_vote.evidence


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_repeated_failures() -> None:
    classifier = EnsembleIntentClassifier(
        providers=[FailingProvider()],
        decision_layer=DecisionLayer(),
        timeout_seconds=0.01,
    )

    for _ in range(3):
        await classifier.classify(processed("hello"), ThreadState())
    await classifier.classify(processed("hello"), ThreadState())

    assert classifier.last_decision is not None
    assert classifier.last_decision.provider_votes[0].status == IntentProviderStatus.CIRCUIT_OPEN
