from __future__ import annotations

from dataclasses import dataclass

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
            return self._pending_confirmation_plan(pending_type=pending.type)

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
            return self._nda_plan(contact=contact, state=state)

        if intent.query_primary == QueryIntentType.AGREEMENT_REQUEST:
            return self._agreement_plan(contact=contact, state=state)

        if self._is_lead_candidate(intent, contact):
            return self._lead_plan(contact=contact, services=services)

        return ActionPlan(
            status=ActionStatus.NOT_NEEDED,
            reason="No sales action required for this turn.",
        )

    def _pending_confirmation_plan(self, *, pending_type: str) -> ActionPlan:
        try:
            action_type = ActionType(pending_type)
        except ValueError:
            return ActionPlan(
                status=ActionStatus.BLOCKED,
                reason=f"Unknown pending confirmation type: {pending_type}",
            )

        return ActionPlan(
            action_type=action_type,
            status=ActionStatus.READY,
            collected_slots={"confirmed": True},
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
    def _nda_plan(*, contact: dict[str, str], state: ThreadState) -> ActionPlan:
        missing: list[str] = []

        if "name" not in contact:
            missing.append("name")
        if "email" not in contact:
            missing.append("email")
        if not state.sales_actions.documents.nda.effective_date:
            missing.append("effective_date")

        if missing:
            return ActionPlan(
                action_type=ActionType.GENERATE_NDA,
                status=ActionStatus.MISSING_INFO,
                missing_slots=missing,
                collected_slots=contact,
                reason="NDA request is missing required document parameters.",
            )

        return ActionPlan(
            action_type=ActionType.GENERATE_NDA,
            status=ActionStatus.NEEDS_CONFIRMATION,
            collected_slots={
                **contact,
                "effective_date": state.sales_actions.documents.nda.effective_date,
            },
            confirmation_required=True,
            pending_confirmation_key=ActionType.GENERATE_NDA.value,
            reason="NDA has enough details and needs send confirmation.",
        )

    @staticmethod
    def _agreement_plan(*, contact: dict[str, str], state: ThreadState) -> ActionPlan:
        quote_id = state.sales_actions.pricing.quote_id or state.commercial.latest_quote_id.value
        if not quote_id:
            return ActionPlan(
                action_type=ActionType.GENERATE_AGREEMENT,
                status=ActionStatus.BLOCKED,
                missing_slots=["quote_id"],
                reason="Agreement requires an existing pricing quote first.",
            )

        missing: list[str] = []
        if "name" not in contact:
            missing.append("name")
        if "email" not in contact:
            missing.append("email")
        if "phone" not in contact:
            missing.append("phone")

        if missing:
            return ActionPlan(
                action_type=ActionType.GENERATE_AGREEMENT,
                status=ActionStatus.MISSING_INFO,
                missing_slots=missing,
                collected_slots={**contact, "quote_id": quote_id},
                reason="Agreement request is missing customer details.",
            )

        return ActionPlan(
            action_type=ActionType.GENERATE_AGREEMENT,
            status=ActionStatus.NEEDS_CONFIRMATION,
            collected_slots={**contact, "quote_id": quote_id},
            confirmation_required=True,
            pending_confirmation_key=ActionType.GENERATE_AGREEMENT.value,
            reason="Agreement has quote and customer details and needs send confirmation.",
        )
