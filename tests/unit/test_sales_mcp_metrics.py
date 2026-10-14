"""Bot half of the Website ↔ Chatbot funnel counters (S9.2.2).

The counters are the only way an operator can tell a silently-misconfigured
integration (every call degrading) from a healthy one, so each outcome the client
can produce is pinned here: an ok call, a degraded call, and a tenant-resolution
miss — the failure that disables the whole integration without an error anyone
sees.
"""

from __future__ import annotations

import json
from uuid import UUID

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bookcraft.infra.config import Settings
from bookcraft.infra.sales_mcp import SalesMcpClient
from bookcraft.infra.sales_mcp_metrics import (
    SALES_MCP_CALLS,
    SALES_MCP_DEGRADATIONS,
    SALES_MCP_TENANT_MISSES,
)

TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")


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
        "sales_mcp_max_attempts": 1,
        "sales_mcp_tenant_allowlist_json": json.dumps(
            [{"siteId": "demo", "tenantId": str(TENANT_ID), "ownerId": "owner-1"}]
        ),
    }
    values.update(overrides)
    return Settings(**values)


def count(counter, **labels: str) -> float:
    return counter.labels(**labels)._value.get()


def client_with(handler) -> SalesMcpClient:
    return SalesMcpClient(
        make_settings(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


def _reply(request: httpx.Request, *, status: int, body: dict) -> httpx.Response:
    call_id = json.loads(request.content)["id"]
    return httpx.Response(status, json={**body, "id": call_id})


async def test_a_successful_call_is_counted_as_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _reply(
            request,
            status=200,
            body={
                "jsonrpc": "2.0",
                "result": {"content": [{"type": "text", "text": json.dumps({"id": "lead-1"})}]},
            },
        )

    before = count(SALES_MCP_CALLS, tool="sales_lead_get", outcome="ok")
    result = await client_with(handler).call_tool("sales_lead_get", {}, tenant_id=TENANT_ID)

    assert result.ok is True
    assert count(SALES_MCP_CALLS, tool="sales_lead_get", outcome="ok") == before + 1


async def test_a_failed_call_is_counted_as_a_degradation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _reply(
            request,
            status=500,
            body={"jsonrpc": "2.0", "error": {"code": -32603, "message": "boom"}},
        )

    degraded_before = count(SALES_MCP_CALLS, tool="sales_lead_get", outcome="degraded")
    result = await client_with(handler).call_tool("sales_lead_get", {}, tenant_id=TENANT_ID)

    assert result.ok is False
    assert result.degraded is True
    assert count(SALES_MCP_CALLS, tool="sales_lead_get", outcome="degraded") == degraded_before + 1
    degradation_before = count(
        SALES_MCP_DEGRADATIONS, tool="sales_lead_get", code=str(result.code)
    )
    assert degradation_before >= 1


async def test_a_tenant_resolution_miss_is_counted_apart_from_the_call() -> None:
    # No HTTP: an allow-list miss must never reach Sales.
    client = SalesMcpClient(make_settings())
    before = count(SALES_MCP_TENANT_MISSES, code="sales_tenant_not_allowed")

    result = await client.call_tool("sales_lead_get", {}, tenant_id=None, site_id="not-listed")

    assert result.ok is False
    assert result.code == "sales_tenant_not_allowed"
    assert count(SALES_MCP_TENANT_MISSES, code="sales_tenant_not_allowed") == before + 1
