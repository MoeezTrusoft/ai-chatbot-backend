from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from prometheus_client import Counter, Histogram

from bookcraft.components.intent.classifier import mock_intent_vote
from bookcraft.components.intent.schemas import (
    DecisionLayerResult,
    IntentProviderStatus,
    IntentVote,
    ProviderIntentVote,
)
from bookcraft.components.llm.protocols import LLMProvider
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.components.trimatch.schemas import TriMatchResult
from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory
from bookcraft.domain.state import ThreadState

INTENT_PROVIDER_CALLS = Counter(
    "intent_provider_calls_total",
    "Intent provider calls by provider and status.",
    ["provider", "status"],
)
INTENT_PROVIDER_LATENCY = Histogram(
    "intent_provider_latency_seconds",
    "Intent provider latency.",
    ["provider"],
)
INTENT_DECISIONS = Counter(
    "intent_decisions_total",
    "Decision Layer intent decisions by query and funnel stage.",
    ["query_intent", "funnel_stage"],
)
INTENT_COST = Counter(
    "llm_call_cost_usd_total",
    "Estimated LLM call cost in USD.",
    ["provider", "purpose"],
)
INTENT_TOKENS = Counter(
    "llm_tokens_total",
    "Estimated LLM tokens by provider, purpose, and token type.",
    ["provider", "purpose", "token_type"],
)


class IntentVoteProvider(Protocol):
    name: str

    async def classify(self, message: ProcessedMessage, state: ThreadState) -> IntentVote: ...


@dataclass(slots=True)
class CircuitBreaker:
    """Per-provider circuit breaker with cooldown and half-open probing.

    States:
      closed     -> calls flow through; consecutive failures count up.
      open       -> calls short-circuit until ``current_cooldown`` elapses
                    since the breaker opened.
      half_open  -> a single probe call is allowed. Success closes the
                    breaker; failure re-opens with doubled cooldown, up to
                    ``max_cooldown_seconds``.

    The earlier implementation latched ``open`` permanently once
    ``failure_threshold`` was reached, so a transient provider outage at
    boot would silently disable that voter for the lifetime of the
    process. The production load report on 2026-05-14 captured exactly
    this failure mode: 300/300 provider votes were ``circuit_open``
    across all three providers for every turn in the run.
    """

    failure_threshold: int = 3
    cooldown_seconds: float = 30.0
    max_cooldown_seconds: float = 600.0
    failure_count: int = 0
    open: bool = False
    half_open: bool = False
    opened_at: float = 0.0
    current_cooldown: float = 30.0
    _clock: Callable[[], float] = field(default=time.monotonic)

    def before_call(self) -> bool:
        if not self.open:
            return True
        if self.half_open:
            # A probe is already in flight; do not allow a second one.
            return False
        if self._clock() - self.opened_at >= self.current_cooldown:
            self.half_open = True
            return True
        return False

    def record_success(self) -> None:
        self.failure_count = 0
        self.open = False
        self.half_open = False
        self.current_cooldown = self.cooldown_seconds

    def record_failure(self) -> None:
        self.failure_count += 1
        if self.half_open:
            # Probe failed: stay open, back off exponentially, reset clock.
            self.half_open = False
            self.current_cooldown = min(
                self.current_cooldown * 2,
                self.max_cooldown_seconds,
            )
            self.opened_at = self._clock()
        elif not self.open and self.failure_count >= self.failure_threshold:
            self.open = True
            self.opened_at = self._clock()
            self.current_cooldown = self.cooldown_seconds


@dataclass(slots=True)
class MockIntentProvider:
    name: str

    async def classify(self, message: ProcessedMessage, state: ThreadState) -> IntentVote:
        del state
        return mock_intent_vote(message, provider_name=self.name)


@dataclass(slots=True)
class LLMIntentProvider:
    name: str
    adapter: LLMProvider

    async def classify(self, message: ProcessedMessage, state: ThreadState) -> IntentVote:
        system = _intent_system_prompt()
        user = _intent_user_prompt(message, state)
        result = await self.adapter.structured(
            system=system,
            user=user,
            output_model=IntentVote,
            purpose="intent",
        )
        return IntentVote.model_validate(_normalize_provider_vote_payload(result))


