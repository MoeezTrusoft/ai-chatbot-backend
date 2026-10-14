import base64
import json
from uuid import UUID

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bookcraft.infra.config import Settings
from bookcraft.infra.sales_mcp import (
    HEADER_CORRELATION,
    HEADER_INTERNAL,
    HEADER_SERVICE_AUTHORIZATION,
    SalesMcpClient,
    SalesMcpConfigError,
    ServiceTokenSigner,
    call_action,
    parse_tenant_allowlist,
    tool_for_action,
    unwrap_tool_result,
)

TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_TENANT = UUID("22222222-2222-4222-8222-222222222222")
SITE_ID = "demo"


def make_key() -> tuple[Ed25519PrivateKey, str]:
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return key, pem


def make_settings(**overrides: object) -> Settings:
    _, pem = make_key()
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


def tool_result(payload: dict[str, object]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "id": "act-1",
            "result": {"content": [{"type": "text", "text": json.dumps(payload)}]},
        },
    )


def client_with(handler, settings: Settings) -> tuple[SalesMcpClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(recording))
    return SalesMcpClient(settings, http_client=http), seen


# ── Service-JWT (S3.3.2) ────────────────────────────────────────────────────
def test_minted_token_is_a_verifiable_eddsa_jws_for_the_bound_tenant() -> None:
    key, pem = make_key()
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    settings = make_settings(sales_mcp_signing_key=pem)
    token = ServiceTokenSigner(settings).mint(TENANT_ID, scopes="sales.lead.read")

    header_raw, payload_raw, signature_raw = token.split(".")
    assert json.loads(_unb64(header_raw)) == {
        "alg": "EdDSA",
        "typ": "JWT",
        "kid": "ai-chatbot-backend",
    }
    claims = json.loads(_unb64(payload_raw))
    assert claims["iss"] == "ai-chatbot-backend"
    assert claims["aud"] == "internal"
    assert claims["sub"] == "svc:ai-chatbot-backend"
    assert claims["tid"] == str(TENANT_ID)
    assert claims["tenantId"] == str(TENANT_ID)
    assert claims["scope"] == "sales.lead.read"
    assert claims["exp"] - claims["iat"] == 120

    public_key = serialization.load_pem_public_key(public_pem.encode())
    signature = _unb64_bytes(signature_raw)
    public_key.verify(signature, f"{header_raw}.{payload_raw}".encode("ascii"))


def test_signer_never_exposes_the_raw_private_key_material() -> None:
    _, pem = make_key()
    signer = ServiceTokenSigner(make_settings(sales_mcp_signing_key=pem))
    signer.mint(TENANT_ID, scopes="sales.lead.read")
    assert "PRIVATE KEY" not in repr(signer)
    assert pem not in repr(signer)


def test_missing_or_wrong_key_material_is_rejected() -> None:
    with pytest.raises(SalesMcpConfigError):
        ServiceTokenSigner(make_settings(sales_mcp_signing_key=None)).mint(
            TENANT_ID, scopes="sales.lead.read"
        )
    with pytest.raises(SalesMcpConfigError):
        ServiceTokenSigner(make_settings(sales_mcp_signing_key="not-a-key")).mint(
            TENANT_ID, scopes="sales.lead.read"
        )


def test_jwk_key_material_is_accepted() -> None:
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    jwk = json.dumps({"kty": "OKP", "crv": "Ed25519", "d": _b64(raw)})
    token = ServiceTokenSigner(make_settings(sales_mcp_signing_key=jwk)).mint(
        TENANT_ID, scopes="sales.lead.read"
    )
    assert token.count(".") == 2


# ── Wire shape (S3.2.1) ─────────────────────────────────────────────────────
async def test_tools_call_sends_the_pinned_jsonrpc_envelope_and_headers() -> None:
    settings = make_settings()
    client, seen = client_with(lambda _: tool_result({"id": "lead-1"}), settings)
    result = await client.call_tool(
        "sales_lead_create",
        {"source": "Chatbot:demo"},
        tenant_id=TENANT_ID,
        request_id="act-1",
        correlation_id="corr-9",
    )

    assert result.ok is True
    assert result.data == {"id": "lead-1"}
    request = seen[0]
    assert str(request.url) == "https://sales.example/mcp"
    body = json.loads(request.content)
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == "act-1"
    assert body["method"] == "tools/call"
    assert body["params"] == {"name": "sales_lead_create", "arguments": {"source": "Chatbot:demo"}}
    assert request.headers[HEADER_INTERNAL] == "true"
    assert request.headers[HEADER_CORRELATION] == "corr-9"
    token_claims = json.loads(_unb64(request.headers[HEADER_SERVICE_AUTHORIZATION].split(".")[1]))
    assert token_claims["tid"] == str(TENANT_ID)


async def test_an_mcp_suffixed_url_is_not_doubled() -> None:
    settings = make_settings(sales_mcp_url="https://sales.example/mcp/")
    client, seen = client_with(lambda _: tool_result({}), settings)
    await client.call_tool("sales_lead_list", {}, tenant_id=TENANT_ID)
    assert str(seen[0].url) == "https://sales.example/mcp"


