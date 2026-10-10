"""Exercise the real HTTP adapter and API against the Sales MCP wire contract."""

import asyncio
import base64
import copy
import json
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

from bookcraft.api.main import create_app
from bookcraft.components.actions.schemas import ActionPlan, ActionStatus, ActionType
from bookcraft.components.storage.management_models import WorkflowRecord
from bookcraft.components.storage.models import DeliveryOutboxRecord, SalesConsultationRecord
from bookcraft.components.storage.thread_repository import ThreadRepository
from bookcraft.infra.config import Settings
from bookcraft.infra.sales_mcp import TOOL_SCOPES, SalesError, SalesMcpClient
from bookcraft.services.sales import SalesService
from tests.unit.test_api_auth_unit import SIGNING_KEY, make_token

TENANT, CUSTOMER, LEAD = [str(uuid4()) for _ in range(3)]


def service_token(**overrides):
    claims = {
        "iss": "erp-rbac",
        "aud": "internal",
        "tenantId": TENANT,
        "userId": "chatbot",
        "scope": " ".join(sorted(set(TOOL_SCOPES.values()))),
        "exp": time.time() + 300,
        **overrides,
    }

    def segment(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return segment({"alg": "EdDSA", "kid": "test"}) + "." + segment(claims) + ".signature"


class SalesFixture:
    def __init__(self):
        self.profile = {
            "id": CUSTOMER,
            "tenantId": TENANT,
            "name": "Test Customer",
            "phone": "+12025550123",
            "email": "test@example.invalid",
            "status": "Lead",
            "lead": {"id": LEAD, "customerId": CUSTOMER, "tenantId": TENANT},
        }
        self.rows, self.receipts, self.calls = {}, {}, []
        self.propose = False
        self.lose_response = False
        self.down = False

    def __call__(self, request):
        assert "authorization" not in request.headers
        assert request.headers["x-service-authorization"].startswith("Bearer ")
        assert request.headers["x-mcp-internal"] == "internal-test-secret"
        rpc = json.loads(request.content)
        assert rpc["method"] == "tools/call"
        tool, args = rpc["params"]["name"], rpc["params"]["arguments"]
        self.calls.append((tool, copy.deepcopy(args)))
        if self.down:
            return httpx.Response(503)
        value = None
        if tool == "sales_customer_get":
            value = copy.deepcopy(self.profile)
        elif tool == "sales_customer_update":
            self.profile.update(args)
            value = copy.deepcopy(self.profile)
        elif tool == "sales_lead_create":
            assert args["customerId"] == CUSTOMER
            self.profile["lead"] = {"id": LEAD, "customerId": CUSTOMER, "tenantId": TENANT}
            value = self.profile["lead"]
        elif tool == "sales_consultation_config_get":
            value = {
                "version": 4,
                "policy": {
                    "enabled": True,
                    "timezone": "Asia/Karachi",
                    "durationMinutes": 45,
                    "maxDaysAhead": 14,
                    "ownerIds": ["real-csr"],
                },
            }
        elif tool == "sales_consultation_availability":
            value = {
                "slots": [{"startTime": args["startTime"], "ownerId": "real-csr"}],
                "reserved": False,
                "timezone": "Asia/Karachi",
            }
        elif tool == "sales_consultation_list":
            value = [
                r
                for r in self.rows.values()
                if r["leadId"] == args["leadId"]
                and ("status" not in args or r["status"] == args["status"])
            ]
        elif tool == "sales_consultation_get":
            value = self.rows.get(args["consultationId"])
        else:
            key = args["requestId"]
            fingerprint = (tool, args)
            if key in self.receipts:
                previous, value = self.receipts[key]
                assert previous == fingerprint
            elif tool == "sales_consultation_schedule":
                start = datetime.fromisoformat(args["startTime"])
                if self.propose:
                    start += timedelta(minutes=15)
                value = {
                    "id": str(uuid4()),
                    "tenantId": TENANT,
                    "customerId": CUSTOMER,
                    "leadId": LEAD,
                    "ownerId": "real-csr",
                    "startTime": start.isoformat(),
                    "endTime": (start + timedelta(minutes=45)).isoformat(),
                    "timezone": "Asia/Karachi",
                    "userTimezone": args["userTimezone"],
                    "status": "Proposed" if self.propose else "Scheduled",
                    "version": 1,
                    "offerExpiresAt": (datetime.now(UTC) + timedelta(minutes=15)).isoformat()
                    if self.propose
                    else None,
                }
                self.rows[value["id"]] = copy.deepcopy(value)
                self.receipts[key] = (copy.deepcopy(fingerprint), copy.deepcopy(value))
            else:
                row = self.rows[args["consultationId"]]
                if row["version"] != args["expectedVersion"]:
                    return httpx.Response(
                        200,
                        json={
                            "jsonrpc": "2.0",
                            "id": rpc["id"],
                            "error": {
                                "code": -32000,
                                "message": "private CRM content",
                                "data": {"code": "SALES-0409", "status": 409},
                            },
                        },
                    )
                row["version"] += 1
                row["status"] = "Canceled" if tool.endswith("_cancel") else "Scheduled"
                if tool.endswith("_reschedule"):
                    row["startTime"] = args["startTime"]
                    row["endTime"] = (
                        datetime.fromisoformat(args["startTime"]) + timedelta(minutes=45)
                    ).isoformat()
                value = copy.deepcopy(row)
                self.receipts[key] = (copy.deepcopy(fingerprint), copy.deepcopy(value))
            if self.lose_response:
                self.lose_response = False
                raise httpx.ReadTimeout("response lost after commit", request=request)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": rpc["id"],
                "result": {
                    "content": [{"type": "text", "text": json.dumps(value)}],
                },
            },
        )


