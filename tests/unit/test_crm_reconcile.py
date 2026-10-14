"""The one-off, idempotent bot→Sales CRM reconcile (S12.2.1).

The plan is pure, so most of this pins the mapping (what is replayed, what is
skipped, and the stable request ids that make a re-run a no-op). The two runner
tests then prove dry-run writes nothing and apply rewrites a consultation onto the
Sales lead id the lead call returned.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import httpx

from bookcraft.infra.config import Settings
from bookcraft.infra.sales_mcp import SalesMcpClient


def _load():

    path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "ops"
        / "reconcile_bot_crm_to_sales.py"
    )
    spec = importlib.util.spec_from_file_location("reconcile_bot_crm_to_sales", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Register before exec so `@dataclass` can resolve `cls.__module__`.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


reconcile = _load()

TENANT = UUID("14c350e8-d0bc-4ee9-90c7-dea2b7a7a007")
OTHER = UUID("066dd015-2820-47b5-b2fc-1e9704f0f420")
LEAD_ID = "33333333-3333-4333-8333-333333333333"
CONSULTATION_ID = "44444444-4444-4444-8444-444444444444"
ALLOWLIST = json.dumps(
    [{"siteId": "demo", "tenantId": str(TENANT), "ownerId": "owner-1"}]
)


def _lead(**overrides: object) -> dict[str, object]:
    return {
        "id": LEAD_ID,
        "tenant_id": str(TENANT),
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "phone": None,
        **overrides,
    }


def _consultation(**overrides: object) -> dict[str, object]:
    return {
        "id": CONSULTATION_ID,
        "tenant_id": str(TENANT),
        "lead_id": LEAD_ID,
        "starts_at_utc": datetime.now(UTC) + timedelta(days=3),
        "customer_timezone": "America/Los_Angeles",
        **overrides,
    }


def test_the_plan_is_deterministic_and_idempotent() -> None:
    first = reconcile.plan_reconcile([_lead()], [_consultation()], allowlist_raw=ALLOWLIST)
    second = reconcile.plan_reconcile([_lead()], [_consultation()], allowlist_raw=ALLOWLIST)

    assert [c.tool for c in first.calls] == ["sales_lead_create", "sales_consultation_schedule"]
    assert [c.request_id for c in first.calls] == [c.request_id for c in second.calls]
    # The lead call derives its id from the row id, so a rerun is the same call.
    assert first.calls[0].request_id == reconcile._lead_request_id(LEAD_ID)


def test_rows_without_a_tenant_or_an_allowed_tenant_are_skipped() -> None:
    plan = reconcile.plan_reconcile(
        [
            _lead(id="a", tenant_id=None),
            _lead(id="b", tenant_id=str(OTHER)),
            _lead(id="c", name=None, email=None, phone=None),
        ],
        [],
        allowlist_raw=ALLOWLIST,
    )

    assert plan.calls == []
    assert [row["reason"] for row in plan.skipped] == [
        "no_tenant",
        "tenant_not_allow_listed",
        "no_contact",
    ]


def test_consultations_need_a_replayed_lead_and_a_future_slot() -> None:
    past = _consultation(id="past", starts_at_utc=datetime.now(UTC) - timedelta(days=1))
    orphan = _consultation(id="orphan", lead_id="99999999-9999-4999-8999-999999999999")
    plan = reconcile.plan_reconcile(
        [_lead()], [past, orphan], allowlist_raw=ALLOWLIST
    )

    assert [c.source_id for c in plan.calls] == [LEAD_ID]
    assert {row["id"]: row["reason"] for row in plan.skipped} == {
        "past": "not_replayable",
        "orphan": "lead_not_replayed",
    }


def _make_settings() -> Settings:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return Settings(
        app_env="test",
        sales_mcp_enabled=True,
        sales_mcp_url="https://sales.example",
        sales_mcp_signing_key=pem,
        sales_mcp_max_attempts=1,
        sales_mcp_tenant_allowlist_json=ALLOWLIST,
    )


def _fake_client(calls: list[dict]) -> SalesMcpClient:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        tool = body["params"]["name"]
        if tool == "sales_lead_create":
            data = {"id": "sales-lead-1", "name": "Ada Lovelace"}
        else:
            data = {"id": "sales-consultation-1", "status": "Scheduled"}
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {"content": [{"type": "text", "text": json.dumps(data)}]},
            },
        )

    return SalesMcpClient(
        _make_settings(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


async def test_dry_run_reports_the_calls_without_making_any() -> None:
    plan = reconcile.plan_reconcile([_lead()], [_consultation()], allowlist_raw=ALLOWLIST)
    calls: list[dict] = []

    report = await reconcile.run_plan(plan, _fake_client(calls), apply=False)

    assert calls == []
    assert report["mode"] == "dry-run"
    assert [r["outcome"] for r in report["results"]] == ["would_apply", "would_apply"]


async def test_apply_replays_a_consultation_onto_the_created_sales_lead() -> None:
    plan = reconcile.plan_reconcile([_lead()], [_consultation()], allowlist_raw=ALLOWLIST)
    calls: list[dict] = []

    report = await reconcile.run_plan(plan, _fake_client(calls), apply=True)

    assert [r["outcome"] for r in report["results"]] == ["applied", "applied"]
    consultation_call = calls[-1]["params"]
    assert consultation_call["name"] == "sales_consultation_schedule"
    assert consultation_call["arguments"]["leadId"] == "sales-lead-1"