async def test_reads_and_bookings_use_their_own_timeouts() -> None:
    settings = make_settings(
        sales_mcp_timeout_read_seconds=1.0,
        sales_mcp_timeout_write_seconds=2.0,
        sales_mcp_timeout_consultation_seconds=3.0,
    )
    client, seen = client_with(lambda _: tool_result({}), settings)
    await client.call_tool("sales_lead_list", {}, tenant_id=TENANT_ID)
    await client.call_tool("sales_lead_create", {}, tenant_id=TENANT_ID)
    await client.call_tool("sales_consultation_schedule", {}, tenant_id=TENANT_ID)
    assert [request.extensions["timeout"]["read"] for request in seen] == [1.0, 2.0, 3.0]


async def test_a_caller_supplied_request_id_is_reused_for_the_jsonrpc_id() -> None:
    settings = make_settings()
    client, seen = client_with(lambda _: tool_result({}), settings)
    await client.call_tool("sales_lead_list", {}, tenant_id=TENANT_ID, request_id="stable-1")
    await client.call_tool("sales_lead_list", {}, tenant_id=TENANT_ID, request_id="stable-1")
    assert [json.loads(r.content)["id"] for r in seen] == ["stable-1", "stable-1"]


# ── Retry / circuit breaker (S3.2.1) ───────────────────────────────────────
async def test_a_transient_5xx_is_retried_with_the_same_id_and_succeeds() -> None:
    settings = make_settings(sales_mcp_max_attempts=2)
    responses = [httpx.Response(503, text="busy"), tool_result({"ok": True})]
    client, seen = client_with(lambda _: responses.pop(0), settings)

    result = await client.call_tool(
        "sales_lead_create", {"source": "Chatbot:demo"}, tenant_id=TENANT_ID, request_id="act-7"
    )

    assert result.ok is True
    assert result.attempts == 2
    assert [json.loads(r.content)["id"] for r in seen] == ["act-7", "act-7"]


async def test_a_timeout_degrades_instead_of_raising() -> None:
    settings = make_settings(sales_mcp_max_attempts=2)

    def boom(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("no route to Sales")

    client, seen = client_with(boom, settings)
    result = await client.call_tool("sales_lead_create", {}, tenant_id=TENANT_ID)

    assert result.ok is False
    assert result.degraded is True
    assert result.retryable is True
    assert result.code == "sales_unavailable"
    assert len(seen) == 2


async def test_the_circuit_opens_after_repeated_failures() -> None:
    settings = make_settings(
        sales_mcp_max_attempts=1,
        sales_mcp_circuit_failure_threshold=2,
        sales_mcp_circuit_cooldown_seconds=60.0,
    )
    client, seen = client_with(lambda _: httpx.Response(500, text="down"), settings)

    for _ in range(2):
        assert (await client.call_tool("sales_lead_list", {}, tenant_id=TENANT_ID)).ok is False
    blocked = await client.call_tool("sales_lead_list", {}, tenant_id=TENANT_ID)

    assert blocked.code == "sales_unavailable"
    assert blocked.degraded is True
    # The third attempt never reached the network.
    assert len(seen) == 2


# ── Error mapping (S3.2.3) ─────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("sales_code", "expected_code", "degraded", "retryable"),
    [
        ("SALES-0422", "sales_invalid_args", True, False),
        ("SALES-0403", "sales_forbidden", True, False),
        ("SALES-0331", "sales_no_scope", True, False),
        ("SALES-0404", "sales_not_found", True, False),
    ],
)
async def test_domain_errors_map_to_bot_facing_codes(
    sales_code: str, expected_code: str, degraded: bool, retryable: bool
) -> None:
    settings = make_settings(sales_mcp_max_attempts=1)
    response = httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "id": "act-1",
            "error": {"code": -32000, "message": "nope", "data": {"code": sales_code}},
        },
    )
    client, _ = client_with(lambda _: response, settings)
    result = await client.call_tool("sales_lead_create", {}, tenant_id=TENANT_ID)

    assert result.ok is False
    assert result.code == expected_code
    assert result.sales_code == sales_code
    assert result.degraded is degraded
    assert result.retryable is retryable


async def test_a_conflict_is_retryable_and_maps_to_the_conflict_code() -> None:
    settings = make_settings(sales_mcp_max_attempts=1)
    response = httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "id": "1",
            "error": {"code": -32000, "data": {"code": "SALES-0409"}},
        },
    )
    client, _ = client_with(lambda _: response, settings)
    result = await client.call_tool("sales_lead_create", {}, tenant_id=TENANT_ID)
    assert (result.code, result.retryable, result.degraded) == ("sales_conflict", True, True)


async def test_a_403_perimeter_denial_degrades_without_retrying() -> None:
    settings = make_settings(sales_mcp_max_attempts=2)
    client, seen = client_with(lambda _: httpx.Response(403, text="denied"), settings)
    result = await client.call_tool("sales_lead_create", {}, tenant_id=TENANT_ID)
    assert (result.code, result.degraded, result.retryable) == ("sales_forbidden", True, False)
    assert len(seen) == 1