@dataclass(slots=True)
class DecisionLayer:
    provider_weights: dict[str, float] = field(
        default_factory=lambda: {
            "claude_haiku": 1.0,
            "openai_gpt_5_4_mini": 1.0,
            "deepseek_v3": 1.0,
        }
    )
    trimatch_weight: float = 0.35
    trimatch_funnel_stage_weight: float = 0.5

    def decide(
        self,
        *,
        provider_votes: Sequence[ProviderIntentVote],
        trimatch_result: TriMatchResult | None,
    ) -> DecisionLayerResult:
        successful = [vote for vote in provider_votes if vote.vote is not None]
        if not successful:
            if trimatch_result is not None and (
                trimatch_result.query_primary is not None
                or trimatch_result.service_primary is not None
                or trimatch_result.funnel_stage is not None
            ):
                query = _normalize_trimatch_query(trimatch_result, trimatch_result.query_primary)
                service = trimatch_result.service_primary
                # When trimatch's funnel signal is trusted (weight > 0),
                # honor it in the no-provider-votes fallback too. Otherwise
                # the system pins funnel_stage to NEW for every turn whenever
                # LLM voters are down, which was the production failure mode
                # on 2026-05-14 (100/100 turns reported funnel_stage=new even
                # when the deterministic layer detected QUOTE_REQUESTED).
                if (
                    self.trimatch_funnel_stage_weight > 0.0
                    and trimatch_result.funnel_stage is not None
                ):
                    funnel = trimatch_result.funnel_stage
                    funnel_audit = "trimatch_funnel_stage_fallback"
                else:
                    funnel = SalesStage.NEW
                    funnel_audit = "trimatch_funnel_stage_unavailable"
                final = IntentVote(
                    query_primary=query,
                    service_primary=service,
                    funnel_stage=funnel,
                    needs_clarification=query == QueryIntentType.UNCLEAR,
                    confidence=trimatch_result.confidence,
                    rationale=(
                        "Decision Layer fallback: provider votes unavailable; "
                        "using Tri-Match query/service shadow evidence."
                    ),
                    evidence=["no_provider_votes", "trimatch_fallback_query_service"],
                )
                INTENT_DECISIONS.labels(
                    query_intent=query.value,
                    funnel_stage=funnel.value,
                ).inc()
                return DecisionLayerResult(
                    final_vote=final,
                    provider_votes=list(provider_votes),
                    needs_clarification=final.needs_clarification,
                    audit_trail=[
                        "no_provider_votes",
                        "trimatch_query_service_fallback",
                        funnel_audit,
                    ],
                    query_scores={query.value: trimatch_result.confidence},
                    service_scores={service.value: trimatch_result.confidence}
                    if service is not None
                    else {},
                    funnel_stage_scores={funnel.value: trimatch_result.confidence}
                    if funnel_audit == "trimatch_funnel_stage_fallback"
                    else {},
                )
            fallback = IntentVote(
                query_primary=QueryIntentType.UNCLEAR,
                service_primary=None,
                funnel_stage=SalesStage.NEW,
                needs_clarification=True,
                confidence=0.0,
                rationale="Decision Layer fallback: no provider returned a usable vote.",
                evidence=[],
            )
            return DecisionLayerResult(
                final_vote=fallback,
                provider_votes=list(provider_votes),
                needs_clarification=True,
                audit_trail=["no_provider_votes"],
            )

        query_scores: dict[str, float] = {}
        service_scores: dict[str, float] = {}
        funnel_stage_scores: dict[str, float] = {}
        audit = ["provider_votes_collected"]
        for vote in successful:
            assert vote.vote is not None
            weight = self.provider_weights.get(vote.provider, 1.0)
            self._add_score(
                query_scores,
                vote.vote.query_primary.value,
                weight,
                vote.vote.confidence,
            )
            self._add_score(
                funnel_stage_scores,
                vote.vote.funnel_stage.value,
                weight,
                vote.vote.confidence,
            )
            if vote.vote.service_primary is not None:
                self._add_score(
                    service_scores,
                    vote.vote.service_primary.value,
                    weight,
                    vote.vote.confidence,
                )
        if trimatch_result is not None:
            if trimatch_result.query_primary is not None:
                self._add_score(
                    query_scores,
                    trimatch_result.query_primary.value,
                    self.trimatch_weight,
                    trimatch_result.confidence,
                )
                audit.append("trimatch_query_vote_included")
            if trimatch_result.service_primary is not None:
                self._add_score(
                    service_scores,
                    trimatch_result.service_primary.value,
                    self.trimatch_weight,
                    trimatch_result.confidence,
                )
                audit.append("trimatch_service_vote_included")
            if trimatch_result.funnel_stage is not None and self.trimatch_funnel_stage_weight > 0.0:
                self._add_score(
                    funnel_stage_scores,
                    trimatch_result.funnel_stage.value,
                    self.trimatch_funnel_stage_weight,
                    trimatch_result.confidence,
                )
                audit.append("trimatch_funnel_stage_vote_included")
            elif trimatch_result.funnel_stage is not None:
                audit.append("trimatch_funnel_stage_present_but_weight_zero")

        query_quorum = self._provider_quorum(successful)
        if query_quorum is not None:
            audit.append(f"provider_query_quorum:{query_quorum}")

        first_vote = successful[0].vote
        if first_vote is None:
            raise RuntimeError("successful provider vote unexpectedly missing vote payload")
        query = QueryIntentType(self._winner(query_scores) or first_vote.query_primary.value)
        service_winner = self._winner(service_scores)
        service = ServiceCategory(service_winner) if service_winner else None
        funnel = SalesStage(self._winner(funnel_stage_scores) or first_vote.funnel_stage.value)
        confidence = min(1.0, max(query_scores.values(), default=0.0))
        needs_clarification = any(vote.vote.needs_clarification for vote in successful if vote.vote)
        final = IntentVote(
            query_primary=query,
            service_primary=service,
            funnel_stage=funnel,
            needs_clarification=needs_clarification,
            confidence=confidence,
            rationale="Decision Layer aggregated provider votes with Tri-Match shadow inputs.",
            evidence=audit,
        )
        INTENT_DECISIONS.labels(query_intent=query.value, funnel_stage=funnel.value).inc()
        return DecisionLayerResult(
            final_vote=final,
            provider_votes=list(provider_votes),
            query_scores=query_scores,
            service_scores=service_scores,
            funnel_stage_scores=funnel_stage_scores,
            needs_clarification=needs_clarification,
            audit_trail=audit,
        )

    def _add_score(
        self,
        scores: dict[str, float],
        key: str,
        source_weight: float,
        confidence: float,
    ) -> None:
        scores[key] = scores.get(key, 0.0) + source_weight * confidence

    def _winner(self, scores: dict[str, float]) -> str | None:
        if not scores:
            return None
        return max(scores.items(), key=lambda item: item[1])[0]

    def _provider_quorum(self, provider_votes: list[ProviderIntentVote]) -> str | None:
        counts: dict[str, int] = {}
        for provider_vote in provider_votes:
            if provider_vote.vote is None:
                continue
            query = provider_vote.vote.query_primary.value
            counts[query] = counts.get(query, 0) + 1
        for query, count in counts.items():
            if count >= 2:
                return query
        return None


