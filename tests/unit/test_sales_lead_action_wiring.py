"""Lead actions write to Sales over MCP, and degrade instead of losing the lead."""

import json
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bookcraft.components.actions.dispatcher import SalesActionDispatcher
from bookcraft.components.actions.schemas import ActionPlan, ActionStatus, ActionType
from bookcraft.components.response.chat_schemas import ChatTurnRequest
from bookcraft.domain.state import ThreadState
from bookcraft.infra.config import Settings
from bookcraft.infra.sales_mcp import SalesMcpClient, SalesTurnContext
from bookcraft.services.chat import ChatService

TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_TENANT = UUID("22222222-2222-4222-8222-222222222222")
THREAD_ID = UUID("33333333-3333-4333-8333-333333333333")
SITE_ID = "demo"
LEAD_ID = "44444444-4444-4444-8444-444444444444"


class FakeSales:
    """A minimal Sales MCP endpoint: create/list leads with idempotency."""

    def __init__(self, *, deny: str | None = None) -> None:
        self.leads: dict[str, dict[str, object]] = {}
        self.by_request_id: dict[str, str] = {}
        self.calls: list[dict[str, object]] = []
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
                        "code": -32001,
                        "message": "forbidden",
                        "data": {"code": "SALES-0403"},
                    },
                },
            )
        if tool == "sales_lead_create":
            return self._create(body["id"], arguments)
        if tool == "sales_lead_list":
            return _text(body["id"], {"leads": list(self.leads.values())})
        raise AssertionError(f"unexpected tool {tool}")

    def _create(self, call_id: object, arguments: dict[str, object]) -> httpx.Response:
        request_id = str(arguments.get("requestId") or "")
        if request_id and request_id in self.by_request_id:
            # Sales replays the first outcome for a repeat of the same request.
            lead_id = self.by_request_id[request_id]
            return _text(call_id, self.leads[lead_id])
        lead = {
            "id": LEAD_ID if not self.leads else f"lead-{len(self.leads) + 1}",
            "tenantId": str(TENANT_ID),
            "source": arguments.get("source"),
            "ownerId": arguments.get("ownerId"),
            "stage": "New",
            "score": 42,
            "customer": {
                "name": arguments.get("name"),
                "email": arguments.get("email"),
                "phone": arguments.get("phone"),
            },
        }
        self.leads[str(lead["id"])] = lead
        if request_id:
            self.by_request_id[request_id] = str(lead["id"])
        return _text(call_id, lead)


def _text(call_id: object, data: dict[str, object]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "id": call_id,
            "result": {"content": [{"type": "text", "text": json.dumps(data)}]},
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
    }
    values.update(overrides)
    return Settings(**values)


def build(fake: FakeSales, *, settings: Settings | None = None, lead_service=None):
    settings = settings or make_settings()
    client = SalesMcpClient(
        settings, http_client=httpx.AsyncClient(transport=fake.transport())
    )
    dispatcher = SalesActionDispatcher(
        sales_client=client,
        lead_service=lead_service,
        # prod + no lead_service: the local path cannot fake success, so a passing
        # result proves Sales actually took the write.
        app_env="prod",
    )
    return client, dispatcher


def lead_plan(**slots: object) -> ActionPlan:
    collected = {
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "phone": "+15550000",
        **slots,
    }
    return ActionPlan(
        action_type=ActionType.CREATE_LEAD,
        status=ActionStatus.READY,
        collected_slots=collected,
        reason="contact details captured",
    )


def context(tenant_id: UUID = TENANT_ID, site_id: str | None = SITE_ID) -> SalesTurnContext:
    return SalesTurnContext(tenant_id=tenant_id, site_id=site_id, thread_id=THREAD_ID)


class StubLeadService:
    """Stands in for the bot's own CRM, to prove the local fallback still saves."""

    def __init__(self) -> None:
        self.requests: list[object] = []

    async def create_or_update(self, request: object) -> object:
        self.requests.append(request)
        lead = SimpleNamespace(
            id=UUID("55555555-5555-4555-8555-555555555555"),
            model_dump=lambda mode="json": {
                "id": "55555555-5555-4555-8555-555555555555",
                "name": "Ada Lovelace",
                "email": "ada@example.com",
                "phone": "+15550000",
            },
        )
        return SimpleNamespace(lead=lead, created=True, updated_fields=[])