async def test_a_non_json_body_is_treated_as_an_upstream_failure() -> None:
    settings = make_settings(sales_mcp_max_attempts=1)
    client, _ = client_with(lambda _: httpx.Response(200, text="<html>oops</html>"), settings)
    result = await client.call_tool("sales_lead_create", {}, tenant_id=TENANT_ID)
    assert (result.ok, result.degraded) == (False, True)


# ── Tenant allow-list + switches (S3.3.3) ──────────────────────────────────
async def test_a_tenant_outside_the_allow_list_never_reaches_sales() -> None:
    settings = make_settings()
    client, seen = client_with(lambda _: tool_result({}), settings)

    result = await client.call_tool("sales_lead_create", {}, tenant_id=OTHER_TENANT)

    assert result.code == "sales_tenant_not_allowed"
    assert result.degraded is True
    assert seen == []
    with pytest.raises(SalesMcpConfigError):
        client.resolve_site("not-registered")


async def test_a_turn_without_a_tenant_stays_local() -> None:
    settings = make_settings()
    client, seen = client_with(lambda _: tool_result({}), settings)
    result = await client.call_tool("sales_lead_create", {}, tenant_id="")
    assert (result.code, result.degraded) == ("sales_tenant_missing", True)
    assert seen == []


async def test_a_disabled_integration_degrades_without_calling_sales() -> None:
    settings = make_settings(sales_mcp_enabled=False)
    client, seen = client_with(lambda _: tool_result({}), settings)
    result = await client.call_tool("sales_lead_create", {}, tenant_id=TENANT_ID)
    assert (result.code, result.ok) == ("sales_integration_disabled", False)
    assert seen == []


async def test_a_tool_outside_the_pinned_allow_list_is_refused_locally() -> None:
    settings = make_settings()
    client, seen = client_with(lambda _: tool_result({}), settings)
    result = await client.call_tool("sales_refund_approve", {}, tenant_id=TENANT_ID)
    assert (result.code, result.degraded) == ("sales_tool_not_allowed", True)
    assert seen == []


def test_allow_list_entries_are_validated() -> None:
    bindings = parse_tenant_allowlist(
        json.dumps(
            [
                {
                    "siteId": SITE_ID,
                    "tenantId": str(TENANT_ID),
                    "ownerId": "owner-1",
                    "chatMode": "human",
                }
            ]
        )
    )
    assert bindings[SITE_ID].tenant_id == TENANT_ID
    assert bindings[SITE_ID].chat_mode == "human"
    assert parse_tenant_allowlist("[]") == {}
    assert parse_tenant_allowlist("") == {}
    malformed = [
        "{",
        '"demo"',
        '[{"siteId": "demo"}]',
        '[{"siteId":"a","tenantId":"nope","ownerId":"o"}]',
    ]
    for bad in malformed:
        with pytest.raises(SalesMcpConfigError):
            parse_tenant_allowlist(bad)


# ── Result unwrapping (S3.2.3) ─────────────────────────────────────────────
def test_content_blocks_are_unwrapped() -> None:
    assert unwrap_tool_result({"content": [{"type": "text", "text": '{"id": "lead-1"}'}]}) == {
        "id": "lead-1"
    }
    assert unwrap_tool_result({"content": [{"type": "text", "text": "not json"}]}) == {
        "text": "not json"
    }
    assert unwrap_tool_result({"content": [{"type": "text", "text": "[1,2]"}]}) == {"items": [1, 2]}
    assert unwrap_tool_result({"structuredContent": {"id": "lead-2"}}) == {"id": "lead-2"}
    assert unwrap_tool_result({}) == {"raw": {}}


# ── Action mapping (S3.2.2) ────────────────────────────────────────────────
def test_actions_map_to_tools() -> None:
    assert tool_for_action("lead_create") == "sales_lead_create"
    assert tool_for_action("consultation_schedule") == "sales_consultation_schedule"
    assert tool_for_action("not_an_action") is None


async def test_an_unmapped_action_degrades_without_calling_sales() -> None:
    settings = make_settings()
    client, seen = client_with(lambda _: tool_result({}), settings)
    result = await call_action(client, "not_an_action", {}, tenant_id=TENANT_ID, request_id=None)
    assert result.code == "sales_action_unmapped"
    assert seen == []


async def test_call_action_routes_a_lead_create_with_its_request_id() -> None:
    settings = make_settings()
    client, seen = client_with(lambda _: tool_result({"id": "lead-9"}), settings)
    result = await call_action(
        client,
        "lead_create",
        {"source": "Chatbot:demo"},
        tenant_id=TENANT_ID,
        request_id="thread-42:lead_create",
    )
    assert result.ok is True
    body = json.loads(seen[0].content)
    assert body["params"]["name"] == "sales_lead_create"
    assert body["id"] == "thread-42:lead_create"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(value: str) -> str:
    return _unb64_bytes(value).decode("utf-8")


def _unb64_bytes(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