def _normalize_provider_vote_payload(payload: object) -> object:
    if not isinstance(payload, dict):
        return payload

    from enum import Enum
    from typing import get_args

    data = dict(payload)

    def as_list(value: object) -> list[object]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, (tuple, set)):
            return list(value)
        return [value]

    def enum_values_for_field(field_name: str) -> set[str]:
        field = IntentVote.model_fields.get(field_name)
        if field is None:
            return set()

        values: set[str] = set()

        def walk(annotation: object) -> None:
            if isinstance(annotation, type) and issubclass(annotation, Enum):
                values.update(str(item.value) for item in annotation)
            for arg in get_args(annotation):
                walk(arg)

        walk(field.annotation)
        return values

    data["query_secondary"] = as_list(data.get("query_secondary"))
    data["service_secondary"] = as_list(data.get("service_secondary"))
    data["evidence"] = [
        str(item)
        for item in as_list(data.get("evidence"))
        if item is not None
    ]

    allowed_query = enum_values_for_field("query_primary")
    allowed_service = enum_values_for_field("service_primary")
    allowed_funnel = enum_values_for_field("funnel_stage")

    data["query_secondary"] = [
        str(item)
        for item in data["query_secondary"]
        if isinstance(item, str) and (not allowed_query or item in allowed_query)
    ]

    data["service_secondary"] = [
        str(item)
        for item in data["service_secondary"]
        if isinstance(item, str) and (not allowed_service or item in allowed_service)
    ]

    if allowed_query and data.get("query_primary") not in allowed_query:
        data["query_primary"] = "unclear"

    if allowed_service and data.get("service_primary") not in allowed_service:
        data["service_primary"] = None

    if allowed_funnel and data.get("funnel_stage") not in allowed_funnel:
        data["funnel_stage"] = "new"

    return data


