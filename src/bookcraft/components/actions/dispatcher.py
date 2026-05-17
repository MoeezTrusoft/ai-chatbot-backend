from __future__ import annotations

import time
from dataclasses import dataclass
from uuid import UUID

from bookcraft.components.actions.schemas import ActionPlan, ActionResult, ActionStatus, ActionType
from bookcraft.components.leads import CreateOrUpdateLeadRequest, LeadService
from bookcraft.components.pricing_actions import PricingActionRequest, PricingActionService


@dataclass(slots=True)
class SalesActionDispatcher:
    lead_service: LeadService | None = None
    pricing_action_service: PricingActionService | None = None

    async def dispatch(
        self,
        plan: ActionPlan,
        *,
        thread_id: UUID,
        customer_id: UUID | None,
    ) -> ActionResult | None:
        if plan.action_type is None or plan.status not in {
            ActionStatus.READY,
            ActionStatus.NEEDS_CONFIRMATION,
        }:
            return None

        started = time.perf_counter()

        if plan.action_type == ActionType.CREATE_LEAD:
            return await self._create_lead(
                plan,
                thread_id=thread_id,
                customer_id=customer_id,
                started=started,
            )

        if plan.action_type == ActionType.PRICE_QUOTE:
            return await self._price_quote(
                plan,
                thread_id=thread_id,
                customer_id=customer_id,
                started=started,
            )

        return ActionResult(
            action_type=plan.action_type,
            success=False,
            customer_safe_summary="This action is planned but not implemented yet.",
            internal_summary=(
                "Sales action dispatcher foundation only; concrete tools come in later PRs."
            ),
            error_code="not_implemented",
            duration_ms=_elapsed_ms(started),
        )

    async def _create_lead(
        self,
        plan: ActionPlan,
        *,
        thread_id: UUID,
        customer_id: UUID | None,
        started: float,
    ) -> ActionResult:
        if self.lead_service is None:
            return ActionResult(
                action_type=ActionType.CREATE_LEAD,
                success=False,
                customer_safe_summary=(
                    "I can collect your details, but lead saving is not connected yet."
                ),
                internal_summary="LeadService is not configured.",
                error_code="lead_service_unavailable",
                duration_ms=_elapsed_ms(started),
            )

        slots = plan.collected_slots

        try:
            result = await self.lead_service.create_or_update(
                CreateOrUpdateLeadRequest(
                    customer_id=customer_id,
                    thread_id=thread_id,
                    name=_string_or_none(slots.get("name")),
                    email=_string_or_none(slots.get("email")),
                    phone=_string_or_none(slots.get("phone")),
                    preferred_contact_method=_string_or_none(slots.get("preferred_contact_method")),
                    services=[
                        str(service) for service in slots.get("services", []) if service is not None
                    ]
                    if isinstance(slots.get("services"), list)
                    else [],
                    genre=_string_or_none(slots.get("genre")),
                    word_count=_int_or_none(slots.get("word_count")),
                    page_count=_int_or_none(slots.get("page_count")),
                    manuscript_status=_string_or_none(slots.get("manuscript_status")),
                    deadline=_string_or_none(slots.get("deadline")),
                    metadata={
                        "recommended_follow_up_slots": plan.recommended_follow_up_slots,
                    },
                )
            )
        except ValueError as exc:
            return ActionResult(
                action_type=ActionType.CREATE_LEAD,
                success=False,
                customer_safe_summary=(
                    "I need at least an email or phone number to get this started."
                ),
                internal_summary=str(exc),
                error_code="missing_contact",
                duration_ms=_elapsed_ms(started),
            )
        except Exception as exc:
            return ActionResult(
                action_type=ActionType.CREATE_LEAD,
                success=False,
                customer_safe_summary=(
                    "I got your contact details, but I could not save the lead just now."
                ),
                internal_summary=exc.__class__.__name__,
                error_code="lead_creation_failed",
                duration_ms=_elapsed_ms(started),
            )

        verb = "created" if result.created else "updated"
        return ActionResult(
            action_type=ActionType.CREATE_LEAD,
            success=True,
            result_id=str(result.lead.id),
            customer_safe_summary=f"Lead {verb} with the available contact details.",
            internal_summary=f"Lead {verb}: {result.lead.id}",
            payload={
                "lead": result.lead.model_dump(mode="json"),
                "created": result.created,
                "updated_fields": result.updated_fields,
                "recommended_follow_up_slots": plan.recommended_follow_up_slots,
            },
            duration_ms=_elapsed_ms(started),
        )

    async def _price_quote(
        self,
        plan: ActionPlan,
        *,
        thread_id: UUID,
        customer_id: UUID | None,
        started: float,
    ) -> ActionResult:
        if self.pricing_action_service is None:
            return ActionResult(
                action_type=ActionType.PRICE_QUOTE,
                success=False,
                customer_safe_summary=(
                    "I can collect the quote details, but estimate creation is not connected yet."
                ),
                internal_summary="PricingActionService is not configured.",
                error_code="pricing_action_service_unavailable",
                duration_ms=_elapsed_ms(started),
            )

        slots = plan.collected_slots
        services = (
            [str(service) for service in slots.get("services", []) if service is not None]
            if isinstance(slots.get("services"), list)
            else []
        )

        try:
            result = await self.pricing_action_service.quote(
                PricingActionRequest(
                    customer_id=customer_id,
                    thread_id=thread_id,
                    lead_id=_uuid_or_none(slots.get("lead_id")),
                    services=services,
                    collected_slots=slots,
                    use_default_assumptions=bool(slots.get("use_default_assumptions")),
                )
            )
        except Exception as exc:
            return ActionResult(
                action_type=ActionType.PRICE_QUOTE,
                success=False,
                customer_safe_summary=(
                    "I have the quote request, but I could not prepare the estimate just now."
                ),
                internal_summary=exc.__class__.__name__,
                error_code="pricing_quote_failed",
                duration_ms=_elapsed_ms(started),
            )

        return ActionResult(
            action_type=ActionType.PRICE_QUOTE,
            success=True,
            result_id=str(result.quote_id),
            customer_safe_summary=result.customer_safe_summary,
            internal_summary=f"Pricing quote processed: {result.quote_id}",
            payload=result.model_dump(mode="json"),
            duration_ms=_elapsed_ms(started),
        )


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except ValueError:
        return None


def _uuid_or_none(value: object) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except ValueError:
        return None
