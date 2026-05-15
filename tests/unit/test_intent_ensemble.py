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
    assert "trimatch_funnel_stage_present_but_weight_zero" in result.audit_trail


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
    # With trimatch_funnel_stage_weight=0.0 the funnel signal is shadow-only,
    # so the fallback still pins funnel_stage to NEW.
    assert result.final_vote.funnel_stage == SalesStage.NEW
    assert "trimatch_query_service_fallback" in result.audit_trail
    assert "trimatch_funnel_stage_unavailable" in result.audit_trail


def test_decision_layer_uses_trimatch_funnel_when_weight_positive_and_providers_fail() -> None:
    """When trimatch funnel weight > 0, the no-provider-votes fallback must
    honor the deterministic funnel signal instead of pinning to NEW.

    This is the production-fix path for the 2026-05-14 incident where all
    three providers were circuit-open and every turn landed at
    funnel_stage=NEW regardless of what the message said.
    """

    trimatch = TriMatchResult(
        query_primary=QueryIntentType.PRICING_QUESTION,
        service_primary=ServiceCategory.GHOSTWRITING,
        funnel_stage=SalesStage.QUOTE_REQUESTED,
        confidence=0.88,
        mode=TriMatchMode.SHADOW,
    )

    result = DecisionLayer(trimatch_funnel_stage_weight=0.5).decide(
        provider_votes=[
            ProviderIntentVote(
                provider="claude_haiku",
                status=IntentProviderStatus.FAILED,
                error="schema",
            )
        ],
        trimatch_result=trimatch,
    )

    assert result.final_vote.funnel_stage == SalesStage.QUOTE_REQUESTED
    assert "trimatch_funnel_stage_fallback" in result.audit_trail
    assert "trimatch_funnel_stage_shadow_weight_zero" not in result.audit_trail
    assert result.funnel_stage_scores == {SalesStage.QUOTE_REQUESTED.value: trimatch.confidence}


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


@pytest.mark.asyncio
async def test_circuit_breaker_recovers_after_cooldown_when_provider_returns() -> None:
    """Open breakers must transition through half-open and close again once
    the upstream recovers. This is the behavior missing in the production
    image on 2026-05-14: every provider stayed circuit_open for the whole
    run because the prior implementation latched open permanently.
    """

    from bookcraft.components.intent.ensemble import CircuitBreaker

    clock = [0.0]
    breaker = CircuitBreaker(
        failure_threshold=2,
        cooldown_seconds=10.0,
        max_cooldown_seconds=60.0,
        _clock=lambda: clock[0],
    )

    # Two failures -> open.
    assert breaker.before_call() is True
    breaker.record_failure()
    assert breaker.before_call() is True
    breaker.record_failure()
    assert breaker.open is True
    assert breaker.before_call() is False  # still within cooldown

    # Advance past cooldown -> single probe allowed (half-open).
    clock[0] = 11.0
    assert breaker.before_call() is True
    assert breaker.half_open is True
    # A second call while a probe is in flight is short-circuited.
    assert breaker.before_call() is False

    # Probe succeeds -> breaker closes.
    breaker.record_success()
    assert breaker.open is False
    assert breaker.half_open is False
    assert breaker.before_call() is True


@pytest.mark.asyncio
async def test_circuit_breaker_backs_off_when_half_open_probe_fails() -> None:
    from bookcraft.components.intent.ensemble import CircuitBreaker

    clock = [0.0]
    breaker = CircuitBreaker(
        failure_threshold=1,
        cooldown_seconds=10.0,
        max_cooldown_seconds=60.0,
        _clock=lambda: clock[0],
    )

    breaker.record_failure()  # opens at t=0, cooldown=10
    assert breaker.open is True

    # First probe at t=10 fails -> cooldown doubles to 20.
    clock[0] = 10.0
    assert breaker.before_call() is True
    breaker.record_failure()
    assert breaker.open is True
    assert breaker.current_cooldown == 20.0

    # Still within new cooldown at t=20 (opened_at reset to 10, so closes at 30).
    clock[0] = 20.0
    assert breaker.before_call() is False

    # Past new cooldown at t=31 -> half-open again.
    clock[0] = 31.0
    assert breaker.before_call() is True

def test_decision_layer_uses_funnel_only_trimatch_when_all_providers_fail() -> None:
    trimatch = TriMatchResult(
        funnel_stage=SalesStage.QUOTE_REQUESTED,
        confidence=0.9,
        mode=TriMatchMode.SHADOW,
        shadow_only_dimensions=["funnel_stage"],
    )

    result = DecisionLayer(trimatch_funnel_stage_weight=0.5).decide(
        provider_votes=[
            ProviderIntentVote(
                provider="claude_haiku",
                status=IntentProviderStatus.FAILED,
                error="timeout",
            )
        ],
        trimatch_result=trimatch,
    )

    assert result.final_vote.query_primary == QueryIntentType.UNCLEAR
    assert result.final_vote.service_primary is None
    assert result.final_vote.funnel_stage == SalesStage.QUOTE_REQUESTED
    assert "trimatch_funnel_stage_fallback" in result.audit_trail
    assert result.funnel_stage_scores == {"quote_requested": 0.9}


def test_provider_payload_normalizer_handles_model_field_shape_drift() -> None:
    from bookcraft.components.intent.normalization import normalize_provider_vote_payload

    raw = {
        "query_primary": "service_question",
        "query_secondary": "publishing_platform_question",
        "service_primary": "cover_design_illustration",
        "service_secondary": "publishing_distribution",
        "funnel_stage": "new",
        "confidence": 0.91,
        "needs_clarification": False,
        "rationale": "provider returned scalar secondary fields",
        "evidence": {"reason": "book cover and publishing distribution mentioned"},
    }

    normalized = normalize_provider_vote_payload(raw)
    vote = IntentVote.model_validate(normalized)

    assert vote.query_primary.value == "service_question"
    assert [item.value for item in vote.query_secondary] == ["publishing_platform_question"]
    assert vote.service_primary.value == "cover_design_illustration"
    assert [item.value for item in vote.service_secondary] == ["publishing_distribution"]
    assert vote.evidence

def test_intent_vote_schema_normalizes_provider_shape_drift() -> None:
    raw = {
        "query_primary": "service_question",
        "query_secondary": "ghostwriting_requirements",
        "service_primary": "audiobook_production",
        "service_secondary": "video_trailer",
        "funnel_stage": "new",
        "confidence": "0.91",
        "needs_clarification": False,
        "rationale": "provider returned scalar fields",
        "evidence": {"reason": "provider returned dict evidence"},
    }

    vote = IntentVote.model_validate(raw)

    assert vote.query_primary.value == "service_question"
    assert vote.query_secondary == []
    assert vote.service_primary.value == "audiobook_production"
    assert [item.value for item in vote.service_secondary] == ["video_trailer"]
    assert vote.confidence == 0.91
    assert vote.evidence
