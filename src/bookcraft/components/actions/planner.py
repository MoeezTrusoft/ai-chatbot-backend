from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from bookcraft.components.actions.schemas import ActionPlan, ActionStatus, ActionType
from bookcraft.components.actions.slot_resolver import (
    contact_slots,
    has_email_or_phone,
    has_time_hint,
    is_confirmation_text,
    lead_follow_up_slots,
    project_slots,
    service_values,
)
from bookcraft.components.extraction.schemas import CombinedExtraction
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.domain.enums import QueryIntentType
from bookcraft.domain.state import ThreadState


@dataclass(slots=True)
class SalesActionPlanner:
    consultation_duration_minutes: int = 30
    default_business_timezone: str = "America/Chicago"

    def plan(
        self,
        *,
        processed: ProcessedMessage,
        state: ThreadState,
        intent: IntentVote,
        extraction: CombinedExtraction,
    ) -> ActionPlan:
        pending = state.sales_actions.pending_confirmation
        if pending.type and is_confirmation_text(processed.normalized):
            return self._pending_confirmation_plan(
                pending_type=pending.type, payload=pending.payload
            )

        contact = contact_slots(state=state, extraction=extraction, processed=processed)
        project = project_slots(state=state, extraction=extraction, processed=processed)
        services = service_values(intent=intent, processed=processed)

        if self._is_consultation_request(intent, extraction):
            return self._consultation_plan(
                processed=processed,
                contact=contact,
                services=services,
            )

        if self._is_pricing_request(intent):
            return self._pricing_plan(project=project, services=services, state=state)

        if intent.query_primary == QueryIntentType.PORTFOLIO_REQUEST:
            return self._portfolio_plan(
                intent=intent,
                services=services,
                extraction=extraction,
                state=state,
            )

        if intent.query_primary == QueryIntentType.NDA_REQUEST:
            return self._nda_plan(contact=contact, state=state, processed=processed)

        if intent.query_primary == QueryIntentType.AGREEMENT_REQUEST:
            return self._agreement_plan(contact=contact, state=state, processed=processed)

        if self._is_lead_candidate(intent, contact):
            return self._lead_plan(contact=contact, services=services)

        return ActionPlan(
            status=ActionStatus.NOT_NEEDED,
            reason="No sales action required for this turn.",
        )

    def _pending_confirmation_plan(
        self,
        *,
        pending_type: str,
        payload: dict[str, object] | None,
    ) -> ActionPlan:
        try:
            action_type = ActionType(pending_type)
        except ValueError:
            return ActionPlan(
                status=ActionStatus.BLOCKED,
                reason=f"Unknown pending confirmation type: {pending_type}",
            )

        collected_payload = payload or {}
        if action_type in {ActionType.GENERATE_NDA, ActionType.GENERATE_AGREEMENT}:
            collected_payload = {**collected_payload, "send_email": True}
        if action_type == ActionType.SCHEDULE_CONSULTATION:
            collected_payload = {**collected_payload, "confirmed": True}

        return ActionPlan(
            action_type=action_type,
            status=ActionStatus.READY,
            collected_slots={**collected_payload, "confirmed": True},
            pending_confirmation_key=pending_type,
            reason="User confirmed the pending sales action.",
        )

    @staticmethod
    def _is_lead_candidate(intent: IntentVote, contact: dict[str, str]) -> bool:
        return intent.query_primary in {
            QueryIntentType.CONTACT_INFO_PROVIDED,
            QueryIntentType.READY_TO_BUY,
            QueryIntentType.CONSULTATION_REQUEST,
        } or has_email_or_phone(contact)

    @staticmethod
    def _is_consultation_request(
        intent: IntentVote,
        extraction: CombinedExtraction,
    ) -> bool:
        return (
            intent.query_primary == QueryIntentType.CONSULTATION_REQUEST
            or extraction.consultation_request.requested
        )

    @staticmethod
    def _is_pricing_request(intent: IntentVote) -> bool:
        return intent.query_primary in {
            QueryIntentType.PRICING_QUESTION,
            QueryIntentType.TIMELINE_QUESTION,
        }

    def _lead_plan(
        self,
        *,
        contact: dict[str, str],
        services: list[str],
    ) -> ActionPlan:
        if "name" not in contact:
            return ActionPlan(
                action_type=ActionType.CREATE_LEAD,
                status=ActionStatus.MISSING_INFO,
                missing_slots=["name"],
                reason="Lead creation requires a contact name.",
            )
        if not has_email_or_phone(contact):
            return ActionPlan(
                action_type=ActionType.CREATE_LEAD,
                status=ActionStatus.MISSING_INFO,
                missing_slots=["email_or_phone"],
                reason="Lead creation requires at least email or phone.",
            )

        return ActionPlan(
            action_type=ActionType.CREATE_LEAD,
            status=ActionStatus.READY,
            collected_slots={
                **contact,
                "services": services,
            },
            recommended_follow_up_slots=lead_follow_up_slots(contact),
            reason="Lead can be created with available contact details.",
        )

    def _consultation_plan(
        self,
        *,
        processed: ProcessedMessage,
        contact: dict[str, str],
        services: list[str],
    ) -> ActionPlan:
        missing: list[str] = []

        if "name" not in contact:
            missing.append("name")
        if not has_email_or_phone(contact):
            missing.append("email_or_phone")
        if not has_time_hint(processed.normalized):
            missing.append("preferred_date_or_time_window")

        if missing:
            return ActionPlan(
                action_type=ActionType.SCHEDULE_CONSULTATION,
                status=ActionStatus.MISSING_INFO,
                missing_slots=missing,
                collected_slots={
                    **contact,
                    "services": services,
                    "duration_minutes": self.consultation_duration_minutes,
                    "business_timezone": self.default_business_timezone,
                },
                reason="Consultation scheduling is missing required customer or time details.",
            )

        return ActionPlan(
            action_type=ActionType.SCHEDULE_CONSULTATION,
            status=ActionStatus.NEEDS_CONFIRMATION,
            confirmation_required=True,
            pending_confirmation_key=ActionType.SCHEDULE_CONSULTATION.value,
            collected_slots={
                **contact,
                "services": services,
                "duration_minutes": self.consultation_duration_minutes,
                "business_timezone": self.default_business_timezone,
                "requested_time_text": processed.normalized,
            },
            reason="Consultation has enough details to negotiate a slot before booking.",
        )

    def _pricing_plan(
        self,
        *,
        project: dict[str, object],
        services: list[str],
        state: ThreadState,
    ) -> ActionPlan:
        missing: list[str] = []

        if not services:
            missing.append("services")
        if "word_count" not in project and "page_count" not in project:
            missing.append("word_or_page_count")
        if "genre" not in project:
            missing.append("genre")
        if "manuscript_status" not in project:
            missing.append("manuscript_status")
        if "deadline" not in project:
            missing.append("deadline")

        attempt_count = state.sales_actions.pricing.quote_attempt_count

        if missing and attempt_count < 1:
            return ActionPlan(
                action_type=ActionType.PRICE_QUOTE,
                status=ActionStatus.MISSING_INFO,
                missing_slots=missing,
                collected_slots={
                    "services": services,
                    **project,
                    "quote_attempt_count": attempt_count,
                },
                reason="Pricing requires project parameters before a proper quote.",
            )

        if missing:
            return ActionPlan(
                action_type=ActionType.PRICE_QUOTE,
                status=ActionStatus.NEEDS_CONFIRMATION,
                missing_slots=missing,
                collected_slots={
                    "services": services,
                    **project,
                    "quote_attempt_count": attempt_count,
                    "use_default_assumptions": True,
                },
                confirmation_required=True,
                pending_confirmation_key=ActionType.PRICE_QUOTE.value,
                reason="User appears to be persisting after missing-info quote request.",
            )

        return ActionPlan(
            action_type=ActionType.PRICE_QUOTE,
            status=ActionStatus.READY,
            collected_slots={"services": services, **project},
            reason="Pricing request has enough details for the pricing engine.",
        )

    @staticmethod
    def _portfolio_plan(
        *,
        intent: IntentVote,
        services: list[str],
        extraction: CombinedExtraction,
        state: ThreadState,
    ) -> ActionPlan:
        requested_service = (
            extraction.sample_request.service
            or (intent.service_primary.value if intent.service_primary else None)
            or (services[0] if services else None)
            or state.sales_actions.portfolio.requested_service
        )
        requested_genre = extraction.sample_request.genre or state.sales_actions.portfolio.genre

        if not requested_service:
            return ActionPlan(
                action_type=ActionType.PORTFOLIO_LOOKUP,
                status=ActionStatus.MISSING_INFO,
                missing_slots=["service"],
                reason="Portfolio lookup needs the intended service.",
            )

        return ActionPlan(
            action_type=ActionType.PORTFOLIO_LOOKUP,
            status=ActionStatus.READY,
            collected_slots={
                "service": requested_service,
                "genre": requested_genre,
                "exclude_sample_ids": state.sales_actions.portfolio.seen_sample_ids,
            },
            reason="Portfolio lookup has enough service context.",
        )

    @staticmethod
    def _nda_plan(
        *,
        contact: dict[str, str],
        state: ThreadState,
        processed: ProcessedMessage,
    ) -> ActionPlan:
        collected_contact = dict(contact)

        inferred_name = _nda_name_from_text(processed.raw)
        if inferred_name and "name" not in collected_contact:
            collected_contact["name"] = inferred_name

        effective_date = (
            state.sales_actions.documents.nda.effective_date
            or _nda_effective_date_from_text(processed.raw)
        )

        missing: list[str] = []

        if "name" not in collected_contact:
            missing.append("name")
        if "email" not in collected_contact:
            missing.append("email")
        if "phone" not in collected_contact:
            missing.append("phone")
        if not effective_date:
            missing.append("effective_date")

        if missing:
            return ActionPlan(
                action_type=ActionType.GENERATE_NDA,
                status=ActionStatus.MISSING_INFO,
                missing_slots=missing,
                collected_slots={
                    **collected_contact,
                    **({"effective_date": effective_date} if effective_date else {}),
                },
                reason="NDA request is missing required document parameters.",
            )

        return ActionPlan(
            action_type=ActionType.GENERATE_NDA,
            status=ActionStatus.NEEDS_CONFIRMATION,
            collected_slots={
                **collected_contact,
                "effective_date": effective_date,
                "send_email": False,
            },
            confirmation_required=True,
            pending_confirmation_key=ActionType.GENERATE_NDA.value,
            reason="NDA has enough details and needs send confirmation.",
        )

    @staticmethod
    def _agreement_plan(
        *,
        contact: dict[str, str],
        state: ThreadState,
        processed: ProcessedMessage,
    ) -> ActionPlan:
        collected_contact = dict(contact)
        inferred_name = _nda_name_from_text(processed.raw)
        if inferred_name and "name" not in collected_contact:
            collected_contact["name"] = inferred_name

        quote_id = state.sales_actions.pricing.quote_id
        if not quote_id:
            return ActionPlan(
                action_type=ActionType.GENERATE_AGREEMENT,
                status=ActionStatus.BLOCKED,
                missing_slots=["quote_id"],
                reason="Agreement requires an existing pricing quote first.",
            )

        client_location = _agreement_location_from_text(processed.raw)

        missing: list[str] = []
        if "name" not in collected_contact:
            missing.append("name")
        if "email" not in collected_contact:
            missing.append("email")
        if "phone" not in collected_contact:
            missing.append("phone")
        if not client_location:
            missing.append("client_location")

        collected_slots = {
            **collected_contact,
            "quote_id": quote_id,
            "effective_date": date.today().isoformat(),
            **({"client_location": client_location} if client_location else {}),
        }

        if missing:
            return ActionPlan(
                action_type=ActionType.GENERATE_AGREEMENT,
                status=ActionStatus.MISSING_INFO,
                missing_slots=missing,
                collected_slots=collected_slots,
                reason="Agreement request is missing required agreement details.",
            )

        return ActionPlan(
            action_type=ActionType.GENERATE_AGREEMENT,
            status=ActionStatus.NEEDS_CONFIRMATION,
            collected_slots={**collected_slots, "send_email": False},
            confirmation_required=True,
            pending_confirmation_key=ActionType.GENERATE_AGREEMENT.value,
            reason="Agreement has quote and customer details and needs send confirmation.",
        )


