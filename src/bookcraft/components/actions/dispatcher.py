from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import structlog

from pydantic import ValidationError

from bookcraft.components.actions.schemas import ActionPlan, ActionResult, ActionStatus, ActionType
from bookcraft.components.consultations import (
    AmbiguousDateError,
    ConsultationActionRequest,
    ConsultationActionService,
    HolidayError,
    RequestedTimeInPastError,
)
from bookcraft.components.document_actions import (
    AgreementActionRequest,
    AgreementActionService,
    NDAActionRequest,
    NDAActionService,
)
from bookcraft.components.consultations.service import RequestedTimeError
from bookcraft.components.leads import CreateOrUpdateLeadRequest, LeadService
from bookcraft.components.portfolio_actions import (
    PortfolioActionRequest,
    PortfolioActionService,
)
from bookcraft.components.pricing_actions import PricingActionRequest, PricingActionService
from bookcraft.components.storage.action_idempotency_repository import (
    ActionIdempotencyRepository,
    make_slots_hash,
)
from bookcraft.infra.crm_freeze import is_crm_frozen
from bookcraft.infra.sales_mcp import (
    SalesMcpClient,
    SalesMcpConfigError,
    SalesTenantNotAllowed,
    SalesTurnContext,
    consultation_availability_arguments,
    consultation_config_arguments,
    consultation_payload_from_sales,
    consultation_policy_from_sales,
    consultation_confirm_arguments,
    consultation_request_id,
    consultation_schedule_arguments,
    consultation_slot_from_sales,
    lead_create_arguments,
    lead_payload_from_sales,
    normalize_e164,
)


logger = structlog.get_logger(__name__)


def _sales_offer_handle(slots: dict[str, object]) -> tuple[str, int] | None:
    """The Sales offer a confirmed turn is accepting, if this turn is one (S7.1.2).

    Sales answers `sales_consultation_schedule` with a `Proposed` row when it cannot
    hold the exact instant. That row is an expiring offer, not a booking: accepting it
    means calling `sales_consultation_confirm` on that same row, so the handle is
    carried on the pending confirmation rather than re-scheduled as a new booking.
    """
    if not slots.get("confirmed"):
        return None
    consultation_id = _string_or_none(slots.get("sales_offer_consultation_id"))
    version = _int_or_none(slots.get("sales_offer_version"))
    if not consultation_id or version is None:
        return None
    return consultation_id, version


def _make_idempotency_key(thread_id: UUID, action_type: str, slots: dict[str, object]) -> str:
    """Stable key: prevents double-dispatch on concurrent/retried confirmations."""
    # Sort slots for determinism; exclude per-call volatile fields.
    stable = {k: v for k, v in sorted(slots.items()) if k not in ("requested_time_text",)}
    raw = f"{thread_id}:{action_type}:{json.dumps(stable, sort_keys=True, default=str)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


_DEV_ENVS: frozenset[str] = frozenset({"test", "dev", "development", "local"})