@pytest.fixture
async def integration(tmp_path):
    token_file = tmp_path / "service.jwt"
    token_file.write_text(service_token(), encoding="utf-8")
    settings = Settings(
        app_env="test",
        api_auth_mode="jwt",
        jwt_signing_key=SIGNING_KEY,
        sales_integration_mode="erp_mcp",
        sales_mcp_url="https://sales.example.invalid/mcp",
        sales_mcp_internal_secret="internal-test-secret",  # noqa: S106 - synthetic fixture
        sales_tenant_id=TENANT,
        sales_actor_id="chatbot",
        sales_service_jwt_file=str(token_file),
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'sales.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    fake = SalesFixture()
    mcp = SalesMcpClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(fake)))
    app = create_app(Settings(app_env="test", api_auth_mode="jwt", jwt_signing_key=SIGNING_KEY))
    app.state.settings, app.state.session_factory = settings, sessions
    app.state.sales_service = SalesService(mcp, sessions)
    app.state.chat_service.thread_repository = ThreadRepository(sessions)
    app.state.chat_service.action_dispatcher.sales_service = app.state.sales_service
    app.state.chat_service.response_generator.sales_mode = True
    thread = uuid4()
    await app.state.chat_service.thread_repository.authorize(
        thread_id=thread,
        subject="sales-website-bridge",
        customer_id=UUID(CUSTOMER),
    )
    token = make_token(
        {
            "sub": "sales-website-bridge",
            "customer_id": CUSTOMER,
            "scope": "chat:turn",
            "exp": time.time() + 300,
        }
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        yield client, thread, app, fake, sessions
    await mcp.aclose()
    await engine.dispose()


def booking(**overrides):
    return {
        "appointment_id": str(uuid4()),
        "starts_at": "2030-10-12T22:15:00+05:00",
        "customer_timezone": "Asia/Karachi",
        "csr_id": "real-csr",
        "name": "Test Customer",
        "phone": "+12025550123",
        **overrides,
    }


async def test_sales_policy_schedule_reschedule_cancel_and_no_standalone_writes(integration):
    client, tid, app, fake, sessions = integration
    base = f"/api/v1/customer/threads/{tid}/appointments"
    policy = (await client.get(base + "/policy")).json()
    assert policy["policy"]["timezone"] == "Asia/Karachi"
    assert "ownerIds" not in policy["policy"]
    result = await client.post(base, json=booking())
    assert result.status_code == 200, result.text
    row = result.json()
    assert row["status"] == "scheduled"
    assert row["csr_id"] == "real-csr"
    assert row["source"] == "erp_sales"
    assert row["business_display_time"].endswith("+05:00")
    assert row["houston_display_time"].endswith("-05:00")
    response = await client.post(
        base,
        json=booking(
            appointment_id=row["id"],
            expected_version=1,
            request_id=str(uuid4()),
            starts_at="2030-10-13T22:15:00+05:00",
        ),
    )
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 2
    stale = await client.delete(base + f"/{row['id']}?expected_version=1&request_id={uuid4()}")
    assert stale.status_code == 409
    assert "private CRM content" not in stale.text
    canceled = await client.delete(base + f"/{row['id']}?expected_version=2&request_id={uuid4()}")
    assert canceled.json()["status"] == "canceled"
    state = await app.state.chat_service.thread_repository.load_or_create(thread_id=tid)
    assert state.state.sales_actions.consultation.confirmed_appointment_id is None
    async with sessions() as session:
        assert not (await session.execute(select(SalesConsultationRecord))).scalars().all()
        assert not (await session.execute(select(DeliveryOutboxRecord))).scalars().all()


async def test_proposed_requires_explicit_confirmation(integration):
    client, tid, app, fake, _ = integration
    fake.propose = True
    base = f"/api/v1/customer/threads/{tid}/appointments"
    offered = (await client.post(base, json=booking())).json()
    assert offered["status"] == "proposed"
    state = await app.state.chat_service.thread_repository.load_or_create(thread_id=tid)
    assert state.state.sales_actions.consultation.confirmed_appointment_id is None
    assert not any(tool.endswith("_confirm") for tool, _ in fake.calls)
    confirmed = await client.post(
        base + f"/{offered['id']}/confirm", json={"expected_version": 1, "request_id": str(uuid4())}
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "scheduled"


async def test_lost_response_is_recovered_with_identical_request_after_restart(integration):
    client, tid, app, fake, sessions = integration
    fake.lose_response = True
    base = f"/api/v1/customer/threads/{tid}/appointments"
    payload = booking()
    failed = await client.post(base, json=payload)
    assert failed.status_code == 503, failed.text
    pending = (await client.get(base + "/operations")).json()
    assert pending[0]["request_id"] == payload["appointment_id"]
    assert (await client.post(base, json=booking())).status_code == 409
    assert (await client.post(base, json={**payload, "csr_id": "different"})).status_code == 409
    app.state.sales_service = SalesService(app.state.sales_service.client, sessions)
    replay = await client.post(base + f"/operations/{payload['appointment_id']}/retry")
    assert replay.status_code == 200, replay.text
    assert len(fake.rows) == 1
    writes = [args for tool, args in fake.calls if tool.endswith("_schedule")]
    assert len(writes) == 2 and writes[0] == writes[1]
    # Historical receipts must not resurrect a later external cancellation.
    fake.rows[replay.json()["id"]].update(status="Canceled", version=2)
    again = await client.post(base, json=payload)
    assert again.json()["status"] == "canceled"
    async with sessions() as session:
        row = await session.get(WorkflowRecord, UUID(payload["appointment_id"]))
        assert row.status == "acknowledged"


async def test_customer_and_tenant_isolation_even_with_shared_bridge_subject(integration):
    client, tid, _, fake, _ = integration
    wrong = make_token(
        {
            "sub": "sales-website-bridge",
            "customer_id": str(uuid4()),
            "scope": "chat:turn",
            "exp": time.time() + 300,
        }
    )
    base = f"/api/v1/customer/threads/{tid}/appointments"
    assert (await client.get(base, headers={"Authorization": "Bearer " + wrong})).status_code == 403
    assert (
        await client.post(
            "/api/v1/chat/facts",
            json={"thread_id": str(tid)},
            headers={"Authorization": "Bearer " + wrong},
        )
    ).status_code == 403
    assert not fake.calls
    fake.profile["tenantId"] = str(uuid4())
    assert (await client.post(base, json=booking())).status_code == 502
    assert not any(tool.endswith("_schedule") for tool, _ in fake.calls)


async def test_bridge_facts_use_sales_profile_before_first_turn(integration):
    client, tid, app, fake, _ = integration
    response = await client.post(
        "/api/v1/chat/facts",
        json={
            "thread_id": str(tid),
            "name": "Forged customer",
            "source_label": "crm_sync",
        },
    )
    assert response.status_code == 200, response.text
    state = await app.state.chat_service.thread_repository.load_or_create(thread_id=tid)
    assert state.state.personal.name.value == fake.profile["name"]


async def test_contact_reuses_sales_customer_and_does_not_change_consent(integration):
    client, tid, _, fake, _ = integration
    fake.profile["lead"] = None
    base = f"/api/v1/customer/threads/{tid}/appointments"
    assert (
        await client.post(
            base + "/contact",
            json={
                "name": "Updated Name",
                "phone": "03001234567",
            },
        )
    ).status_code == 422
    response = await client.post(
        base + "/contact",
        json={
            "name": "Updated Name",
            "phone": "+923001234567",
        },
    )
    assert response.status_code == 200, response.text
    assert fake.profile["id"] == CUSTOMER
    update = next(args for tool, args in fake.calls if tool == "sales_customer_update")
    assert set(update) == {"customerId", "name", "phone"}
    creates = [args for tool, args in fake.calls if tool == "sales_lead_create"]
    assert creates == [{"customerId": CUSTOMER, "source": "chatbot"}]
    await client.post(base + "/contact", json={"name": "Updated Name", "phone": "+923001234567"})
    assert len([tool for tool, _ in fake.calls if tool == "sales_lead_create"]) == 1


async def test_outage_and_chat_action_never_fall_back_to_local_booking(integration):
    client, tid, app, fake, _ = integration
    fake.down = True
    base = f"/api/v1/customer/threads/{tid}/appointments"
    assert (await client.post(base, json=booking())).status_code == 503
    assert not fake.rows
    result = await app.state.chat_service.action_dispatcher.dispatch(
        ActionPlan(
            action_type=ActionType.SCHEDULE_CONSULTATION,
            status=ActionStatus.READY,
            reason="test",
            collected_slots={},
        ),
        thread_id=tid,
        customer_id=UUID(CUSTOMER),
    )
    assert not result.success
    assert result.error_code == "sales_booking_confirmation_required"


@pytest.mark.parametrize(
    "claims",
    [
        {"tenantId": str(uuid4())},
        {"userId": "different"},
        {"exp": 1},
        {"scope": "sales.config.manage"},
        {"iss": "untrusted"},
        {"aud": "website"},
    ],
)
async def test_rotated_token_must_preserve_tenant_actor_and_permission(integration, claims):
    client, tid, app, fake, _ = integration
    from pathlib import Path

    await asyncio.to_thread(
        Path(app.state.settings.sales_service_jwt_file).write_text,
        service_token(**claims),
        encoding="utf-8",
    )
    response = await client.get(f"/api/v1/customer/threads/{tid}/appointments")
    assert response.status_code == 503
    assert fake.calls == []


@pytest.mark.parametrize(
    "payload",
    [
        {"jsonrpc": "2.0", "id": "wrong", "result": {}},
        {"jsonrpc": "2.0", "result": {"content": []}},
        {"result": {"isError": True, "content": [{"type": "text", "text": "private"}]}},
    ],
)
async def test_invalid_rpc_envelopes_fail_closed(integration, payload):
    _, _, app, _, _ = integration
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    async with httpx.AsyncClient(transport=transport) as client:
        mcp = SalesMcpClient(app.state.settings, client)
        with pytest.raises(SalesError, match="sales_invalid_response"):
            await mcp.call("sales_customer_get", {"customerId": CUSTOMER})


async def test_callback_cannot_confirm_a_proposal_or_another_customer(integration):
    client, tid, _, fake, _ = integration
    fake.propose = True
    base = f"/api/v1/customer/threads/{tid}/appointments"
    row = (await client.post(base, json=booking())).json()
    admin = make_token({"sub": "staff", "scope": "chat:manage", "exp": time.time() + 300})
    headers = {"Authorization": "Bearer " + admin}
    payload = {"thread_id": str(tid), "appointment_id": row["id"]}
    response = await client.post(
        "/api/v1/chat/consultation-confirmed", json=payload, headers=headers
    )
    assert response.status_code == 409
    fake.rows[row["id"]].update(status="Scheduled", customerId=str(uuid4()))
    response = await client.post(
        "/api/v1/chat/consultation-confirmed", json=payload, headers=headers
    )
    assert response.status_code == 502


async def test_conversation_erasure_does_not_call_invented_sales_rest_route(integration):
    from bookcraft.services.privacy import erase_thread

    client, tid, app, fake, sessions = integration
    base = f"/api/v1/customer/threads/{tid}"
    await client.post(base + "/appointments", json=booking())
    response = await client.delete(base + "/data")
    assert response.json()["scope"] == "chatbot_conversation"
    assert response.json()["sales_records"] == "retained_in_sales"
    before = len(fake.calls)
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as remote:
        await erase_thread(
            sessions, app.state.settings, app.state.chat_service.trace_store, None, remote, tid
        )
    assert len(fake.calls) == before
    assert len(fake.rows) == 1
    status = await client.get(base + "/deletion")
    assert status.status_code == 200
    assert status.json()["status"] == "completed"
    assert status.json()["sales_records"] == "retained_in_sales"


async def test_readiness_reports_sales_failure_without_credentials(integration):
    from bookcraft.infra.readiness import ReadinessChecker

    _, _, app, fake, _ = integration
    checker = ReadinessChecker(app.state.settings, app.state.sales_service.client)
    assert (await checker._check_sales())[1].status == "ok"
    fake.down = True
    result = (await checker._check_sales())[1]
    assert result.status == "error"
    assert result.detail == "sales_unavailable"


def test_sales_prompts_do_not_advertise_standalone_hours():
    from bookcraft.components.response.generator import (
        _response_system_prompt,
        sales_safe_response,
        sales_system_prompt,
    )

    prompt = sales_system_prompt(_response_system_prompt())
    assert "10 AM to 7 PM" not in prompt
    assert "accept their email as the contact" not in prompt
    assert "Proposed offer" in prompt
    assert "10 AM" not in sales_safe_response(
        "We're available Monday–Friday, 10 AM to 7 PM Central."
    )


def test_sales_context_does_not_offer_standalone_slots(monkeypatch):
    from bookcraft.components.context import ContextPackBuilder
    from bookcraft.components.sales.consultation_state import ConsultationStage
    from bookcraft.domain.state import ThreadState
    from tests.unit.test_context_pack_builder import _intent

    monkeypatch.setattr(
        "bookcraft.components.context.pack_builder.suggest_consultation_slot_labels",
        lambda **kwargs: ["Unverified standalone slot"],
    )
    state = ThreadState(consultation_stage=ConsultationStage.REQUESTED_TIME_SLOTS_OFFERED)
    assert ContextPackBuilder().build(state=state, intent=_intent()).suggested_call_slots
    assert (
        not ContextPackBuilder(sales_mode=True)
        .build(state=state, intent=_intent())
        .suggested_call_slots
    )


async def test_sales_lead_updates_state_without_becoming_a_local_foreign_key(integration):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from bookcraft.components.pricing_actions.schemas import PricingActionResult
    from bookcraft.domain.state import ThreadState

    _, tid, app, _, _ = integration
    dispatcher = app.state.chat_service.action_dispatcher
    lead = await dispatcher.dispatch(
        ActionPlan(
            action_type=ActionType.CREATE_LEAD,
            status=ActionStatus.READY,
            collected_slots={},
            reason="test enquiry",
        ),
        thread_id=tid,
        customer_id=UUID(CUSTOMER),
    )
    state = ThreadState()
    app.state.chat_service._apply_sales_action_result_to_state(state, lead)
    assert state.lead_id == LEAD and state.lead_created
    quote = AsyncMock(
        return_value=PricingActionResult(
            quote_id=uuid4(),
            status="needs_input",
            services=["editing"],
            customer_safe_summary="Draft estimate",
            quote_output={},
        )
    )
    dispatcher.pricing_action_service = SimpleNamespace(quote=quote)
    result = await dispatcher.dispatch(
        ActionPlan(
            action_type=ActionType.PRICE_QUOTE,
            status=ActionStatus.READY,
            collected_slots={"lead_id": LEAD, "services": ["editing"]},
            reason="test estimate",
        ),
        thread_id=tid,
        customer_id=UUID(CUSTOMER),
    )
    assert result.success
    request = quote.call_args.args[0]
    assert request.lead_id is None
    assert request.collected_slots["lead_id"] == LEAD