def _format_provider_error(exc: Exception) -> str:
    name = exc.__class__.__name__
    response = getattr(exc, "response", None)

    if response is not None:
        status_code = getattr(response, "status_code", None)
        body = getattr(response, "text", "") or ""
        body = body.replace("\\n", " ").replace("\\r", " ")
        return f"{name}: status={status_code} body={body[:500]}"

    message = str(exc).strip()
    if message:
        return f"{name}: {message[:500]}"
    return name


@dataclass(slots=True)
class EnsembleIntentClassifier:
    providers: Sequence[IntentVoteProvider]
    decision_layer: DecisionLayer
    timeout_seconds: float = 2.5
    circuit_breakers: dict[str, CircuitBreaker] = field(default_factory=dict)
    last_decision: DecisionLayerResult | None = None

    async def classify(
        self,
        message: ProcessedMessage,
        state: ThreadState,
        trimatch_result: TriMatchResult | None = None,
    ) -> IntentVote:
        provider_votes = await asyncio.gather(
            *(self._classify_provider(provider, message, state) for provider in self.providers)
        )
        decision = self.decision_layer.decide(
            provider_votes=provider_votes,
            trimatch_result=trimatch_result,
        )
        self.last_decision = decision
        return decision.final_vote

    async def _classify_provider(
        self,
        provider: IntentVoteProvider,
        message: ProcessedMessage,
        state: ThreadState,
    ) -> ProviderIntentVote:
        breaker = self.circuit_breakers.setdefault(provider.name, CircuitBreaker())
        if not breaker.before_call():
            INTENT_PROVIDER_CALLS.labels(
                provider=provider.name,
                status=IntentProviderStatus.CIRCUIT_OPEN.value,
            ).inc()
            return ProviderIntentVote(
                provider=provider.name,
                status=IntentProviderStatus.CIRCUIT_OPEN,
                error="circuit_open",
            )
        started = time.perf_counter()
        try:
            with INTENT_PROVIDER_LATENCY.labels(provider=provider.name).time():
                vote = await asyncio.wait_for(
                    provider.classify(message, state),
                    timeout=self.timeout_seconds,
                )
                vote = _normalize_provider_vote(vote, message)
        except TimeoutError:
            breaker.record_failure()
            return self._failed_vote(
                provider=provider.name,
                status=IntentProviderStatus.TIMED_OUT,
                started=started,
                error="timeout",
            )
        except Exception as exc:  # noqa: BLE001 - provider failure must be captured.
            breaker.record_failure()
            return self._failed_vote(
                provider=provider.name,
                status=IntentProviderStatus.FAILED,
                started=started,
                error=_format_provider_error(exc),
            )
        breaker.record_success()
        prompt_tokens = max(1, len(message.normalized.split()))
        completion_tokens = 32
        INTENT_PROVIDER_CALLS.labels(
            provider=provider.name,
            status=IntentProviderStatus.SUCCEEDED.value,
        ).inc()
        INTENT_TOKENS.labels(provider.name, "intent", "prompt").inc(prompt_tokens)
        INTENT_TOKENS.labels(provider.name, "intent", "completion").inc(completion_tokens)
        INTENT_COST.labels(provider=provider.name, purpose="intent").inc(0.0)
        return ProviderIntentVote(
            provider=provider.name,
            status=IntentProviderStatus.SUCCEEDED,
            vote=vote,
            latency_ms=(time.perf_counter() - started) * 1000,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=0.0,
        )

    def _failed_vote(
        self,
        *,
        provider: str,
        status: IntentProviderStatus,
        started: float,
        error: str,
    ) -> ProviderIntentVote:
        INTENT_PROVIDER_CALLS.labels(provider=provider, status=status.value).inc()
        return ProviderIntentVote(
            provider=provider,
            status=status,
            latency_ms=(time.perf_counter() - started) * 1000,
            error=error,
        )


