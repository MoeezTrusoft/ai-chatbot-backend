from dataclasses import dataclass

from prometheus_client import Histogram

from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.llm.metrics import LLM_CALLS
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory
from bookcraft.domain.state import ThreadState

INTENT_SECONDS = Histogram("intent_classification_seconds", "Intent classification latency.")


@dataclass(slots=True)
class HaikuIntentClassifier:
    provider_name: str = "mock_haiku"

    async def classify(self, message: ProcessedMessage, state: ThreadState) -> IntentVote:
        del state
        with INTENT_SECONDS.time():
            LLM_CALLS.labels(provider=self.provider_name, purpose="intent").inc()
            return self._mock_vote(message)

    def _mock_vote(self, message: ProcessedMessage) -> IntentVote:
        text = message.normalized.lower()
        query = QueryIntentType.SERVICE_QUESTION
        stage = SalesStage.SERVICE_DISCOVERY
        if text in {"hi", "hello", "hey"}:
            query = QueryIntentType.GREETING
            stage = SalesStage.NEW
        elif "price" in text or "cost" in text or "quote" in text:
            query = QueryIntentType.PRICING_QUESTION
            stage = SalesStage.QUOTE_REQUESTED
        elif "timeline" in text or "how long" in text or "when" in text:
            query = QueryIntentType.TIMELINE_QUESTION
            stage = SalesStage.QUOTE_REQUESTED
        elif "portfolio" in text or "sample" in text:
            query = QueryIntentType.PORTFOLIO_REQUEST
        elif "nda" in text:
            query = QueryIntentType.NDA_REQUEST
            stage = SalesStage.NDA_REQUESTED
        elif "agreement" in text or "contract" in text:
            query = QueryIntentType.AGREEMENT_REQUEST
            stage = SalesStage.AGREEMENT_REQUESTED
        elif "@" in text or "email" in text or "phone" in text:
            query = QueryIntentType.CONTACT_INFO_PROVIDED
            stage = SalesStage.SCOPING

        raw_services = message.deterministic_atoms.get("services")
        services = [ServiceCategory(service) for service in _string_list(raw_services)]
        return IntentVote(
            query_primary=query,
            service_primary=services[0] if services else None,
            service_secondary=services[1:],
            funnel_stage=stage,
            needs_clarification=query
            in {QueryIntentType.PRICING_QUESTION, QueryIntentType.TIMELINE_QUESTION},
            confidence=0.95 if query == QueryIntentType.GREETING else 0.82,
            rationale="Mock Haiku classifier derived intent from deterministic atoms and keywords.",
            evidence=[message.normalized[:160]],
        )


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []
