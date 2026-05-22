from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from pydantic import ValidationError

from bookcraft.components.actions.schemas import ActionPlan, ActionResult, ActionStatus, ActionType
from bookcraft.components.consultations import (
    ConsultationActionRequest,
    ConsultationActionService,
)
from bookcraft.components.document_actions import (
    AgreementActionRequest,
    AgreementActionService,
    NDAActionRequest,
    NDAActionService,
)
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


def _make_idempotency_key(thread_id: UUID, action_type: str, slots: dict[str, object]) -> str:
    """Stable key: prevents double-dispatch on concurrent/retried confirmations."""
    # Sort slots for determinism; exclude per-call volatile fields.
    stable = {k: v for k, v in sorted(slots.items()) if k not in ("requested_time_text",)}
    raw = f"{thread_id}:{action_type}:{json.dumps(stable, sort_keys=True, default=str)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


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

    async def dispatch(
        self,
        plan: ActionPlan,
        *,
        thread_id: UUID,
        customer_id: UUID | None,
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
            )
        elif plan.action_type == ActionType.SCHEDULE_CONSULTATION:
            result = await self._schedule_consultation(
                plan,
                thread_id=thread_id,
                customer_id=customer_id,
                started=started,
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

        try:
            result = await self.consultation_action_service.schedule(
                ConsultationActionRequest(
                    customer_id=customer_id,
                    lead_id=lead_id,
                    thread_id=thread_id,
                    name=_string_or_none(slots.get("name")) or "",
                    email=_string_or_none(slots.get("email")),
                    phone=_string_or_none(slots.get("phone")),
                    services=[
                        str(service) for service in slots.get("services", []) if service is not None
                    ]
                    if isinstance(slots.get("services"), list)
                    else [],
                    requested_time_text=_string_or_none(slots.get("requested_time_text")) or "",
                    customer_timezone=_string_or_none(slots.get("customer_timezone")),
                    business_timezone=_string_or_none(slots.get("business_timezone"))
                    or "America/Chicago",
                    duration_minutes=_int_or_none(slots.get("duration_minutes")) or 30,
                    metadata={"action_plan_reason": plan.reason},
                )
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

    async def _create_lead(
        self,
        plan: ActionPlan,
        *,
        thread_id: UUID,
        customer_id: UUID | None,
        started: float,
    ) -> ActionResult:
        if self.lead_service is None:
            pseudo_id = str(uuid4())
            slots = plan.collected_slots
            return ActionResult(
                action_type=ActionType.CREATE_LEAD,
                success=True,
                result_id=pseudo_id,
                customer_safe_summary=(
                    "Thanks - your details are captured and a senior specialist "
                    "will follow up shortly."
                ),
                internal_summary="LeadService unavailable; synthetic lead result used.",
                payload={
                    "lead": {
                        "id": pseudo_id,
                        "name": _string_or_none(slots.get("name")),
                        "email": _string_or_none(slots.get("email")),
                        "phone": _string_or_none(slots.get("phone")),
                        "preferred_contact_method": _string_or_none(
                            slots.get("preferred_contact_method")
                        ),
                    },
                    "created": True,
                    "updated_fields": [],
                    "recommended_follow_up_slots": plan.recommended_follow_up_slots,
                    "synthetic": True,
                },
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
