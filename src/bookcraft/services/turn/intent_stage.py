"""Intent classification and its audit events, independent of response generation."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import structlog

from bookcraft.components.intent.context_arbiter import ContextArbiterResult
from bookcraft.components.intent.hardening import harden_intent_from_message
from bookcraft.components.intent.schemas import DecisionLayerResult, IntentVote
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.components.trimatch.schemas import TriMatchResult
from bookcraft.domain.state import ThreadState
from bookcraft.infra.observability import STAGE_LATENCY

if TYPE_CHECKING:
    from bookcraft.services.chat import ChatService, _EventBuffer


async def classify_and_audit(
    self: ChatService,
    *,
    processed: ProcessedMessage,
    state: ThreadState,
    thread_id: UUID,
    event_sequence: int,
    previous_event_hash: str | None,
    event_ids: list[str],
    _evt_buf: _EventBuffer | None,
) -> tuple[
    TriMatchResult | None,
    TriMatchResult | None,
    IntentVote,
    IntentVote,
    ContextArbiterResult,
    DecisionLayerResult | None,
    int,
    str | None,
]:
    from bookcraft.services.chat import (
        _apply_shortcut_to_intent,
        _apply_tiebreaker_to_intent,
        _intent_snapshot,
        _summarize_trimatch_payload,
        _trimatch_advisory_payload,
        _trimatch_disagreement_payload,
        _trimatch_shortcut_considered_payload,
        _trimatch_tiebreaker_considered_payload,
    )

    trimatch_result = None
    trimatch_shadow_result = None
    if self.trimatch_engine is not None:
        trimatch_result = self.trimatch_engine.classify(processed)
        event_id, event_sequence, previous_event_hash = await self._append_thread_event(
            thread_id=thread_id,
            sequence=event_sequence,
            previous_hash=previous_event_hash,
            event_type="trimatch.voted",
            payload=_summarize_trimatch_payload(trimatch_result)
            if self.trimatch_event_evidence_summary
            else trimatch_result.model_dump(mode="json"),
            _buf=_evt_buf,
        )
        event_ids.append(event_id)

    if self.trimatch_shadow_engine is not None:
        try:
            trimatch_shadow_result = self.trimatch_shadow_engine.classify(processed)
            if self.trimatch_extra_mode == "shadow":
                (
                    event_id,
                    event_sequence,
                    previous_event_hash,
                ) = await self._append_thread_event(
                    thread_id=thread_id,
                    sequence=event_sequence,
                    previous_hash=previous_event_hash,
                    event_type="trimatch.extra_shadow_voted",
                    payload=_summarize_trimatch_payload(trimatch_shadow_result)
                    if self.trimatch_event_evidence_summary
                    else trimatch_shadow_result.model_dump(mode="json"),
                    _buf=_evt_buf,
                )
                event_ids.append(event_id)
        except Exception as exc:
            if self.trimatch_extra_mode == "advisory":
                failure_event_type = "trimatch.extra_advisory_failed"
            elif self.trimatch_extra_mode == "tiebreaker_candidate":
                failure_event_type = "trimatch.extra_tiebreaker_failed"
            elif self.trimatch_extra_mode == "shortcut_candidate":
                failure_event_type = "trimatch.extra_shortcut_failed"
            else:
                failure_event_type = "trimatch.extra_shadow_failed"
            structlog.get_logger(__name__).warning(
                failure_event_type,
                thread_id=str(thread_id),
                exception_class=exc.__class__.__name__,
            )
            event_id, event_sequence, previous_event_hash = await self._append_thread_event(
                thread_id=thread_id,
                sequence=event_sequence,
                previous_hash=previous_event_hash,
                event_type=failure_event_type,
                payload={"exception_class": exc.__class__.__name__},
                _buf=_evt_buf,
            )
            event_ids.append(event_id)

    with STAGE_LATENCY.labels(stage="intent_classification").time():
        intent = await self.intent_classifier.classify(
            processed,
            state,
            trimatch_result=trimatch_result,
        )
    ensemble_intent = intent
    intent = harden_intent_from_message(intent, processed)
    arbiter_result = self.context_arbiter.arbitrate(
        intent=intent,
        processed=processed,
        state=state,
    )
    intent = arbiter_result.intent
    decision = self.intent_classifier.last_decision

    if self.trimatch_extra_mode == "advisory" and trimatch_shadow_result is not None:
        event_id, event_sequence, previous_event_hash = await self._append_thread_event(
            thread_id=thread_id,
            sequence=event_sequence,
            previous_hash=previous_event_hash,
            event_type="trimatch.extra_advisory_recommended",
            payload=_trimatch_advisory_payload(
                extra_advisory=trimatch_shadow_result,
                final_intent=intent,
            ),
            _buf=_evt_buf,
        )
        event_ids.append(event_id)

    if self.trimatch_extra_mode == "tiebreaker_candidate" and trimatch_shadow_result is not None:
        tiebreaker_payload = _trimatch_tiebreaker_considered_payload(
            active_trimatch=trimatch_result,
            extra_tiebreaker=trimatch_shadow_result,
            ensemble_intent=ensemble_intent,
            final_intent=intent,
        )
        intent = _apply_tiebreaker_to_intent(
            intent=intent,
            decision=tiebreaker_payload["decision"],
        )
        tiebreaker_payload["after"] = {
            "final_after_tiebreaker": _intent_snapshot(intent),
        }
        event_id, event_sequence, previous_event_hash = await self._append_thread_event(
            thread_id=thread_id,
            sequence=event_sequence,
            previous_hash=previous_event_hash,
            event_type="trimatch.extra_tiebreaker_considered",
            payload=tiebreaker_payload,
            _buf=_evt_buf,
        )
        event_ids.append(event_id)

    if self.trimatch_extra_mode == "shortcut_candidate" and trimatch_shadow_result is not None:
        shortcut_payload = _trimatch_shortcut_considered_payload(
            extra_shortcut=trimatch_shadow_result,
            final_intent=intent,
        )
        intent = _apply_shortcut_to_intent(
            intent=intent,
            shortcut=shortcut_payload["shortcut"],
        )
        shortcut_payload["after"] = {
            "final_after_shortcut": _intent_snapshot(intent),
        }
        event_id, event_sequence, previous_event_hash = await self._append_thread_event(
            thread_id=thread_id,
            sequence=event_sequence,
            previous_hash=previous_event_hash,
            event_type="trimatch.extra_shortcut_considered",
            payload=shortcut_payload,
            _buf=_evt_buf,
        )
        event_ids.append(event_id)

    extra_shadow_for_disagreement = (
        trimatch_shadow_result if self.trimatch_extra_mode == "shadow" else None
    )

    disagreement_payload = _trimatch_disagreement_payload(
        active_trimatch=trimatch_result,
        extra_shadow=extra_shadow_for_disagreement,
        ensemble_intent=ensemble_intent,
        final_intent=intent,
    )
    if disagreement_payload["should_log"]:
        event_id, event_sequence, previous_event_hash = await self._append_thread_event(
            thread_id=thread_id,
            sequence=event_sequence,
            previous_hash=previous_event_hash,
            event_type="trimatch.disagreement_observed",
            payload=disagreement_payload,
            _buf=_evt_buf,
        )
        event_ids.append(event_id)

    event_id, event_sequence, previous_event_hash = await self._append_thread_event(
        thread_id=thread_id,
        sequence=event_sequence,
        previous_hash=previous_event_hash,
        event_type="intent.classified",
        payload={
            "intent": intent.model_dump(mode="json"),
            "decision": decision.model_dump(mode="json") if decision else None,
        },
        _buf=_evt_buf,
    )
    event_ids.append(event_id)
    return (
        trimatch_result,
        trimatch_shadow_result,
        intent,
        ensemble_intent,
        arbiter_result,
        decision,
        event_sequence,
        previous_event_hash,
    )
