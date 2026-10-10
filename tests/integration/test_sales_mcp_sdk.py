"""Protocol-level regression tests using the official SDK, not a mocked MCP session."""

import asyncio
import json
import logging
from uuid import uuid4

import httpx2 as httpx
import pytest

from bookcraft.infra.config import Settings
from bookcraft.infra.sales_mcp import MAX_RESPONSE_BYTES, SalesError, SalesMcpClient
from tests.integration.test_sales_alignment import CUSTOMER, TENANT, SalesFixture, service_token


@pytest.fixture
async def endpoint(tmp_path):
    token_file = tmp_path / "service.jwt"
    token_file.write_text(service_token(), encoding="utf-8")
    settings = Settings(
        app_env="test",
        sales_mcp_url="https://sales.example.invalid/mcp",
        sales_mcp_internal_secret="internal-test-secret",  # noqa: S106
        sales_service_jwt_file=str(token_file),
        sales_tenant_id=TENANT,
        sales_actor_id="chatbot",
        sales_timeout_seconds=1,
    )
    fixture = SalesFixture()
    client = SalesMcpClient(settings, httpx.MockTransport(fixture))
    yield client, fixture, token_file
    await client.aclose()


def replace_response(client, fixture, change):
    def handle(request):
        response = fixture(request)
        if request.method == "POST":
            return change(request, json.loads(request.content), response)
        return response

    client.transport = httpx.MockTransport(handle)


async def test_sdk_lifecycle_negotiates_before_discovery_and_call(endpoint):
    client, fixture, _ = endpoint
    headers = []

    def inspect(request, rpc, response):
        headers.append((rpc["method"], dict(request.headers)))
        return response

    replace_response(client, fixture, inspect)
    result = await client.call("sales_customer_get", {"customerId": CUSTOMER})
    assert result["id"] == CUSTOMER
    methods = [message["method"] for message in fixture.messages]
    assert methods == ["initialize", "notifications/initialized", "tools/list", "tools/call"]
    for method, header in headers:
        assert "application/json" in header["accept"]
        assert "text/event-stream" in header["accept"]
        if method != "initialize":
            assert header["mcp-protocol-version"] == "2025-06-18"
    assert len({h["x-correlation-id"] for _, h in headers}) == 1


@pytest.mark.parametrize("sse", [False, True])
async def test_sdk_supports_json_and_sse_structured_results(endpoint, sse):
    client, fixture, _ = endpoint

    def result(request, rpc, response):
        if rpc["method"] != "tools/call":
            return response
        envelope = {
            "jsonrpc": "2.0",
            "id": rpc["id"],
            "result": {
                "content": [],
                "structuredContent": {"id": CUSTOMER},
            },
        }
        if sse:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content="event: message\ndata: " + json.dumps(envelope) + "\n\n",
            )
        return httpx.Response(200, json=envelope)

    replace_response(client, fixture, result)
    assert await client.call("sales_customer_get", {}) == {"id": CUSTOMER}


@pytest.mark.parametrize("status", [404, 409, 422])
async def test_sales_domain_status_takes_priority_over_sdk_error_code(endpoint, status):
    client, fixture, _ = endpoint

    def error(request, rpc, response):
        if rpc["method"] != "tools/call":
            return response
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": rpc["id"],
                "error": {
                    "code": -32000,
                    "message": "private CRM content",
                    "data": {"status": status},
                },
            },
        )

    replace_response(client, fixture, error)
    with pytest.raises(SalesError) as exc:
        await client.call("sales_customer_get", {})
    assert exc.value.status == status
    assert not exc.value.uncertain
    assert "private" not in str(exc.value)


@pytest.mark.parametrize("status", [307, 401, 403, 429, 503])
async def test_http_failure_does_not_redirect_or_replay(endpoint, status):
    client, fixture, _ = endpoint

    def error(request, rpc, response):
        if rpc["method"] != "tools/call":
            return response
        return httpx.Response(status, headers={"location": str(request.url) + "/redirect"})

    replace_response(client, fixture, error)
    with pytest.raises(SalesError) as exc:
        await client.call("sales_customer_get", {})
    assert exc.value.status == (502 if status == 307 else 503)
    assert len(fixture.calls) == 1


async def test_missing_tool_fails_before_business_call(endpoint):
    client, fixture, _ = endpoint

    def listing(request, rpc, response):
        if rpc["method"] == "tools/list":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": rpc["id"], "result": {"tools": []}}
            )
        return response

    replace_response(client, fixture, listing)
    with pytest.raises(SalesError, match="sales_tool_unavailable"):
        await client.call("sales_customer_get", {})
    assert not fixture.calls
    with pytest.raises(SalesError, match="sales_tool_not_allowed"):
        await client.call("sales_consultation_config_update", {})
    assert len([m for m in fixture.messages if m["method"] == "initialize"]) == 1


