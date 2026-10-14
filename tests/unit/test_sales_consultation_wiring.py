"""Delegating a consultation booking to Sales over MCP (P7 / S7.1.1–S7.1.4).

Sales owns the CRM of record, so once the integration is on a booking has to land
in Sales' calendar rather than the bot's own tables. These tests pin the three
things that go wrong silently: booking twice for one request, claiming an expiring
*offer* is a held slot, and losing the booking entirely when Sales declines (it has
to degrade to the local scheduler, not vanish).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bookcraft.components.actions.dispatcher import SalesActionDispatcher
from bookcraft.components.actions.schemas import ActionPlan, ActionResult, ActionStatus, ActionType
from bookcraft.components.consultations.schemas import (
    ConsultationActionRequest,
    ConsultationActionResult,
)
from bookcraft.domain.state import ThreadState
from bookcraft.infra.config import Settings
from bookcraft.infra.sales_mcp import SalesMcpClient, SalesTurnContext
from bookcraft.services.chat import ChatService

TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
THREAD_ID = UUID("22222222-2222-4222-8222-222222222222")
LEAD_ID = "33333333-3333-4333-8333-333333333333"
CONSULTATION_ID = "44444444-4444-4444-8444-444444444444"
SITE_ID = "bookcraft-main"
START = datetime(2026, 11, 3, 21, 0)


class StubConsultationService:
    """The in-process scheduler, standing in for the local booking path (D3)."""

    def __init__(self) -> None:
        self.local_calls = 0
        # What the Sales path handed to the shared time seam, if anything.
        self.resolved: list[ConsultationActionRequest] = []
        self.resolved_start = START

    def resolve_requested_start(self, request: ConsultationActionRequest) -> datetime:
        self.resolved.append(request)
        return self.resolved_start

    async def schedule(self, request: ConsultationActionRequest) -> ConsultationActionResult:
        self.local_calls += 1
        return ConsultationActionResult(
            appointment_id=UUID("99999999-9999-4999-8999-999999999999"),
            csr_id="csr-local",
            csr_name="Local CSR",
            priority_rank=1,
            starts_at_utc=START,
            ends_at_utc=START,
            houston_display_time="3:00 PM",
            customer_display_time="1:00 PM",
            status="Scheduled",
            customer_safe_summary="You're booked in locally.",
        )


class FakeConsultationSales:
    """A Sales MCP endpoint that only knows the two consultation tools we call."""

    def __init__(
        self,
        *,
        slots: list[dict[str, Any]] | None = None,
        status: str = "Scheduled",
        deny: str | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.consultations: dict[str, dict[str, Any]] = {}
        self.by_request_id: dict[str, str] = {}
        # requestId → first outcome, mirroring Sales' mutation receipts.
        self.receipts: dict[str, dict[str, Any]] = {}
        self.slots = (
            slots if slots is not None else [{"startTime": START.isoformat(), "ownerId": "csr-7"}]
        )
        self.status = status
        self.deny = deny

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        tool = body["params"]["name"]
        arguments = body["params"]["arguments"]
        self.calls.append({"tool": tool, "arguments": arguments, "id": body["id"]})
        if self.deny == tool:
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "error": {
                        "code": -32000,
                        "message": "forbidden",
                        "data": {"code": "SALES-0403"},
                    },
                },
            )
        if tool == "sales_consultation_availability":
            return _text(body["id"], {"slots": self.slots})
        if tool == "sales_consultation_schedule":
            return self._schedule(body["id"], arguments)
        if tool == "sales_consultation_confirm":
            return self._confirm(body["id"], arguments)
        raise AssertionError(f"unexpected tool {tool}")

    def _confirm(self, call_id: object, arguments: dict[str, Any]) -> httpx.Response:
        request_id = str(arguments.get("requestId") or "")
        if request_id and request_id in self.receipts:
            # Sales keeps an idempotency receipt per requestId and replays the
            # first outcome, so a retried confirm is not a second booking.
            return _text(call_id, self.receipts[request_id])
        row = self.consultations.get(str(arguments.get("consultationId") or ""))
        if row is None:
            return _error(call_id, "SALES-0404")
        if int(arguments.get("expectedVersion")) != int(row.get("version") or 1):
            # Stale version: Sales refuses rather than booking over a newer row.
            return _error(call_id, "SALES-0409")
        held = {**row, "status": "Scheduled", "version": int(row.get("version") or 1) + 1}
        self.consultations[str(held["id"])] = held
        if request_id:
            self.receipts[request_id] = held
        return _text(call_id, held)

    def _schedule(self, call_id: object, arguments: dict[str, Any]) -> httpx.Response:
        request_id = str(arguments.get("requestId") or "")
        if request_id and request_id in self.by_request_id:
            # Sales replays the first outcome for a repeat of the same request.
            return _text(call_id, self.consultations[self.by_request_id[request_id]])
        consultation = {
            "id": CONSULTATION_ID,
            "tenantId": str(TENANT_ID),
            "leadId": arguments.get("leadId"),
            "status": self.status,
            "ownerId": arguments.get("preferredOwnerId"),
            "startTime": arguments.get("startTime"),
            "endTime": START.isoformat(),
            "timezone": "America/Chicago",
            "userTimezone": arguments.get("userTimezone"),
            "version": 1,
        }
        self.consultations[CONSULTATION_ID] = consultation
        if request_id:
            self.by_request_id[request_id] = CONSULTATION_ID
        return _text(call_id, consultation)

    def tools(self) -> list[str]:
        return [call["tool"] for call in self.calls]


def _text(call_id: object, data: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "id": call_id,
            "result": {"content": [{"type": "text", "text": json.dumps(data)}]},
        },
    )


def _error(call_id: object, sales_code: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "id": call_id,
            "error": {"code": -32000, "message": sales_code, "data": {"code": sales_code}},
        },
    )


def make_settings(**overrides: object) -> Settings:
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    values: dict[str, object] = {
        "app_env": "test",
        "sales_mcp_enabled": True,
        "sales_mcp_url": "https://sales.example",
        "sales_mcp_signing_key": pem,
        "sales_mcp_tenant_allowlist_json": json.dumps(
            [{"siteId": SITE_ID, "tenantId": str(TENANT_ID), "ownerId": "owner-1"}]
        ),
        # Production switches the in-process scheduler off so Sales owns the
        # booking; the standalone/dev default (true) is covered separately.
        "chatbot_local_scheduler": False,
    }
    values.update(overrides)
    return Settings(**values)


def build(
    fake: FakeConsultationSales,
    service: StubConsultationService,
    *,
    settings: Settings | None = None,
):
    client = SalesMcpClient(
        settings or make_settings(),
        http_client=httpx.AsyncClient(transport=fake.transport()),
    )
    dispatcher = SalesActionDispatcher(
        sales_client=client,
        consultation_action_service=service,
        app_env="prod",
    )
    return dispatcher


def booking_plan(**slots: object) -> ActionPlan:
    return ActionPlan(
        action_type=ActionType.SCHEDULE_CONSULTATION,
        status=ActionStatus.READY,
        collected_slots={
            "name": "Ada Lovelace",
            "email": "ada@example.com",
            "phone": "+14155552671",
            "requested_time_text": "Tuesday 3pm",
            "customer_timezone": "America/Los_Angeles",
            "business_timezone": "America/Chicago",
            **slots,
        },
        reason="customer agreed a time",
    )


def context(*, lead_id: str | None = LEAD_ID) -> SalesTurnContext:
    return SalesTurnContext(
        tenant_id=TENANT_ID, site_id=SITE_ID, thread_id=THREAD_ID, lead_id=lead_id
    )


async def dispatch(dispatcher: SalesActionDispatcher, plan: ActionPlan, ctx: SalesTurnContext):
    return await dispatcher.dispatch(
        plan, thread_id=THREAD_ID, customer_id=None, sales_context=ctx
    )


# ── The booking lands in Sales (S7.1.1/S7.1.3) ──────────────────────────────
async def test_a_booking_is_scheduled_in_sales_rather_than_the_local_scheduler() -> None:
    fake = FakeConsultationSales()
    service = StubConsultationService()

    result = await dispatch(build(fake, service), booking_plan(), context())

    assert result is not None
    assert result.success is True
    assert result.result_id == CONSULTATION_ID
    assert result.payload["consultation"]["origin"] == "sales"
    assert result.payload["consultation"]["id"] == CONSULTATION_ID
    assert result.payload["consultation"]["status"] == "Scheduled"
    assert result.payload["created"] is True
    assert service.local_calls == 0, "Sales took the booking; local must not book it again"
    # Availability is asked first: schedule will only accept an instant Sales offers.
    assert fake.tools() == ["sales_consultation_availability", "sales_consultation_schedule"]


async def test_the_booking_uses_the_shared_time_seam_and_the_instant_it_returned() -> None:
    fake = FakeConsultationSales()
    service = StubConsultationService()

    result = await dispatch(build(fake, service), booking_plan(), context())

    assert result is not None
    assert result.payload["requested_time_available"] is True
    # The Sales path must not re-derive "Tuesday 3pm": it hands the shared seam a
    # fully-formed request and books whatever instant came back.
    assert len(service.resolved) == 1
    resolved = service.resolved[0]
    assert resolved.requested_time_text == "Tuesday 3pm"
    assert resolved.customer_timezone == "America/Los_Angeles"
    assert resolved.phone == "+14155552671", "Sales needs the E.164 phone the seam saw"
    assert resolved.duration_minutes == 30
    availability = fake.calls[0]["arguments"]
    assert availability["leadId"] == LEAD_ID
    assert availability["startTime"] == START.isoformat()
    assert availability["userTimezone"] == "America/Los_Angeles"


async def test_a_retried_booking_reuses_the_request_id_so_sales_dedupes_it() -> None:
    fake = FakeConsultationSales()
    plan = booking_plan()
    # Fresh dispatchers stand in for the retry landing on another worker.
    first = await dispatch(build(fake, StubConsultationService()), plan, context())
    second = await dispatch(build(fake, StubConsultationService()), plan, context())

    assert first is not None and second is not None
    scheduled = [call for call in fake.calls if call["tool"] == "sales_consultation_schedule"]
    assert len(scheduled) == 2
    assert scheduled[0]["arguments"]["requestId"] == scheduled[1]["arguments"]["requestId"]
    # ...and Sales, seeing the same request twice, still holds exactly one booking.
    assert len(fake.consultations) == 1
    assert second.result_id == first.result_id == CONSULTATION_ID


async def test_a_proposed_result_is_reported_as_an_offer_not_as_booked() -> None:
    fake = FakeConsultationSales(status="Proposed")

    result = await dispatch(build(fake, StubConsultationService()), booking_plan(), context())

    assert result is not None
    assert result.success is True
    # An expiring offer is not a held slot; claiming otherwise is what produced
    # duplicate submissions in chat 7266.
    assert result.payload["created"] is False
    assert result.payload["consultation"]["status"] == "Proposed"
    assert "confirm" in result.customer_safe_summary.lower()


async def test_a_booking_that_lands_a_different_instant_is_surfaced() -> None:
    later = datetime(2026, 11, 3, 22, 0)
    fake = FakeConsultationSales(slots=[{"startTime": later.isoformat(), "ownerId": "csr-7"}])

    result = await dispatch(build(fake, StubConsultationService()), booking_plan(), context())

    assert result is not None
    assert result.success is True
    assert result.payload["requested_time_available"] is False


# ── Failure paths degrade instead of losing the booking (D3/S7.1.4) ─────────
async def test_an_uninterpretable_phone_is_voiced_and_sales_is_never_called() -> None:
    fake = FakeConsultationSales()

    result = await dispatch(
        build(fake, StubConsultationService()), booking_plan(phone="call me maybe"), context()
    )

    assert result is not None
    assert result.success is False
    assert result.error_code == "invalid_phone"
    assert fake.calls == [], "Sales rejects a non-E.164 lead, so the call must not be made"
    assert result.payload["sales"]["code"] == "invalid_phone"


async def test_without_a_sales_lead_the_local_scheduler_still_books() -> None:
    fake = FakeConsultationSales()
    service = StubConsultationService()

    result = await dispatch(build(fake, service), booking_plan(), context(lead_id=None))

    assert result is not None
    assert result.success is True
    assert fake.calls == [], "there is no Sales lead to book against"
    assert service.local_calls == 1


async def test_an_unclaimed_turn_never_calls_sales() -> None:
    fake = FakeConsultationSales()
    service = StubConsultationService()
    unclaimed = SalesTurnContext(thread_id=THREAD_ID, lead_id=LEAD_ID)

    result = await dispatch(build(fake, service), booking_plan(), unclaimed)

    assert result is not None
    assert result.success is True
    assert fake.calls == []
    assert service.local_calls == 1


async def test_a_forbidden_booking_falls_back_to_the_local_scheduler() -> None:
    fake = FakeConsultationSales(deny="sales_consultation_availability")
    service = StubConsultationService()

    result = await dispatch(build(fake, service), booking_plan(), context())

    assert result is not None
    assert result.success is True
    assert service.local_calls == 1, "a refused Sales call must not lose the booking"
    assert result.payload["sales"]["sales_code"] == "SALES-0403"
    assert result.payload["sales"]["degraded"] is True


async def test_no_availability_offered_degrades_rather_than_booking_blind() -> None:
    fake = FakeConsultationSales(slots=[])
    service = StubConsultationService()

    result = await dispatch(build(fake, service), booking_plan(), context())

    assert result is not None
    assert service.local_calls == 1
    assert fake.tools() == ["sales_consultation_availability"]
    assert result.payload["sales"]["code"] == "sales_no_availability"


# ── Accepting an offer Sales persisted (S7.1.2) ─────────────────────────────
async def test_accepting_a_proposed_offer_confirms_that_row_instead_of_rescheduling() -> None:
    fake = FakeConsultationSales(status="Proposed")
    service = StubConsultationService()
    dispatcher = build(fake, service)

    offered = await dispatch(dispatcher, booking_plan(), context())
    assert offered is not None and offered.payload["created"] is False

    # The visitor says "yes" to the offer: the pending confirmation replays the
    # original slots plus the offer handle the state layer recorded.
    accepted = await dispatcher.dispatch(
        booking_plan(
            confirmed=True,
            sales_offer_consultation_id=CONSULTATION_ID,
            sales_offer_version=1,
        ),
        thread_id=THREAD_ID,
        customer_id=None,
        sales_context=context(),
    )

    assert accepted is not None
    assert accepted.success is True
    assert accepted.payload["created"] is True
    assert accepted.result_id == CONSULTATION_ID
    assert fake.tools()[-1] == "sales_consultation_confirm"
    # ...and the accepted offer is not re-scheduled as a fresh booking.
    assert fake.tools().count("sales_consultation_schedule") == 1
    confirm_call = fake.calls[-1]["arguments"]
    assert confirm_call["consultationId"] == CONSULTATION_ID
    assert confirm_call["expectedVersion"] == 1
    assert service.local_calls == 0


async def test_the_offer_confirm_reuses_its_request_id_on_a_retry() -> None:
    fake = FakeConsultationSales(status="Proposed")
    await build(fake, StubConsultationService()).dispatch(
        booking_plan(), thread_id=THREAD_ID, customer_id=None, sales_context=context()
    )
    slots = {
        "confirmed": True,
        "sales_offer_consultation_id": CONSULTATION_ID,
        "sales_offer_version": 1,
    }
    first = await build(fake, StubConsultationService()).dispatch(
        booking_plan(**slots), thread_id=THREAD_ID, customer_id=None, sales_context=context()
    )
    second = await build(fake, StubConsultationService()).dispatch(
        booking_plan(**slots), thread_id=THREAD_ID, customer_id=None, sales_context=context()
    )

    assert first is not None and second is not None
    confirms = [call for call in fake.calls if call["tool"] == "sales_consultation_confirm"]
    assert len(confirms) == 2
    assert confirms[0]["arguments"]["requestId"] == confirms[1]["arguments"]["requestId"]
    # The replay returns the first outcome rather than confirming twice.
    assert second.success is True
    assert second.result_id == first.result_id == CONSULTATION_ID
    assert fake.consultations[CONSULTATION_ID]["version"] == 2


async def test_a_confirm_that_stays_proposed_is_not_reported_as_booked() -> None:
    class StickyOffer(FakeConsultationSales):
        def _confirm(self, call_id: object, arguments: dict[str, Any]) -> httpx.Response:
            row = {**self.consultations[str(arguments["consultationId"])], "status": "Proposed"}
            self.consultations[str(row["id"])] = row
            return _text(call_id, row)

    fake = StickyOffer(status="Proposed")
    await build(fake, StubConsultationService()).dispatch(
        booking_plan(), thread_id=THREAD_ID, customer_id=None, sales_context=context()
    )

    result = await dispatch(
        build(fake, StubConsultationService()),
        booking_plan(
            confirmed=True,
            sales_offer_consultation_id=CONSULTATION_ID,
            sales_offer_version=1,
        ),
        context(),
    )

    assert result is not None
    assert result.payload["created"] is False
    assert "booked" not in result.customer_safe_summary.lower()


async def test_a_refused_confirm_degrades_to_the_local_scheduler() -> None:
    fake = FakeConsultationSales(status="Proposed")
    await build(fake, StubConsultationService()).dispatch(
        booking_plan(), thread_id=THREAD_ID, customer_id=None, sales_context=context()
    )
    fake.deny = "sales_consultation_confirm"
    service = StubConsultationService()

    result = await dispatch(
        build(fake, service),
        booking_plan(
            confirmed=True,
            sales_offer_consultation_id=CONSULTATION_ID,
            sales_offer_version=1,
        ),
        context(),
    )

    assert result is not None
    assert result.success is True
    assert service.local_calls == 1, "a refused confirm must not lose the booking"
    assert result.payload["sales"]["tool"] == "sales_consultation_confirm"
    assert result.payload["sales"]["degraded"] is True


async def test_an_offer_without_a_sales_tenant_is_never_confirmed_externally() -> None:
    fake = FakeConsultationSales(status="Proposed")
    service = StubConsultationService()
    unclaimed = SalesTurnContext(thread_id=THREAD_ID, lead_id=LEAD_ID)

    result = await dispatch(
        build(fake, service),
        booking_plan(
            confirmed=True,
            sales_offer_consultation_id=CONSULTATION_ID,
            sales_offer_version=1,
        ),
        unclaimed,
    )

    assert result is not None
    assert fake.calls == []
    assert service.local_calls == 1


# ── The standalone/dev switch (S7.1.4/T3.4) ─────────────────────────────────
async def test_the_local_scheduler_flag_keeps_the_booking_in_process() -> None:
    fake = FakeConsultationSales()
    service = StubConsultationService()
    dispatcher = build(
        fake, service, settings=make_settings(chatbot_local_scheduler=True)
    )

    result = await dispatch(dispatcher, booking_plan(), context())

    assert result is not None
    assert result.success is True
    assert fake.calls == [], "CHATBOT_LOCAL_SCHEDULER=true means no Sales booking"
    assert service.local_calls == 1
    assert "sales" not in result.payload


# ── Recording the booking in thread state (S7.1.3) ─────────────────────────
def _sales_booking_result(*, status: str, created: bool, version: int = 1) -> ActionResult:
    return ActionResult(
        action_type=ActionType.SCHEDULE_CONSULTATION,
        success=True,
        result_id=CONSULTATION_ID,
        customer_safe_summary="ok",
        payload={
            "consultation": {
                "id": CONSULTATION_ID,
                "status": status,
                "owner_id": "csr-7",
                "lead_id": LEAD_ID,
                "start_time": START.isoformat(),
                "end_time": START.isoformat(),
                "timezone": "America/Chicago",
                "user_timezone": "America/Los_Angeles",
                "version": version,
                "offer_expires_at": None,
                "origin": "sales",
            },
            "created": created,
            "requested_time_available": True,
            "sales": {"tool": "sales_consultation_schedule", "ok": True, "degraded": False},
        },
    )


def test_a_sales_booking_is_recorded_with_its_owner_and_times() -> None:
    state = ThreadState()

    ChatService._apply_sales_action_result_to_state(
        state, _sales_booking_result(status="Scheduled", created=True)
    )

    consultation = state.sales_actions.consultation
    assert consultation.confirmed_appointment_id == CONSULTATION_ID
    assert consultation.csr_id == "csr-7"
    assert consultation.customer_timezone == "America/Los_Angeles"
    # The booked instant is rendered in the tenant's timezone for later grounding.
    assert consultation.confirmed_display_time == "Tuesday, November 3, 2026 at 3:00 PM CST"
    assert consultation.confirmed_customer_display_time == (
        "Tuesday, November 3, 2026 at 1:00 PM PST"
    )
    assert consultation.pending_confirmation is False
    assert state.consultation_handoff_created is True
    assert state.consultation_handoff_action_id == CONSULTATION_ID
    assert state.sales_actions.integration.last_tool == "sales_consultation_schedule"


def test_a_proposed_offer_is_kept_as_a_pending_confirmation_not_a_booking() -> None:
    state = ThreadState()
    state.sales_actions.pending_confirmation.payload = {"name": "Ada", "confirmed": True}

    ChatService._apply_sales_action_result_to_state(
        state, _sales_booking_result(status="Proposed", created=False, version=4)
    )

    consultation = state.sales_actions.consultation
    # Nothing is held yet, so none of the booked-consultation gates may be set.
    assert consultation.confirmed_appointment_id is None
    assert consultation.confirmed_display_time is None
    assert state.consultation_handoff_created is False
    assert consultation.pending_confirmation is True
    assert state.sales_actions.pending_confirmation.type == "schedule_consultation"
    payload = state.sales_actions.pending_confirmation.payload
    assert payload is not None
    # The "yes" next turn must be able to confirm this exact row...
    assert payload["sales_offer_consultation_id"] == CONSULTATION_ID
    assert payload["sales_offer_version"] == 4
    # ...and it still carries the details the booking was made with.
    assert payload["name"] == "Ada"
    assert state.sales_actions.pending_confirmation.created_at is not None


def test_the_offer_handle_survives_an_expiry_window_from_sales() -> None:
    state = ThreadState()
    result = _sales_booking_result(status="Proposed", created=False)
    result.payload["consultation"]["offer_expires_at"] = "2026-10-14T12:30:00+00:00"

    ChatService._apply_sales_action_result_to_state(state, result)

    assert state.sales_actions.pending_confirmation.expires_at == datetime(
        2026, 10, 14, 12, 30, tzinfo=UTC
    )


def test_the_action_event_carries_the_sales_booking_facts() -> None:
    events = ChatService._build_action_events(
        _sales_booking_result(status="Scheduled", created=True)
    )

    assert events == [
        {
            "type": "consultation_scheduled",
            "appointment_id": CONSULTATION_ID,
            "lead_id": LEAD_ID,
            "csr_id": "csr-7",
            "csr_name": None,
            "starts_at_utc": START.isoformat(),
            "ends_at_utc": START.isoformat(),
            "customer_timezone": "America/Los_Angeles",
            "status": "Scheduled",
            "confirmed": True,
        }
    ]


def test_an_offer_action_event_says_it_is_not_confirmed() -> None:
    events = ChatService._build_action_events(
        _sales_booking_result(status="Proposed", created=False)
    )

    assert events[0]["confirmed"] is False
    assert events[0]["appointment_id"] == CONSULTATION_ID


def test_a_locally_booked_consultation_keeps_its_own_payload_shape() -> None:
    state = ThreadState()
    local = ActionResult(
        action_type=ActionType.SCHEDULE_CONSULTATION,
        success=True,
        result_id="99999999-9999-4999-8999-999999999999",
        customer_safe_summary="booked locally",
        payload={
            "csr_id": "csr-local",
            "csr_name": "Local CSR",
            "customer_timezone": "America/Chicago",
            "houston_display_time": "Tuesday, November 3, 2026 at 3:00 PM CST",
            "customer_display_time": "Tuesday, November 3, 2026 at 1:00 PM PST",
        },
    )

    ChatService._apply_sales_action_result_to_state(state, local)

    consultation = state.sales_actions.consultation
    assert consultation.confirmed_appointment_id == "99999999-9999-4999-8999-999999999999"
    assert consultation.csr_name == "Local CSR"
    assert consultation.confirmed_display_time == "Tuesday, November 3, 2026 at 3:00 PM CST"
    assert state.consultation_handoff_created is True


async def test_an_unresolvable_time_hands_the_turn_to_the_local_error_mapping() -> None:
    from bookcraft.components.consultations import AmbiguousDateError

    class AmbiguousResolver(StubConsultationService):
        """The real service raises from both the resolver and the booking itself."""

        def resolve_requested_start(self, request: ConsultationActionRequest) -> datetime:
            raise AmbiguousDateError("which Monday?")

        async def schedule(self, request: ConsultationActionRequest) -> ConsultationActionResult:
            raise AmbiguousDateError("which Monday?")

    fake = FakeConsultationSales()

    result = await dispatch(build(fake, AmbiguousResolver()), booking_plan(), context())

    assert result is not None
    assert result.success is False
    # The local path owns the reply for an ambiguous date; Sales is never asked.
    assert fake.calls == []
    assert result.error_code == "ambiguous_requested_date"
    assert "sales" not in result.payload