def _nda_name_from_text(text: str) -> str | None:
    cleaned = re.sub(r"\s+", " ", text).strip()

    # Handles: "Use Maya Author, maya@example.com..."
    use_match = re.search(
        r"\buse\s+(.+?)(?=,|\s+and\s+make\b|\s+and\s+the\b|$)",
        cleaned,
        flags=re.IGNORECASE,
    )
    if use_match:
        candidate = _clean_nda_name_candidate(use_match.group(1))
        if candidate:
            return candidate

    patterns = [
        r"\bmy\s+name\s+is\s+(.+?)(?=,|\s+and\s+|$)",
        r"\bname\s+is\s+(.+?)(?=,|\s+and\s+|$)",
        r"\bauthor\s+name\s+is\s+(.+?)(?=,|\s+and\s+|$)",
    ]

    for pattern in patterns:
        match = re.search(pattern, cleaned, flags=re.IGNORECASE)
        if match:
            candidate = _clean_nda_name_candidate(match.group(1))
            if candidate:
                return candidate

    return None


def _clean_nda_name_candidate(value: str) -> str | None:
    candidate = value.strip(" ,.;:-")

    # Stop accidental capture if contact details leaked into the name phrase.
    candidate = re.split(r"\b[\w.+-]+@[\w.-]+\b", candidate)[0]
    candidate = re.split(r"\+?\d[\d\s().-]{4,}", candidate)[0]
    candidate = candidate.strip(" ,.;:-")

    if not candidate:
        return None

    blocked = {
        "me",
        "this",
        "the nda",
        "nda",
        "today",
        "my email",
        "email",
        "phone",
    }
    if candidate.casefold() in blocked:
        return None

    words = candidate.split()
    if len(words) > 5:
        return None

    if not all(re.match(r"^[A-Za-z][A-Za-z'.-]*$", word) for word in words):
        return None

    return " ".join(word[:1].upper() + word[1:] for word in words)


def _nda_effective_date_from_text(text: str) -> str | None:
    lowered = text.casefold()

    if "effective today" in lowered or "effective date today" in lowered:
        return date.today().isoformat()

    match = re.search(
        r"\beffective\s+(?:on\s+)?(\d{4}-\d{2}-\d{2})\b",
        lowered,
    )
    if match:
        return match.group(1)

    return None


def _agreement_location_from_text(text: str) -> str | None:
    patterns = [
        r"\blocation\s+is\s+(.+?)(?=,|\.|\s+and\s+|$)",
        r"\bi\s+am\s+in\s+(.+?)(?=,|\.|\s+and\s+|$)",
        r"\bi'm\s+in\s+(.+?)(?=,|\.|\s+and\s+|$)",
        r"\bbased\s+in\s+(.+?)(?=,|\.|\s+and\s+|$)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            candidate = match.group(1).strip(" ,.;:-")
            if 2 <= len(candidate) <= 120:
                return candidate

    return None