def _normalize_provider_vote(vote: IntentVote, message: ProcessedMessage) -> IntentVote:
    if vote.query_primary != QueryIntentType.GREETING:
        return vote
    if _is_greeting_only(message.normalized):
        return vote
    corrected_query = (
        QueryIntentType.SERVICE_QUESTION
        if vote.service_primary is not None
        else QueryIntentType.UNCLEAR
    )
    return vote.model_copy(
        update={
            "query_primary": corrected_query,
            "needs_clarification": corrected_query == QueryIntentType.UNCLEAR,
            "rationale": "Corrected provider greeting vote: message contains substantive text.",
            "evidence": [*vote.evidence, "greeting_vote_rejected_for_substantive_message"],
        }
    )


def _normalize_trimatch_query(
    trimatch_result: TriMatchResult,
    query: QueryIntentType | None,
) -> QueryIntentType:
    if query != QueryIntentType.GREETING:
        return query or QueryIntentType.UNCLEAR
    matched_text = " ".join(evidence.matched_text for evidence in trimatch_result.evidence)
    if _is_greeting_only(matched_text):
        return QueryIntentType.GREETING
    if trimatch_result.service_primary is not None:
        return QueryIntentType.SERVICE_QUESTION
    return QueryIntentType.UNCLEAR


def _is_greeting_only(text: str) -> bool:
    return bool(
        re.fullmatch(
            r"\s*(hi|hello|hey|good (morning|afternoon|evening))[!.?]*\s*",
            text,
            flags=re.IGNORECASE,
        )
    )


def build_mock_ensemble_classifier(
    *,
    timeout_seconds: float,
    trimatch_funnel_stage_weight: float,
) -> EnsembleIntentClassifier:
    return EnsembleIntentClassifier(
        providers=[
            MockIntentProvider(name="claude_haiku"),
            MockIntentProvider(name="openai_gpt_5_4_mini"),
            MockIntentProvider(name="deepseek_v3"),
        ],
        decision_layer=DecisionLayer(
            trimatch_funnel_stage_weight=trimatch_funnel_stage_weight,
        ),
        timeout_seconds=timeout_seconds,
    )


def build_live_ensemble_classifier(
    *,
    providers: Sequence[IntentVoteProvider],
    timeout_seconds: float,
    trimatch_funnel_stage_weight: float,
) -> EnsembleIntentClassifier:
    return EnsembleIntentClassifier(
        providers=providers,
        decision_layer=DecisionLayer(
            trimatch_funnel_stage_weight=trimatch_funnel_stage_weight,
        ),
        timeout_seconds=timeout_seconds,
    )


def _intent_system_prompt() -> str:
    query_values = ", ".join(item.value for item in QueryIntentType)
    service_values = ", ".join(item.value for item in ServiceCategory)
    stage_values = ", ".join(item.value for item in SalesStage)
    return (
        "You classify BookCraft sales-chat intent only. Return strict JSON matching the "
        "provided schema. Do not call tools. Do not calculate or mention prices, timelines, "
        "discounts, sample URLs, legal clauses, or guarantees. "
        f"Allowed query_primary values: {query_values}. "
        f"Allowed service_primary values: {service_values}. "
        f"Allowed funnel_stage values: {stage_values}. "
        "Use null when service_primary is unclear. Keep rationale short."
    )


def _intent_user_prompt(message: ProcessedMessage, state: ThreadState) -> str:
    state_snapshot = {
        "known_email": state.personal.email.value,
        "known_phone": state.personal.phone.value,
        "word_count": state.project.word_count.value,
        "page_count": state.project.page_count.value,
        "genre": state.project.genre.value,
        "manuscript_status": state.project.manuscript_status.value,
        "sales_stage": state.sales_stage.value.value if state.sales_stage.value else None,
    }
    return (
        "Classify this inbound message.\n"
        f"Normalized message: {message.normalized}\n"
        f"Deterministic atoms: {message.deterministic_atoms}\n"
        f"Thread state snapshot: {state_snapshot}\n"
        "Required JSON fields: query_primary, query_secondary, service_primary, "
        "service_secondary, funnel_stage, needs_clarification, confidence, rationale, evidence."
    )