@dataclass(slots=True)
class SalesActionDispatcher:
    lead_service: LeadService | None = None
    consultation_action_service: ConsultationActionService | None = None
    pricing_action_service: PricingActionService | None = None
    portfolio_action_service: PortfolioActionService | None = None
    nda_action_service: NDAActionService | None = None
    agreement_action_service: AgreementActionService | None = None
    # Batch 4: durable idempotency repository (DB-backed in prod, in-process fallback in test).
    # When provided, claim() uses UNIQUE(idempotency_key) INSERT to prevent multi-worker
    # double-dispatch across restarts, containers, or concurrent workers.
    action_idempotency_repository: ActionIdempotencyRepository = field(
        default_factory=ActionIdempotencyRepository
    )
    # D2/S3.2.2: Sales owns the CRM write when the integration is switched on.
    # The in-process services below stay as the fallback (D3) so a Sales outage
    # degrades a turn instead of losing the enquiry.
    sales_client: SalesMcpClient | None = None
    # Tenants whose CRM Sales owns after cutover (S12.2.2). The bot's local CRM
    # tables become read-only for them, so the degraded path cannot silently
    # recreate the dual-CRM divergence the integration removes.
    frozen_crm_tenants: frozenset[str] = field(default_factory=frozenset)
    # Controls synthetic-success behaviour when lead_service is None.
    # In test/dev envs (app_env in _DEV_ENVS) a fake success is returned so e2e tests
    # can exercise post-lead flows without a real DB.  In production this must be False
    # so a missing lead_service surfaces as a hard failure rather than silent data loss.
    app_env: str = "dev"

    async def dispatch(
        self,
        plan: ActionPlan,
        *,
        thread_id: UUID,
        customer_id: UUID | None,
        sales_context: SalesTurnContext | None = None,
    ) -> ActionResult | None:
        if plan.action_type is None or plan.status != ActionStatus.READY:
            return None

        # Build or retrieve idempotency key.
        idem_key = plan.idempotency_key or _make_idempotency_key(
            thread_id, plan.action_type, plan.collected_slots
        )

        # Batch 4: durable idempotency claim — prevents double-dispatch across workers.
        claimed = await self.action_idempotency_repository.claim(
            idempotency_key=idem_key,
            thread_id=thread_id,
            action_type=str(plan.action_type),
            slots_hash=make_slots_hash(plan.collected_slots),
        )
        if not claimed:
            # Another worker or process already dispatched this action.
            return None

        started = time.perf_counter()
        result: ActionResult | None = None

        if plan.action_type == ActionType.CREATE_LEAD:
            result = await self._create_lead(
                plan,
                thread_id=thread_id,
                customer_id=customer_id,
                started=started,
                # Reusing the durable idempotency key as the Sales requestId makes
                # a retry a replay Sales dedupes, rather than a second lead
                # (S3.2.4) — and a retried turn reuses this exact key.
                request_id=idem_key,
                sales_context=sales_context,
            )
        elif plan.action_type == ActionType.SCHEDULE_CONSULTATION:
            result = await self._schedule_consultation(
                plan,
                thread_id=thread_id,
                customer_id=customer_id,
                started=started,
                # Same durable key as the lead write, so a retried booking is a
                # replay Sales dedupes rather than a second appointment (S3.2.4).
                request_id=idem_key,
                sales_context=sales_context,
            )
        elif plan.action_type == ActionType.PRICE_QUOTE:
            result = await self._price_quote(
                plan,
                thread_id=thread_id,
                customer_id=customer_id,
                started=started,
            )
        elif plan.action_type == ActionType.PORTFOLIO_LOOKUP:
            result = await self._portfolio_lookup(
                plan,
                thread_id=thread_id,
                customer_id=customer_id,
                started=started,
            )
        elif plan.action_type == ActionType.GENERATE_NDA:
            result = await self._generate_nda(
                plan,
                thread_id=thread_id,
                customer_id=customer_id,
                started=started,
            )
        elif plan.action_type == ActionType.GENERATE_AGREEMENT:
            result = await self._generate_agreement(
                plan,
                thread_id=thread_id,
                customer_id=customer_id,
                started=started,
            )

        if result is None:
            result = ActionResult(
                action_type=plan.action_type,
                success=False,
                customer_safe_summary="This action is planned but not implemented yet.",
                internal_summary=(
                    "Sales action dispatcher foundation only; concrete tools come in later PRs."
                ),
                error_code="not_implemented",
                duration_ms=_elapsed_ms(started),
            )

        # Update durable idempotency record with final status.
        if result.success:
            await self.action_idempotency_repository.mark_completed(
                idempotency_key=idem_key,
                result_summary=(result.customer_safe_summary or "")[:512],
            )
        else:
            await self.action_idempotency_repository.mark_failed(
                idempotency_key=idem_key,
                error_code=result.error_code or "unknown",
            )
        return result

    async def _schedule_consultation(
        self,
        plan: ActionPlan,
        *,
        thread_id: UUID,
        customer_id: UUID | None,
        started: float,
        request_id: str | None = None,
        sales_context: SalesTurnContext | None = None,
    ) -> ActionResult:
        # Sales owns the calendar once the integration is on and the conversation
        # has a tenant plus a Sales lead to book against (D3/S7.1.1). Anything
        # else falls through to the in-process scheduler exactly as before.
        offer = _sales_offer_handle(plan.collected_slots)
        if offer is not None and self._sales_booking_available(sales_context):
            # The customer is accepting an offer Sales already persisted, so the
            # booking is a confirm of that exact row (S7.1.2) — scheduling again
            # would ask the calendar for a slot it already refused.
            confirmed, offer_meta = await self._confirm_consultation_in_sales(
                plan,
                offer=offer,
                started=started,
                request_id=request_id,
                sales_context=sales_context,
            )
            if confirmed is not None:
                if offer_meta is not None:
                    confirmed.payload = {**confirmed.payload, "sales": offer_meta}
                return confirmed
            if self._crm_frozen(sales_context):
                # Sales owns this tenant's calendar and did not take the confirm,
                # so the local table must not become a second booking (S12.2.2).
                result = self._frozen_local_write_result(
                    ActionType.SCHEDULE_CONSULTATION, started
                )
                if offer_meta is not None:
                    result.payload = {**result.payload, "sales": offer_meta}
                return result
            result = await self._schedule_consultation_locally(
                plan,
                thread_id=thread_id,
                customer_id=customer_id,
                started=started,
            )
            if offer_meta is not None:
                result.payload = {**result.payload, "sales": offer_meta}
            return result

        sales_result, sales_meta = await self._schedule_consultation_in_sales(
            plan,
            started=started,
            request_id=request_id,
            sales_context=sales_context,
        )
        if sales_result is not None:
            if sales_meta is not None:
                # A refusal raised before the call was made still has to say so on
                # the result, exactly like the degraded local path below (D3).
                sales_result.payload = {**sales_result.payload, "sales": sales_meta}
            return sales_result

        if self._crm_frozen(sales_context):
            # S12.2.2: Sales owns this tenant's CRM and did not take the booking,
            # so the local table stays read-only rather than diverging.
            result = self._frozen_local_write_result(ActionType.SCHEDULE_CONSULTATION, started)
            if sales_meta is not None:
                result.payload = {**result.payload, "sales": sales_meta}
            return result

        result = await self._schedule_consultation_locally(
            plan,
            thread_id=thread_id,
            customer_id=customer_id,
            started=started,
        )
        if sales_meta is not None:
            # Sales was tried and did not take the booking. Keep the outcome so
            # the state layer records why the turn degraded (D3).
            result.payload = {**result.payload, "sales": sales_meta}
        return result

    def _crm_frozen(self, sales_context: SalesTurnContext | None) -> bool:
        """Whether the local CRM tables are read-only for this turn's tenant."""
        return is_crm_frozen(
            sales_context.tenant_id if sales_context else None,
            self.frozen_crm_tenants,
        )

    def _frozen_local_write_result(self, action_type: ActionType, started: float) -> ActionResult:
        """The degraded result when a frozen tenant's local CRM write is refused.

        Nothing is written locally (Sales owns the tenant's CRM); the visitor is
        told the truth — the details were not saved — rather than being shown a
        success that only exists in a table Sales will never read.
        """
        return ActionResult(
            action_type=action_type,
            success=False,
            error_code="crm_frozen",
            customer_safe_summary=(
                "I couldn't reach our sales system just now, so I haven't saved "
                "that yet. Please try again in a moment."
            ),
            internal_summary="local CRM write refused: tenant is frozen (S12.2.2)",
            duration_ms=_elapsed_ms(started),
        )

    def _sales_booking_available(self, sales_context: SalesTurnContext | None) -> bool:
        """True when this turn may write to Sales at all (D3/S7.1.4).

        `CHATBOT_LOCAL_SCHEDULER=true` keeps the in-process scheduler for standalone
        and dev deployments that have no Sales to book against.
        """
        if self.sales_client is None or not self.sales_client.enabled:
            return False
        if sales_context is None or not sales_context.usable or not sales_context.lead_id:
            return False
        return not self.sales_client.local_scheduler

    async def _confirm_consultation_in_sales(
        self,
        plan: ActionPlan,
        *,
        offer: tuple[str, int],
        started: float,
        request_id: str | None,
        sales_context: SalesTurnContext | None,
    ) -> tuple[ActionResult | None, dict[str, object] | None]:
        """Confirm a persisted, unexpired Sales offer (S7.1.2).

        `(None, meta)` means the confirm did not land, and the caller degrades to the
        local scheduler with the reason recorded rather than losing the booking.
        """
        assert self.sales_client is not None and sales_context is not None
        idem_key = request_id or plan.idempotency_key
        if not idem_key:
            return None, None
        consultation_id, expected_version = offer
        call = await self.sales_client.call_tool(
            "sales_consultation_confirm",
            consultation_confirm_arguments(
                consultation_id=consultation_id,
                expected_version=expected_version,
                request_id=consultation_request_id(str(idem_key)),
            ),
            tenant_id=sales_context.tenant_id,
            site_id=sales_context.site_id,
        )
        meta: dict[str, object] = {
            "tool": "sales_consultation_confirm",
            "request_id": idem_key,
            "ok": call.ok,
            "code": call.code,
            "sales_code": call.sales_code,
            "degraded": call.degraded,
            "attempts": call.attempts,
        }
        if not call.ok or not isinstance(call.data, dict):
            return None, meta
        consultation = consultation_payload_from_sales(call.data)
        if not consultation.get("id"):
            return None, {**meta, "ok": False, "code": "sales_invalid_response"}
        status = str(consultation.get("status") or "")
        # A confirm that comes back Proposed again is not a held slot either; the
        # reply layer must not claim a booking the tenant still holds as an offer.
        return (
            ActionResult(
                action_type=ActionType.SCHEDULE_CONSULTATION,
                success=True,
                result_id=str(consultation["id"]),
                customer_safe_summary=(
                    "You're booked in. I've sent the details to our sales team."
                    if status != "Proposed"
                    else (
                        "That time still isn't held on our side — could you pick "
                        "another day or time?"
                    )
                ),
                internal_summary=(
                    f"Consultation confirmed in Sales: {consultation['id']} ({status})"
                ),
                payload={
                    "consultation": consultation,
                    "created": status != "Proposed",
                    "requested_time_available": True,
                    "sales": meta,
                    "recommended_follow_up_slots": plan.recommended_follow_up_slots,
                },
                duration_ms=_elapsed_ms(started),
            ),
            meta,
        )

    async def _schedule_consultation_locally(
        self,
        plan: ActionPlan,
        *,
        thread_id: UUID,
        customer_id: UUID | None,
        started: float,
    ) -> ActionResult:
        if self.consultation_action_service is None:
            return ActionResult(
                action_type=ActionType.SCHEDULE_CONSULTATION,
                success=False,
                customer_safe_summary=(
                    "I can collect the consultation details, but scheduling is not connected yet."
                ),
                internal_summary="ConsultationActionService is not configured.",
                error_code="consultation_action_service_unavailable",
                duration_ms=_elapsed_ms(started),
            )

        slots = plan.collected_slots
        lead_id: UUID | None = _uuid_or_none(slots.get("lead_id"))

        if self.lead_service is not None:
            try:
                lead_result = await self.lead_service.create_or_update(
                    CreateOrUpdateLeadRequest(
                        customer_id=customer_id,
                        thread_id=thread_id,
                        name=_string_or_none(slots.get("name")),
                        email=_string_or_none(slots.get("email")),
                        phone=_string_or_none(slots.get("phone")),
                        preferred_contact_method=_string_or_none(
                            slots.get("preferred_contact_method")
                        ),
                        services=[
                            str(service)
                            for service in slots.get("services", [])
                            if service is not None
                        ]
                        if isinstance(slots.get("services"), list)
                        else [],
                        genre=_string_or_none(slots.get("genre")),
                        word_count=_int_or_none(slots.get("word_count")),
                        page_count=_int_or_none(slots.get("page_count")),
                        manuscript_status=_string_or_none(slots.get("manuscript_status")),
                        deadline=_string_or_none(slots.get("deadline")),
                        metadata={"source_action": "schedule_consultation"},
                    )
                )
                lead_id = lead_result.lead.id
            except Exception as exc:
                return ActionResult(
                    action_type=ActionType.SCHEDULE_CONSULTATION,
                    success=False,
                    customer_safe_summary=(
                        "I have the consultation details, but I could not save the lead "
                        "before booking just now."
                    ),
                    internal_summary=_exception_summary(exc),
                    error_code="consultation_lead_creation_failed",
                    duration_ms=_elapsed_ms(started),
                )

        name_val = _string_or_none(slots.get("name"))
        if not name_val:
            return ActionResult(
                success=False,
                action_type=ActionType.SCHEDULE_CONSULTATION,
                error_code="missing_contact",
                internal_summary="Customer name is missing — cannot schedule consultation",
                duration_ms=_elapsed_ms(started),
            )

        try:
            result = await self.consultation_action_service.schedule(
                ConsultationActionRequest(
                    customer_id=customer_id,
                    lead_id=lead_id,
                    thread_id=thread_id,
                    name=name_val,
                    email=_string_or_none(slots.get("email")),
                    phone=_string_or_none(slots.get("phone")),
                    services=[
                        str(service) for service in slots.get("services", []) if service is not None
                    ]
                    if isinstance(slots.get("services"), list)
                    else [],
                    requested_time_text=_string_or_none(slots.get("requested_time_text")) or "",
                    customer_timezone=_string_or_none(slots.get("customer_timezone"))
                    or _string_or_none(slots.get("timezone"))
                    # Unify the assumed customer timezone with the chat layer default
                    # (chat.py uses America/Chicago) so the booked time no longer depends
                    # on which code path built the request.
                    or "America/Chicago",
                    business_timezone=_string_or_none(slots.get("business_timezone"))
                    or "America/Chicago",
                    duration_minutes=_int_or_none(slots.get("duration_minutes")) or 30,
                    metadata={"action_plan_reason": plan.reason},
                )
            )
        except RequestedTimeInPastError as exc:
            return ActionResult(
                action_type=ActionType.SCHEDULE_CONSULTATION,
                success=False,
                customer_safe_summary=(
                    "That time has already passed — our specialists are available "
                    "Monday–Friday, 10 AM to 7 PM Central Time. What upcoming day and "
                    "time works best for you?"
                ),
                internal_summary=_exception_summary(exc),
                error_code="requested_time_in_past",
                duration_ms=_elapsed_ms(started),
            )
        except AmbiguousDateError as exc:
            return ActionResult(
                action_type=ActionType.SCHEDULE_CONSULTATION,
                success=False,
                customer_safe_summary=(
                    "I want to get the date right — could you confirm the exact day? "
                    "For example, 'Monday, June 22' works best."
                ),
                internal_summary=_exception_summary(exc),
                error_code="ambiguous_requested_date",
                duration_ms=_elapsed_ms(started),
            )
        except HolidayError as exc:
            return ActionResult(
                action_type=ActionType.SCHEDULE_CONSULTATION,
                success=False,
                customer_safe_summary=(
                    "We're closed that day, so I can't book a consultation then. "
                    "Our specialists are available Monday–Friday, 10 AM to 7 PM "
                    "Central Time — what other day works for you?"
                ),
                internal_summary=_exception_summary(exc),
                error_code="requested_date_is_holiday",
                duration_ms=_elapsed_ms(started),
            )
        except Exception as exc:
            return ActionResult(
                action_type=ActionType.SCHEDULE_CONSULTATION,
                success=False,
                customer_safe_summary=(
                    "I have the consultation request, but I could not book the slot just now."
                ),
                internal_summary=_exception_summary(exc),
                error_code="consultation_scheduling_failed",
                payload={
                    "exception_class": exc.__class__.__name__,
                    "exception_detail": _exception_summary(exc),
                },
                duration_ms=_elapsed_ms(started),
            )

        return ActionResult(
            action_type=ActionType.SCHEDULE_CONSULTATION,
            success=True,
            result_id=str(result.appointment_id),
            customer_safe_summary=result.customer_safe_summary,
            internal_summary=f"Consultation scheduled: {result.appointment_id}",
            payload=result.model_dump(mode="json"),
            duration_ms=_elapsed_ms(started),
        )

    async def _schedule_consultation_in_sales(
        self,
        plan: ActionPlan,
        *,
        started: float,
        request_id: str | None,
        sales_context: SalesTurnContext | None,
    ) -> tuple[ActionResult | None, dict[str, object] | None]:
        """Try the booking in Sales first when the integration is switched on (D3).

        `(None, None)` means Sales is not the right actor for this turn, so the
        caller proceeds on the local path exactly as before. `(None, meta)` means
        Sales was asked and declined: the booking still has to happen somewhere, so
        the local path runs with the reason recorded for the state layer.
        """
        if not self._sales_booking_available(sales_context):
            return None, None
        client = self.sales_client
        assert client is not None and sales_context is not None
        lead_id = sales_context.lead_id
        assert lead_id
        service = self.consultation_action_service
        if service is None:
            return None, None
        idem_key = request_id or plan.idempotency_key
        if not idem_key:
            # No stable key means the call could not be deduped server-side, and
            # a duplicated booking is worse than a local one.
            return None, None

        slots = plan.collected_slots
        name = _string_or_none(slots.get("name"))
        if not name:
            # The local path owns the missing-contact reply.
            return None, None
        raw_phone = _string_or_none(slots.get("phone"))
        phone = normalize_e164(raw_phone)
        if raw_phone and phone is None:
            # Sales requires a E.164 phone on the lead's customer, so a number we
            # cannot interpret is a booking it will refuse after the visitor has
            # already agreed a time. Say so now, plainly (S7.2.1).
            return (
                ActionResult(
                    action_type=ActionType.SCHEDULE_CONSULTATION,
                    success=False,
                    customer_safe_summary=(
                        "I have the details, but that phone number doesn't look "
                        "callable. Could you share it with the country code, "
                        "for example +1 415 555 2671?"
                    ),
                    internal_summary="Phone number is not a valid E.164 number",
                    error_code="invalid_phone",
                    duration_ms=_elapsed_ms(started),
                ),
                {
                    "tool": "sales_consultation_schedule",
                    "request_id": idem_key,
                    "ok": False,
                    "code": "invalid_phone",
                    "degraded": False,
                },
            )

        customer_timezone = (
            _string_or_none(slots.get("customer_timezone"))
            or _string_or_none(slots.get("timezone"))
            or "America/Chicago"
        )
        business_timezone = _string_or_none(slots.get("business_timezone")) or "America/Chicago"

        # S7.2.2/S7.3.1: Sales owns the consultation calendar, so read its policy
        # before interpreting the request. The window, timezone, weekday grid and
        # holiday blackout dates from the policy replace the bot's hard-coded
        # 10–19 America/Chicago assumptions. A failed or empty read keeps the local
        # defaults — a policy lookup must never cost the visitor their booking.
        policy_call = await client.call_tool(
            "sales_consultation_config_get",
            consultation_config_arguments(),
            tenant_id=sales_context.tenant_id,
            site_id=sales_context.site_id,
        )
        policy = (
            consultation_policy_from_sales(policy_call.data)
            if policy_call.ok and isinstance(policy_call.data, dict)
            else {}
        )
        policy_meta: dict[str, object] = {
            "ok": policy_call.ok,
            "code": policy_call.code,
            "sales_code": policy_call.sales_code,
            "degraded": policy_call.degraded,
            "applied": bool(policy),
        }
        if policy.get("timezone"):
            business_timezone = str(policy["timezone"])
        policy_start_hour, policy_end_hour = _policy_business_hours(policy)
        policy_weekdays = _policy_weekdays(policy)
        policy_blackout = _policy_blackout_dates(policy)

        services = slots.get("services")
        service_names = (
            [str(item) for item in services if item is not None]
            if isinstance(services, list)
            else []
        )
        action_request = ConsultationActionRequest(
            customer_id=None,
            lead_id=None,
            thread_id=sales_context.thread_id,
            name=name,
            email=_string_or_none(slots.get("email")),
            phone=phone,
            services=service_names,
            requested_time_text=_string_or_none(slots.get("requested_time_text")) or "",
            customer_timezone=customer_timezone,
            business_timezone=business_timezone,
            duration_minutes=_int_or_none(slots.get("duration_minutes")) or 30,
            metadata={"action_plan_reason": plan.reason},
        )
        try:
            # Reuse the local resolver so "Tuesday 3pm" means the same instant on
            # both paths; Sales only owns the calendar, not the interpretation —
            # which is why its policy is handed in as an override (S7.2.2/S7.3.1).
            requested_start = service.resolve_requested_start(
                action_request,
                business_start_hour=policy_start_hour,
                business_end_hour=policy_end_hour,
                business_weekdays=policy_weekdays,
                blackout_dates=policy_blackout,
            )
        except RequestedTimeError:
            # Hand the turn to the local path: its error mapping is the one the
            # reply layer already voices for a past/holiday/ambiguous date.
            return None, None

        availability = await client.call_tool(
            "sales_consultation_availability",
            consultation_availability_arguments(
                lead_id=lead_id,
                start_time=requested_start.isoformat(),
                customer_timezone=customer_timezone,
            ),
            tenant_id=sales_context.tenant_id,
            site_id=sales_context.site_id,
        )
        meta: dict[str, object] = {
            "tool": "sales_consultation_availability",
            "request_id": idem_key,
            "ok": availability.ok,
            "code": availability.code,
            "sales_code": availability.sales_code,
            "degraded": availability.degraded,
            "attempts": availability.attempts,
            "policy": policy_meta,
        }
        if not availability.ok or not isinstance(availability.data, dict):
            return None, meta
        offered = consultation_slot_from_sales(availability.data)
        if not offered:
            return None, {**meta, "ok": False, "code": "sales_no_availability"}
        chosen = offered[0]
        start_time = str(chosen.get("startTime") or "")
        if not start_time:
            return None, {**meta, "ok": False, "code": "sales_invalid_response"}

        owner_id = chosen.get("ownerId")
        schedule_call = await client.call_tool(
            "sales_consultation_schedule",
            consultation_schedule_arguments(
                lead_id=lead_id,
                start_time=start_time,
                customer_timezone=customer_timezone,
                request_id=consultation_request_id(str(idem_key)),
                preferred_owner_id=str(owner_id) if owner_id else None,
            ),
            tenant_id=sales_context.tenant_id,
            site_id=sales_context.site_id,
        )
        meta = {
            "tool": "sales_consultation_schedule",
            "request_id": idem_key,
            "ok": schedule_call.ok,
            "code": schedule_call.code,
            "sales_code": schedule_call.sales_code,
            "degraded": schedule_call.degraded,
            "attempts": schedule_call.attempts,
            "policy": policy_meta,
        }
        if not schedule_call.ok or not isinstance(schedule_call.data, dict):
            return None, meta
        consultation = consultation_payload_from_sales(schedule_call.data)
        if not consultation.get("id"):
            return None, {**meta, "ok": False, "code": "sales_invalid_response"}

        status = str(consultation.get("status") or "")
        if status == "Proposed":
            # A Proposed result is an expiring offer, never a held slot — claiming
            # "booked" here is the error that produced duplicate form submissions.
            summary = (
                "I can offer you that time, but it isn't held yet — "
                "shall I confirm it for you?"
            )
        else:
            summary = "You're booked in. I've sent the details to our sales team."
        return (
            ActionResult(
                action_type=ActionType.SCHEDULE_CONSULTATION,
                success=True,
                result_id=str(consultation["id"]),
                customer_safe_summary=summary,
                internal_summary=(
                    f"Consultation scheduled in Sales: {consultation['id']} ({status})"
                ),
                payload={
                    "consultation": consultation,
                    "created": status != "Proposed",
                    "requested_time_available": start_time == requested_start.isoformat(),
                    "sales": meta,
                    "recommended_follow_up_slots": plan.recommended_follow_up_slots,
                },
                duration_ms=_elapsed_ms(started),
            ),
            meta,
        )

    async def _create_lead(
        self,
        plan: ActionPlan,
        *,
        thread_id: UUID,
        customer_id: UUID | None,
        started: float,
        request_id: str | None = None,
        sales_context: SalesTurnContext | None = None,
    ) -> ActionResult:
        sales_result, sales_meta = await self._create_lead_in_sales(
            plan,
            customer_id=customer_id,
            started=started,
            request_id=request_id,
            sales_context=sales_context,
        )
        if sales_result is not None:
            return sales_result

        if self._crm_frozen(sales_context):
            # S12.2.2: a cut-over tenant's leads live in Sales. With Sales not
            # taking this one, the local write is refused instead of creating a
            # second lead only the bot can see.
            result = self._frozen_local_write_result(ActionType.CREATE_LEAD, started)
            if sales_meta is not None:
                result.payload = {**result.payload, "sales": sales_meta}
            return result

        result = await self._create_lead_locally(
            plan,
            thread_id=thread_id,
            customer_id=customer_id,
            started=started,
        )
        if sales_meta is not None:
            # Sales was tried and did not take the lead. Keep the outcome on the
            # result so the state layer can record why the turn degraded (D3).
            result.payload = {**result.payload, "sales": sales_meta}
        return result

    async def _create_lead_in_sales(
        self,
        plan: ActionPlan,
        *,
        customer_id: UUID | None,
        started: float,
        request_id: str | None,
        sales_context: SalesTurnContext | None,
    ) -> tuple[ActionResult | None, dict[str, object] | None]:
        """Try the CRM write in Sales first when the integration is switched on.

        Returns the authoritative result plus the metadata to record. `(None, None)`
        means Sales is not configured for this turn, and the caller proceeds on the
        local path exactly as before.
        """
        if self.sales_client is None or not self.sales_client.enabled:
            return None, None
        if sales_context is None or not sales_context.usable:
            # No verified tenant/site on the turn: Sales would refuse the call, and
            # guessing a tenant is exactly what the allow-list exists to prevent.
            logger.info(
                "sales_mcp_skipped_no_tenant",
                thread_id=str(sales_context.thread_id if sales_context else None),
            )
            return None, None

        tool = "sales_lead_create"
        try:
            binding = self.sales_client.resolve_binding(
                tenant_id=sales_context.tenant_id, site_id=sales_context.site_id
            )
            arguments = lead_create_arguments(
                plan.collected_slots,
                binding=binding,
                customer_id=customer_id,
                request_id=request_id,
                # S6.2.1: the thread's funnel stage travels with the lead; Sales
                # maps it with the frozen `mapSalesStage` instead of filing the
                # chat lead at its default stage.
                stage=plan.sales_stage,
                # S6.3.1: the page/keyword the visitor arrived on (from the
                # Sales-signed turn) rides along, so the lead keeps its
                # attribution instead of starting blank.
                landing_page=sales_context.landing_page,
                landing_keyword=sales_context.landing_keyword,
            )
        except (SalesTenantNotAllowed, SalesMcpConfigError) as exc:
            # Two different problems land here: the turn names a tenant/site Sales
            # does not allow, or the payload cannot be described on the wire (e.g.
            # a funnel stage the frozen map does not key). Both degrade locally,
            # but the recorded code must say which, or an operator chasing a
            # missing lead is sent to the allow-list for an argument bug.
            return None, {
                "tool": tool,
                "request_id": request_id,
                "ok": False,
                "code": (
                    "sales_tenant_not_allowed"
                    if isinstance(exc, SalesTenantNotAllowed)
                    else "sales_invalid_arguments"
                ),
                "degraded": True,
                "detail": str(exc)[:200],
            }

        outcome = await self.sales_client.call_tool(
            tool,
            arguments,
            tenant_id=binding.tenant_id,
            site_id=binding.site_id,
            request_id=request_id,
        )
        meta: dict[str, object] = {
            "tool": tool,
            "request_id": request_id,
            "ok": outcome.ok,
            "code": outcome.code,
            "sales_code": outcome.sales_code,
            "degraded": outcome.degraded,
            "attempts": outcome.attempts,
        }
        if not outcome.ok or not isinstance(outcome.data, dict):
            return None, meta

        lead = lead_payload_from_sales(outcome.data, contact=plan.collected_slots)
        if not lead.get("id"):
            # A success without an id is a contract violation: treat it as a
            # failure so the enquiry still lands on the local path.
            meta["ok"] = False
            meta["code"] = "sales_invalid_response"
            return None, meta

        return (
            ActionResult(
                action_type=ActionType.CREATE_LEAD,
                success=True,
                result_id=str(lead["id"]),
                customer_safe_summary="Got it! I've passed your details to our sales team.",
                internal_summary=f"Lead created in Sales: {lead['id']}",
                payload={
                    "lead": lead,
                    "created": True,
                    "sales": meta,
                    "recommended_follow_up_slots": plan.recommended_follow_up_slots,
                },
                duration_ms=_elapsed_ms(started),
            ),
            meta,
        )

    async def _create_lead_locally(
        self,
        plan: ActionPlan,
        *,
        thread_id: UUID,
        customer_id: UUID | None,
        started: float,
    ) -> ActionResult:
        if self.lead_service is None:
            if self.app_env in _DEV_ENVS:
                # Dev/test: return synthetic success so e2e tests can exercise post-lead flows.
                return ActionResult(
                    success=True,
                    action_type=ActionType.CREATE_LEAD,
                    customer_safe_summary="Got it! I've noted your details.",
                    internal_summary="synthetic lead (no lead_service in dev/test env)",
                    payload={"synthetic": True},
                    duration_ms=_elapsed_ms(started),
                )
            logger.critical(
                "lead_service_not_configured",
                message="lead_service is None — no lead will be created; check startup config",
            )
            return ActionResult(
                success=False,
                action_type=ActionType.CREATE_LEAD,
                error_code="service_unavailable",
                customer_safe_summary="I got your details but couldn't save them right now.",
                internal_summary="lead_service is None — check startup configuration",
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
            logger.error(
                "lead_creation_exception",
                exc_class=exc.__class__.__name__,
                detail=str(exc)[:500],
                thread_id=str(getattr(self, "_current_thread_id", "unknown")),
            )
            return ActionResult(
                action_type=ActionType.CREATE_LEAD,
                success=False,
                customer_safe_summary=(
                    "I got your contact details, but I could not save the lead just now."
                ),
                internal_summary=f"{exc.__class__.__name__}: {str(exc)[:200]}",
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

    async def _generate_agreement(
        self,
        plan: ActionPlan,
        *,
        thread_id: UUID,
        customer_id: UUID | None,
        started: float,
    ) -> ActionResult:
        if self.agreement_action_service is None:
            return ActionResult(
                action_type=ActionType.GENERATE_AGREEMENT,
                success=False,
                customer_safe_summary=(
                    "I can collect the agreement details, but agreement generation "
                    "is not connected yet."
                ),
                internal_summary="AgreementActionService is not configured.",
                error_code="agreement_action_service_unavailable",
                duration_ms=_elapsed_ms(started),
            )

        slots = plan.collected_slots
        try:
            result = await self.agreement_action_service.generate_and_maybe_send(
                AgreementActionRequest(
                    customer_id=customer_id,
                    thread_id=thread_id,
                    lead_id=_uuid_or_none(slots.get("lead_id")),
                    quote_id=_uuid_or_none(slots.get("quote_id")),
                    client_full_name=_string_or_none(slots.get("name")) or "",
                    client_phone=_string_or_none(slots.get("phone")) or "",
                    client_email=_string_or_none(slots.get("email")) or "",
                    client_location=_string_or_none(slots.get("client_location")) or "",
                    effective_date=_string_or_none(slots.get("effective_date")) or "",
                    signature=_string_or_none(slots.get("signature"))
                    or _string_or_none(slots.get("name")),
                    send_email=bool(slots.get("send_email")),
                    metadata={"action_plan_reason": plan.reason},
                )
            )
        except Exception as exc:
            return ActionResult(
                action_type=ActionType.GENERATE_AGREEMENT,
                success=False,
                customer_safe_summary=(
                    "I have the agreement request, but I could not prepare it just now."
                ),
                internal_summary=_exception_summary(exc),
                error_code="agreement_generation_failed",
                payload={
                    "exception_class": exc.__class__.__name__,
                    "exception_detail": _exception_summary(exc),
                },
                duration_ms=_elapsed_ms(started),
            )

        return ActionResult(
            action_type=ActionType.GENERATE_AGREEMENT,
            success=True,
            result_id=result.document_id,
            customer_safe_summary=result.customer_safe_summary,
            internal_summary=f"Agreement action processed: {result.document_id}",
            payload=result.model_dump(mode="json"),
            duration_ms=_elapsed_ms(started),
        )

    async def _generate_nda(
        self,
        plan: ActionPlan,
        *,
        thread_id: UUID,
        customer_id: UUID | None,
        started: float,
    ) -> ActionResult:
        if self.nda_action_service is None:
            return ActionResult(
                action_type=ActionType.GENERATE_NDA,
                success=False,
                customer_safe_summary=(
                    "I can collect the NDA details, but NDA generation is not connected yet."
                ),
                internal_summary="NDAActionService is not configured.",
                error_code="nda_action_service_unavailable",
                duration_ms=_elapsed_ms(started),
            )

        slots = plan.collected_slots
        try:
            result = await self.nda_action_service.generate_and_maybe_send(
                NDAActionRequest(
                    customer_id=customer_id,
                    thread_id=thread_id,
                    lead_id=_uuid_or_none(slots.get("lead_id")),
                    author_title=_string_or_none(slots.get("author_title")) or "Author",
                    author_full_name=_string_or_none(slots.get("name")) or "",
                    author_phone=_string_or_none(slots.get("phone")) or "",
                    author_email=_string_or_none(slots.get("email")) or "",
                    effective_date=_string_or_none(slots.get("effective_date")) or "",
                    signature=_string_or_none(slots.get("signature"))
                    or _string_or_none(slots.get("name")),
                    send_email=bool(slots.get("send_email")),
                    metadata={"action_plan_reason": plan.reason},
                )
            )
        except Exception as exc:
            return ActionResult(
                action_type=ActionType.GENERATE_NDA,
                success=False,
                customer_safe_summary=(
                    "I have the NDA request, but I could not prepare it just now."
                ),
                internal_summary=_exception_summary(exc),
                error_code="nda_generation_failed",
                payload={
                    "exception_class": exc.__class__.__name__,
                    "exception_detail": _exception_summary(exc),
                },
                duration_ms=_elapsed_ms(started),
            )

        return ActionResult(
            action_type=ActionType.GENERATE_NDA,
            success=True,
            result_id=result.document_id,
            customer_safe_summary=result.customer_safe_summary,
            internal_summary=f"NDA action processed: {result.document_id}",
            payload=result.model_dump(mode="json"),
            duration_ms=_elapsed_ms(started),
        )

    async def _portfolio_lookup(
        self,
        plan: ActionPlan,
        *,
        thread_id: UUID,
        customer_id: UUID | None,
        started: float,
    ) -> ActionResult:
        if self.portfolio_action_service is None:
            return ActionResult(
                action_type=ActionType.PORTFOLIO_LOOKUP,
                success=False,
                customer_safe_summary=(
                    "I can collect the sample request, but sample lookup is not connected yet."
                ),
                internal_summary="PortfolioActionService is not configured.",
                error_code="portfolio_action_service_unavailable",
                duration_ms=_elapsed_ms(started),
            )

        slots = plan.collected_slots
        service = _string_or_none(slots.get("service"))
        if service is None:
            return ActionResult(
                action_type=ActionType.PORTFOLIO_LOOKUP,
                success=False,
                customer_safe_summary=("I need to know which service you want samples for first."),
                internal_summary="Portfolio lookup missing service.",
                error_code="missing_portfolio_service",
                duration_ms=_elapsed_ms(started),
            )

        try:
            result = await self.portfolio_action_service.lookup(
                PortfolioActionRequest(
                    customer_id=customer_id,
                    thread_id=thread_id,
                    service=service,
                    genre=_string_or_none(slots.get("genre")),
                    exclude_sample_ids=[
                        str(sample_id) for sample_id in slots.get("exclude_sample_ids", [])
                    ]
                    if isinstance(slots.get("exclude_sample_ids"), list)
                    else [],
                    limit=_int_or_none(slots.get("limit")) or 3,
                )
            )
        except Exception as exc:
            return ActionResult(
                action_type=ActionType.PORTFOLIO_LOOKUP,
                success=False,
                customer_safe_summary=(
                    "I have the sample request, but I could not fetch samples just now."
                ),
                internal_summary=exc.__class__.__name__,
                error_code="portfolio_lookup_failed",
                duration_ms=_elapsed_ms(started),
            )

        return ActionResult(
            action_type=ActionType.PORTFOLIO_LOOKUP,
            success=True,
            result_id=",".join(result.sample_ids) if result.sample_ids else None,
            customer_safe_summary=result.customer_safe_summary,
            internal_summary=(f"Portfolio lookup processed for {result.service}: {result.status}"),
            payload=result.model_dump(mode="json"),
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


def _policy_business_hours(policy: dict[str, object]) -> tuple[int | None, int | None]:
    """The policy's open/close minutes as whole start/end hours (S7.2.2).

    The resolver works in hours; the policy carries minutes-of-day. A missing
    bound stays `None` so the resolver keeps the tenant's local default.
    """
    open_minute = _int_or_none(policy.get("open_minute"))
    close_minute = _int_or_none(policy.get("close_minute"))
    start_hour = open_minute // 60 if open_minute is not None else None
    end_hour = (close_minute + 59) // 60 if close_minute is not None else None
    return start_hour, end_hour


def _policy_weekdays(policy: dict[str, object]) -> frozenset[int] | None:
    """The policy's working-weekday grid, or `None` to keep the local default."""
    raw = policy.get("weekdays")
    if isinstance(raw, list) and raw:
        return frozenset(day for day in raw if isinstance(day, int) and 0 <= day <= 6)
    return None


def _policy_blackout_dates(policy: dict[str, object]) -> frozenset[str] | None:
    """The policy's holiday blackout dates (S7.3.1), or `None` when unknown.

    An empty set is meaningful: Sales owns the calendar, so "no blackout dates"
    must not fall back to the bot's own table.
    """
    raw = policy.get("blackout_dates")
    if not isinstance(raw, list):
        return None
    return frozenset(day for day in raw if isinstance(day, str))


def _uuid_or_none(value: object) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except ValueError:
        return None


def _exception_summary(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return json.dumps(exc.errors(include_url=False), default=str)[:3000]

    detail = str(exc).strip()
    if detail:
        return f"{exc.__class__.__name__}: {detail}"[:3000]

    return exc.__class__.__name__
