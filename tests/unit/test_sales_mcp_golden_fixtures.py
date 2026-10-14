"""The bot reads the shared `tools/call` goldens exactly as the contract says.

The fixtures live in erp-contracts (`fixtures/sales-mcp/tools-call.golden.json`)
and are validated against the frozen TS schemas there. This suite feeds the same
responses through the real client and asserts the recorded `expect`, so the two
repos cannot drift apart on how a Sales reply is interpreted (S3.5.1).
"""

import json
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bookcraft.infra.config import Settings
from bookcraft.infra.sales_mcp import (
    ACTION_TOOL_MAP,
    TOOL_PERMISSIONS,
    SalesMcpClient,
)

TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
FIXTURES = (
    Path(__file__).resolve().parents[3]
    / "erp-contracts"
    / "fixtures"
    / "sales-mcp"
    / "tools-call.golden.json"
)


def load_cases() -> list[dict[str, object]]:
    if not FIXTURES.is_file():
        pytest.skip(f"cross-repo fixtures not present at {FIXTURES}")
    return json.loads(FIXTURES.read_text(encoding="utf-8"))["cases"]


CASES = load_cases()
CASE_IDS = [str(case["name"]) for case in CASES]


def make_settings() -> Settings:
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
        sales_mcp_tenant_allowlist_json=json.dumps(
            [{"siteId": "demo", "tenantId": str(TENANT_ID), "ownerId": "owner-1"}]
        ),
    )


def response_from(case: dict[str, object]) -> httpx.Response:
    spec = case["response"]
    assert isinstance(spec, dict)
    status = int(spec.get("status", 200))
    if "json" in spec:
        return httpx.Response(status, json=spec["json"])
    return httpx.Response(status, text=str(spec.get("text", "")))


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
async def test_the_bot_reads_each_golden_exchange_as_the_contract_expects(
    case: dict[str, object],
) -> None:
    expect = case["expect"]
    assert isinstance(expect, dict)
    client = SalesMcpClient(
        make_settings(),
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: response_from(case))
        ),
    )

    result = await client.call_tool(
        str(case["tool"]),
        dict(case["request"]["params"]["arguments"]),  # type: ignore[index,union-attr]
        tenant_id=TENANT_ID,
        site_id="demo",
        request_id=str(case["request"]["id"]),  # type: ignore[index]
    )

    assert result.ok is bool(expect["ok"]), case["name"]
    assert result.code == expect["code"], case["name"]
    assert result.retryable is bool(expect["retryable"]), case["name"]
    assert result.degraded is bool(expect["degraded"]), case["name"]
    if expect["ok"]:
        assert result.data is not None
        # Subset match, mirroring the contract suite's toMatchObject: Sales may
        # return more of the record than a case asserts, never less.
        for key, expected in dict(expect["dataContains"]).items():
            actual = result.data[key]
            if isinstance(expected, list):
                assert dict(expected[0]).items() <= dict(actual[0]).items(), f"{case['name']}:{key}"
            else:
                assert actual == expected, f"{case['name']}:{key}"


def test_every_golden_tool_is_one_the_bot_may_call() -> None:
    for case in CASES:
        tool = str(case["tool"])
        assert tool in TOOL_PERMISSIONS, tool


def test_the_fixtures_cover_every_tool_the_bot_calls_today() -> None:
    # The tools the dispatcher can actually reach (S3.2.2): a new one must land
    # with a golden exchange rather than shipping unverified.
    covered = {str(case["tool"]) for case in CASES}
    assert {"sales_lead_create", "sales_lead_list"} <= covered


def test_every_mapped_action_points_at_a_granted_tool() -> None:
    for action, tool in ACTION_TOOL_MAP.items():
        assert tool in TOOL_PERMISSIONS, f"{action} maps to ungranted {tool}"