async def test_paginated_discovery_uses_sdk_cursor(endpoint):
    client, fixture, _ = endpoint

    def listing(request, rpc, response):
        if rpc["method"] == "tools/list" and not rpc.get("params", {}).get("cursor"):
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": rpc["id"],
                    "result": {"tools": [], "nextCursor": "next"},
                },
            )
        return response

    replace_response(client, fixture, listing)
    await client.call("sales_customer_get", {})
    lists = [m for m in fixture.messages if m["method"] == "tools/list"]
    assert len(lists) == 2
    assert lists[1]["params"]["cursor"] == "next"


async def test_oversized_response_is_bounded(endpoint):
    client, fixture, _ = endpoint

    def oversize(request, rpc, response):
        if rpc["method"] == "tools/call":
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=b" " * (MAX_RESPONSE_BYTES + 1),
            )
        return response

    replace_response(client, fixture, oversize)
    with pytest.raises(SalesError, match="sales_invalid_response") as exc:
        await client.call("sales_customer_get", {})
    assert exc.value.uncertain


async def test_malformed_response_does_not_leak_into_sdk_logs(endpoint, caplog):
    client, fixture, _ = endpoint

    def malformed(request, rpc, response):
        if rpc["method"] == "tools/call":
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content='{"private customer account": "invalid',
            )
        return response

    replace_response(client, fixture, malformed)
    with caplog.at_level(logging.DEBUG), pytest.raises(SalesError):
        await client.call("sales_customer_get", {"customerId": "private-argument"})
    assert "private customer account" not in caplog.text
    assert "private-argument" not in caplog.text
    assert "Sales MCP protocol event" in caplog.text


async def test_token_rotation_and_concurrent_calls_keep_headers_isolated(endpoint):
    client, fixture, token_file = endpoint
    entered = asyncio.Event()
    release = asyncio.Event()
    first_token = token_file.read_text(encoding="utf-8")
    captured = {}

    async def handle(request):
        if request.method != "POST":
            return fixture(request)
        group = request.headers["x-correlation-id"]
        captured.setdefault(group, set()).add(request.headers["x-service-authorization"])
        rpc = json.loads(request.content)
        if rpc["method"] == "tools/call" and not entered.is_set():
            entered.set()
            await release.wait()
        return fixture(request)

    client.transport = httpx.MockTransport(handle)
    first = asyncio.create_task(client.call("sales_customer_get", {}))
    await asyncio.wait_for(entered.wait(), timeout=1)
    new_token = service_token(jti=str(uuid4()))
    token_file.write_text(new_token, encoding="utf-8")
    try:
        await client.call("sales_customer_get", {})
    finally:
        release.set()
        await first
    assert list(captured.values()) == [{"Bearer " + first_token}, {"Bearer " + new_token}]


async def test_deadline_and_cancellation_do_not_automatically_retry(endpoint):
    client, fixture, _ = endpoint
    entered = asyncio.Event()

    async def hanging(request):
        response = fixture(request)
        if request.method == "POST" and json.loads(request.content)["method"] == "tools/call":
            entered.set()
            await asyncio.Event().wait()
        return response

    client.transport = httpx.MockTransport(hanging)
    client.settings = client.settings.model_copy(update={"sales_timeout_seconds": 0.2})
    with pytest.raises(SalesError, match="sales_outcome_unknown") as exc:
        await client.call("sales_customer_get", {})
    assert exc.value.uncertain
    assert len(fixture.calls) == 1
    entered.clear()
    task = asyncio.create_task(client.call("sales_customer_get", {}))
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(fixture.calls) == 2


@pytest.mark.parametrize("json_response", [False, True])
async def test_interoperates_with_official_mcp_server(endpoint, json_response):
    from mcp.server import MCPServer

    client, _, _ = endpoint
    server = MCPServer("Sales test server")

    @server.tool()
    async def sales_customer_get(customerId: str) -> dict[str, str]:
        return {"id": customerId}

    app = server.streamable_http_app(
        json_response=json_response, stateless_http=True, host="sales.example.invalid"
    )
    client.transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        assert await client.call("sales_customer_get", {"customerId": CUSTOMER}) == {"id": CUSTOMER}


async def test_http_pool_survives_sessions_and_closes_at_shutdown(endpoint):
    client, fixture, _ = endpoint

    class Pool(httpx.MockTransport):
        closed = 0

        async def aclose(self):
            self.closed += 1

    pool = Pool(fixture)
    client.transport = pool
    await client.call("sales_customer_get", {})
    await client.call("sales_customer_get", {})
    assert pool.closed == 0
    await client.aclose()
    assert pool.closed == 1