# ── The CRM write goes to Sales (S3.2.2) ───────────────────────────────────
async def test_a_lead_action_creates_the_lead_in_sales_and_lists_it_back() -> None:
    fake = FakeSales()
    client, dispatcher = build(fake)

    result = await dispatcher.dispatch(
        lead_plan(), thread_id=THREAD_ID, customer_id=None, sales_context=context()
    )
    assert result is not None
    assert result.success is True
    assert result.result_id == LEAD_ID
    assert result.payload["lead"]["origin"] == "sales"
    assert result.payload["lead"]["name"] == "Ada Lovelace"
    assert result.payload["sales"] == {
        "tool": "sales_lead_create",
        "request_id": result.payload["sales"]["request_id"],
        "ok": True,
        "code": None,
        "sales_code": None,
        "degraded": False,
        "attempts": 1,
    }

    create_call = fake.calls[0]
    assert create_call["tool"] == "sales_lead_create"
    assert create_call["arguments"]["source"] == f"Chatbot:{SITE_ID}"
    assert create_call["arguments"]["ownerId"] == "owner-1"
    assert create_call["arguments"]["requestId"]
    assert create_call["id"] == create_call["arguments"]["requestId"]

    listed = await client.call_tool(
        "sales_lead_list", {"search": "ada"}, tenant_id=TENANT_ID, site_id=SITE_ID
    )
    assert listed.ok is True
    assert [lead["id"] for lead in listed.data["leads"]] == [LEAD_ID]
    assert listed.data["leads"][0]["score"] == 42
    assert listed.data["leads"][0]["ownerId"] == "owner-1"


async def test_a_retried_action_reuses_the_request_id_and_creates_one_lead() -> None:
    fake = FakeSales()
    first_client, first = build(fake)
    # A fresh dispatcher models the retry arriving after a restart: the stable
    # requestId — not the in-process idempotency cache — is what dedupes it.
    _, second = build(fake)

    first_result = await first.dispatch(
        lead_plan(), thread_id=THREAD_ID, customer_id=None, sales_context=context()
    )
    second_result = await second.dispatch(
        lead_plan(), thread_id=THREAD_ID, customer_id=None, sales_context=context()
    )

    assert first_result is not None and second_result is not None
    assert first_result.result_id == second_result.result_id == LEAD_ID
    assert len(fake.leads) == 1
    request_ids = [call["arguments"]["requestId"] for call in fake.calls]
    assert request_ids[0] == request_ids[1]
    assert first_client.enabled is True


async def test_a_different_payload_under_the_same_request_id_is_a_conflict() -> None:
    fake = FakeSales()
    _, dispatcher = build(fake)
    await dispatcher.dispatch(
        lead_plan(), thread_id=THREAD_ID, customer_id=None, sales_context=context()
    )
    # Same thread, same action, different contact details → different key.
    other = lead_plan(email="someone.else@example.com")
    await dispatcher.dispatch(
        other, thread_id=THREAD_ID, customer_id=None, sales_context=context()
    )
    assert len(fake.leads) == 2


# ── Degrade instead of losing the lead (S3.2.3, §6.4 rule 6) ───────────────
async def test_a_forbidden_tool_falls_back_to_the_local_path_and_is_marked() -> None:
    fake = FakeSales(deny="sales_lead_create")
    lead_service = StubLeadService()
    _, dispatcher = build(fake, lead_service=lead_service)

    result = await dispatcher.dispatch(
        lead_plan(), thread_id=THREAD_ID, customer_id=None, sales_context=context()
    )

    assert result is not None
    # The enquiry is not lost: the local path saved it.
    assert result.success is True
    assert len(lead_service.requests) == 1
    # The recorded lead is the local one, not anything from the refused call.
    assert result.payload["lead"]["id"] == "55555555-5555-4555-8555-555555555555"
    assert result.payload["sales"]["code"] == "sales_forbidden"
    assert result.payload["sales"]["degraded"] is True
    assert result.payload["sales"]["sales_code"] == "SALES-0403"


async def test_a_refused_tenant_never_reaches_sales_and_degrades() -> None:
    fake = FakeSales()
    lead_service = StubLeadService()
    _, dispatcher = build(fake, lead_service=lead_service)

    result = await dispatcher.dispatch(
        lead_plan(), thread_id=THREAD_ID, customer_id=None, sales_context=context(OTHER_TENANT)
    )

    assert fake.calls == []
    assert result is not None and result.success is True
    assert result.payload["sales"]["code"] == "sales_tenant_not_allowed"
    assert result.payload["sales"]["degraded"] is True


async def test_without_a_sales_context_the_local_path_is_used_unchanged() -> None:
    fake = FakeSales()
    lead_service = StubLeadService()
    _, dispatcher = build(fake, lead_service=lead_service)

    result = await dispatcher.dispatch(
        lead_plan(), thread_id=THREAD_ID, customer_id=None, sales_context=None
    )

    assert fake.calls == []
    assert result is not None and result.success is True
    assert "sales" not in result.payload


async def test_a_disabled_integration_keeps_todays_behaviour() -> None:
    fake = FakeSales()
    lead_service = StubLeadService()
    _, dispatcher = build(
        fake, settings=make_settings(sales_mcp_enabled=False), lead_service=lead_service
    )

    result = await dispatcher.dispatch(
        lead_plan(), thread_id=THREAD_ID, customer_id=None, sales_context=context()
    )

    assert fake.calls == []
    assert result is not None and result.success is True
    assert "sales" not in result.payload


# ── Thread-state recording (S3.2.2) ────────────────────────────────────────
def test_the_sales_lead_id_is_recorded_in_thread_state() -> None:
    state = ThreadState()
    result = SimpleNamespace(
        action_type=ActionType.CREATE_LEAD,
        success=True,
        result_id=LEAD_ID,
        payload={
            "lead": {"id": LEAD_ID, "name": "Ada", "email": "ada@example.com", "origin": "sales"},
            "sales": {
                "tool": "sales_lead_create",
                "request_id": "abc",
                "ok": True,
                "code": None,
                "degraded": False,
            },
        },
    )
    ChatService._apply_sales_action_result_to_state(state, result)

    assert state.sales_actions.integration.lead_id == LEAD_ID
    assert state.sales_actions.integration.last_tool == "sales_lead_create"
    assert state.sales_actions.integration.last_request_id == "abc"
    assert state.sales_actions.integration.degraded is False
    assert state.sales_actions.lead.lead_id == LEAD_ID
    assert state.lead_created is True


def test_a_degraded_sales_call_is_visible_in_thread_state() -> None:
    state = ThreadState()
    result = SimpleNamespace(
        action_type=ActionType.CREATE_LEAD,
        success=True,
        result_id="55555555-5555-4555-8555-555555555555",
        payload={
            "lead": {"id": "55555555-5555-4555-8555-555555555555", "origin": "local"},
            "sales": {
                "tool": "sales_lead_create",
                "request_id": "abc",
                "ok": False,
                "code": "sales_forbidden",
                "degraded": True,
            },
        },
    )
    ChatService._apply_sales_action_result_to_state(state, result)

    assert state.sales_actions.integration.degraded is True
    assert state.sales_actions.integration.last_code == "sales_forbidden"
    assert state.sales_actions.integration.lead_id is None


def test_a_turn_that_saved_nothing_anywhere_is_still_recorded() -> None:
    # Sales refused the call and the local path had no service either: without this
    # the turn would look like a plain failure with no trace of the degraded CRM.
    state = ThreadState()
    result = SimpleNamespace(
        action_type=ActionType.CREATE_LEAD,
        success=False,
        result_id=None,
        payload={
            "sales": {
                "tool": "sales_lead_create",
                "request_id": "abc",
                "ok": False,
                "code": "sales_unavailable",
                "degraded": True,
            }
        },
    )
    ChatService._apply_sales_action_result_to_state(state, result)

    assert state.sales_actions.integration.degraded is True
    assert state.sales_actions.integration.last_code == "sales_unavailable"
    assert state.sales_actions.integration.lead_id is None
    assert state.lead_created is False


# ── Verified claims, never request input (S3.3.1) ──────────────────────────
def test_a_body_supplied_tenant_is_overwritten_by_the_verified_claim() -> None:
    from bookcraft.api.auth import AuthPrincipal
    from bookcraft.api.chat import _with_verified_claims

    payload = ChatTurnRequest(
        thread_id=THREAD_ID,
        message="hi",
        tenant_id=OTHER_TENANT,
        site_id="attacker-site",
    )
    claimed = _with_verified_claims(
        payload,
        AuthPrincipal(
            subject="svc:sales",
            customer_id=UUID("66666666-6666-4666-8666-666666666666"),
            tenant_id=TENANT_ID,
            site_id=SITE_ID,
        ),
    )

    assert claimed.tenant_id == TENANT_ID
    assert claimed.site_id == SITE_ID
    assert claimed.customer_id == UUID("66666666-6666-4666-8666-666666666666")


def test_a_token_without_claims_cannot_inherit_a_tenant() -> None:
    from bookcraft.api.auth import AuthPrincipal
    from bookcraft.api.chat import _with_verified_claims

    payload = ChatTurnRequest(thread_id=THREAD_ID, message="hi", tenant_id=OTHER_TENANT)
    cleared = _with_verified_claims(payload, AuthPrincipal(subject="anonymous"))

    assert cleared.tenant_id is None
    assert cleared.site_id is None


def test_settings_default_the_integration_off() -> None:
    assert Settings(app_env="test").sales_mcp_enabled is False


@pytest.mark.parametrize("slot", ["email", "phone"])
async def test_a_lead_with_only_one_contact_channel_still_reaches_sales(slot: str) -> None:
    fake = FakeSales()
    client, dispatcher = build(fake)
    slots = {"name": "Ada", slot: "ada@example.com" if slot == "email" else "+15550000"}
    plan = ActionPlan(
        action_type=ActionType.CREATE_LEAD,
        status=ActionStatus.READY,
        collected_slots=slots,
        reason="contact details captured",
    )

    result = await dispatcher.dispatch(
        plan, thread_id=THREAD_ID, customer_id=None, sales_context=context()
    )

    assert result is not None and result.success is True
    assert client.bindings[SITE_ID].owner_id == "owner-1"
