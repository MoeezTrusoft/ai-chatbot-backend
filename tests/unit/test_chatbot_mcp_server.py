"""The chatbot's MCP server: protocol, permission gate, routing, and mounting."""

import base64
import json
import time
from uuid import UUID

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bookcraft.components.mcp import (
    CHATBOT_MCP_TOOLS,
    TOOL_NAMES,
    TOOLS_BY_NAME,
    ChatbotMcpServer,
    listed_tools,
)
from bookcraft.infra.config import Settings
from bookcraft.infra.service_tokens import ServicePrincipal

THREAD_ID = UUID("33333333-3333-4333-8333-333333333333")

TURN_PRINCIPAL = ServicePrincipal(
    subject="svc:erp-sales-backend",
    issuer="erp-sales-backend",
    scopes=frozenset(
        {
            "chat.conversation.write",
            "chat.greeting.read",
            "chat.state.read",
            "pricing.quote.get",
            "portfolio.sample.list",
            "documents.document.export",
        }
    ),
)


def call(message: dict[str, object], principal: ServicePrincipal | None = TURN_PRINCIPAL) -> dict:
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": message}


class Recorder:
    def __init__(self) -> None:
        self.turns: list[dict] = []
        self.greets: list[dict] = []
        self.tools: list[tuple[str, dict]] = []
        self.loaded: list[UUID] = []

    def server(self, *, failing_tool: str | None = None) -> ChatbotMcpServer:
        async def handle_turn(arguments: dict) -> dict:
            self.turns.append(dict(arguments))
            return {"bubbles": [{"text": "hi"}], "blocked": False}

        async def handle_greet(arguments: dict) -> dict:
            self.greets.append(dict(arguments))
            return {"bubbles": [{"text": "welcome"}]}

        async def invoke_tool(name: str, arguments: dict, principal) -> dict:
            self.tools.append((name, dict(arguments)))
            if failing_tool and name == failing_tool:
                raise RuntimeError("boom")
            return {"tool_name": name, "status": "ok", "result": {"ok": True}}

        async def load_state(thread_id: UUID) -> dict:
            self.loaded.append(thread_id)
            return {"thread_id": str(thread_id), "version": 2, "state": {"sales_stage": "New"}}

        return ChatbotMcpServer(
            handle_turn=handle_turn,
            handle_greet=handle_greet,
            invoke_tool=invoke_tool,
            load_thread_state=load_state,
        )


# ── Manifest / protocol (S3.6.1, S3.6.3) ───────────────────────────────────
def test_the_manifest_exposes_the_six_read_mostly_capabilities() -> None:
    assert TOOL_NAMES == (
        "chat.turn",
        "chat.greet",
        "chat.state",
        "pricing.quote",
        "portfolio.lookup",
        "documents.render",
    )
    for tool in CHATBOT_MCP_TOOLS:
        assert tool.input_schema["type"] == "object"
        assert tool.permission and tool.permission.count(".") == 2


async def test_tools_list_is_stable_and_needs_no_authentication() -> None:
    server = Recorder().server()
    first = await server.handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, None)
    second = await server.handle_rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, None)

    assert first["result"] == {"tools": listed_tools()}
    assert json.dumps(first["result"]) == json.dumps(second["result"])
    assert [t["name"] for t in first["result"]["tools"]] == list(TOOL_NAMES)


async def test_initialize_pins_the_protocol_and_server_identity() -> None:
    server = Recorder().server()
    response = await server.handle_rpc(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "old"}},
        None,
    )
    assert response["result"] == {
        "protocolVersion": "2025-06-18",
        "serverInfo": {"name": "bookcraft-chatbot", "version": "1.0.0"},
        "capabilities": {"tools": {"listChanged": False}},
    }


async def test_an_unknown_method_is_a_protocol_error() -> None:
    server = Recorder().server()
    response = await server.handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "nope"}, None)
    assert response["error"]["code"] == -32601


# ── Permission gate (S3.6.3) ───────────────────────────────────────────────
async def test_a_call_without_a_principal_is_refused() -> None:
    recorder = Recorder()
    response = await recorder.server().handle_rpc(
        call({"name": "chat.turn", "arguments": {"thread_id": str(THREAD_ID), "message": "hi"}}),
        None,
    )
    assert response["error"]["data"]["code"] == "CHATBOT-0401"
    assert recorder.turns == []


async def test_a_missing_scope_is_forbidden_and_the_tool_never_runs() -> None:
    recorder = Recorder()
    narrow = ServicePrincipal(
        subject="svc:other", issuer="erp-gateway", scopes=frozenset({"chat.state.read"})
    )
    response = await recorder.server().handle_rpc(
        call({"name": "chat.turn", "arguments": {"thread_id": str(THREAD_ID), "message": "hi"}}),
        narrow,
    )
    assert response["error"]["data"]["code"] == "CHATBOT-0403"
    assert recorder.turns == []


async def test_an_unknown_tool_is_not_found() -> None:
    server = Recorder().server()
    response = await server.handle_rpc(
        call({"name": "chat.delete", "arguments": {}}), TURN_PRINCIPAL
    )
    assert response["error"]["data"]["code"] == "CHATBOT-0404"


async def test_missing_required_arguments_are_rejected_before_dispatch() -> None:
    recorder = Recorder()
    response = await recorder.server().handle_rpc(
        call({"name": "chat.turn", "arguments": {"message": "hi"}}), TURN_PRINCIPAL
    )
    assert response["error"]["data"]["code"] == "CHATBOT-0422"
    assert "thread_id" in response["error"]["message"]
    assert recorder.turns == []


# ── Routing to the in-process capability (S3.6.2) ──────────────────────────
async def test_chat_turn_reaches_the_chat_service_with_its_arguments() -> None:
    recorder = Recorder()
    response = await recorder.server().handle_rpc(
        call({"name": "chat.turn", "arguments": {"thread_id": str(THREAD_ID), "message": "hi"}}),
        TURN_PRINCIPAL,
    )
    assert response["result"]["content"][0]["type"] == "text"
    assert json.loads(response["result"]["content"][0]["text"])["bubbles"][0]["text"] == "hi"
    assert recorder.turns == [{"thread_id": str(THREAD_ID), "message": "hi"}]


async def test_chat_state_reads_the_thread_repository() -> None:
    recorder = Recorder()
    response = await recorder.server().handle_rpc(
        call({"name": "chat.state", "arguments": {"thread_id": str(THREAD_ID)}}), TURN_PRINCIPAL
    )
    assert recorder.loaded == [THREAD_ID]
    assert json.loads(response["result"]["content"][0]["text"])["version"] == 2


async def test_pricing_and_portfolio_route_to_the_registry_tools() -> None:
    recorder = Recorder()
    server = recorder.server()
    await server.handle_rpc(
        call(
            {
                "name": "pricing.quote",
                "arguments": {"thread_id": str(THREAD_ID), "word_count": 100},
            }
        ),
        TURN_PRINCIPAL,
    )
    await server.handle_rpc(
        call(
            {
                "name": "portfolio.lookup",
                "arguments": {"thread_id": str(THREAD_ID), "genre": "fantasy"},
            }
        ),
        TURN_PRINCIPAL,
    )
    assert [name for name, _ in recorder.tools] == [
        "pricing.quote.estimate.v2",
        "portfolio.request_samples.v1",
    ]


async def test_documents_render_selects_the_document_tool_by_kind() -> None:
    recorder = Recorder()
    server = recorder.server()
    await server.handle_rpc(
        call(
            {
                "name": "documents.render",
                "arguments": {"kind": "agreement", "thread_id": str(THREAD_ID)},
            }
        ),
        TURN_PRINCIPAL,
    )
    await server.handle_rpc(
        call(
            {
                "name": "documents.render",
                "arguments": {"kind": "memo", "thread_id": str(THREAD_ID)},
            }
        ),
        TURN_PRINCIPAL,
    )
    assert [name for name, _ in recorder.tools] == ["documents.generate_agreement.v1"]


async def test_a_failing_collaborator_becomes_a_retryable_internal_error() -> None:
    recorder = Recorder()
    server = recorder.server(failing_tool="pricing.quote.estimate.v2")
    response = await server.handle_rpc(
        call({"name": "pricing.quote", "arguments": {"thread_id": str(THREAD_ID)}}), TURN_PRINCIPAL
    )
    assert response["error"]["data"] == {"code": "CHATBOT-0500", "retryable": True}
    assert response["error"]["code"] == -32603
    # The exception text never reaches the caller.
    assert "boom" not in json.dumps(response)


async def test_a_tool_without_its_collaborator_says_so_instead_of_failing_oddly() -> None:
    server = ChatbotMcpServer()
    response = await server.handle_rpc(
        call({"name": "chat.greet", "arguments": {}}), TURN_PRINCIPAL
    )
    assert response["error"]["data"]["code"] == "CHATBOT-0500"
    assert "not available" in response["error"]["message"]


# ── Mounting and auth over HTTP (S3.6.4) ───────────────────────────────────
REGISTERED_KID = "sales-1"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _sign(key: Ed25519PrivateKey, header: dict, claims: dict) -> str:
    signing_input = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(claims).encode())}"
    return f"{signing_input}.{_b64(key.sign(signing_input.encode()))}"


def _register(settings: Settings, key: Ed25519PrivateKey, kid: str = REGISTERED_KID) -> None:
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    settings.chatbot_mcp_public_keys_json = json.dumps(
        {kid: {"kty": "OKP", "crv": "Ed25519", "x": _b64(public)}}
    )


def _token(
    settings: Settings,
    *,
    issuer: str,
    scope: str,
    aud: str = "internal",
    presented_kid: str = REGISTERED_KID,
) -> str:
    """Register one key and sign with it, optionally claiming a different kid."""
    key = Ed25519PrivateKey.generate()
    _register(settings, key)
    claims = {
        "iss": issuer,
        "aud": aud,
        "sub": "svc:erp-sales-backend",
        "scope": scope,
        "exp": int(time.time()) + 300,
    }
    return _sign(key, {"alg": "EdDSA", "typ": "JWT", "kid": presented_kid}, claims)


def _mounted_app(settings: Settings) -> FastAPI:
    """Mount the router the way main.create_app does, over a stub server."""

    from bookcraft.api.mcp import router as mcp_router

    app = FastAPI()
    app.state.settings = settings
    app.state.mcp_server = Recorder().server()
    app.include_router(mcp_router)
    return app


def test_the_route_is_absent_and_answers_404_while_the_flag_is_off() -> None:
    settings = Settings(app_env="test")
    app = FastAPI()
    app.state.settings = settings
    from bookcraft.api.mcp import router as mcp_router

    app.include_router(mcp_router)

    with TestClient(app) as client:
        listed = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert listed.status_code == 404


def test_an_unsigned_call_is_rejected() -> None:
    settings = Settings(app_env="test", chatbot_mcp_server_enabled=True)
    with TestClient(_mounted_app(settings)) as client:
        response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response.status_code == 401


def test_a_valid_service_token_is_accepted() -> None:
    settings = Settings(app_env="test", chatbot_mcp_server_enabled=True)
    token = _token(settings, issuer="erp-sales-backend", scope="chat.conversation.write")
    with TestClient(_mounted_app(settings)) as client:
        response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"x-service-authorization": token},
        )
    assert response.status_code == 200
    assert [t["name"] for t in response.json()["result"]["tools"]] == list(TOOL_NAMES)


@pytest.mark.parametrize(
    ("presented_kid", "issuer", "aud"),
    [
        ("not-registered", "erp-sales-backend", "internal"),
        (REGISTERED_KID, "not-a-tier", "internal"),
        (REGISTERED_KID, "erp-sales-backend", "somewhere-else"),
    ],
)
def test_a_token_that_fails_any_check_is_unauthorized(
    presented_kid: str, issuer: str, aud: str
) -> None:
    settings = Settings(app_env="test", chatbot_mcp_server_enabled=True)
    token = _token(
        settings,
        issuer=issuer,
        scope="chat.state.read",
        aud=aud,
        presented_kid=presented_kid,
    )
    with TestClient(_mounted_app(settings)) as client:
        response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"x-service-authorization": token},
        )
    assert response.status_code == 401


def test_a_missing_permission_is_refused_while_the_manifest_stays_listable() -> None:
    settings = Settings(app_env="test", chatbot_mcp_server_enabled=True)
    token = _token(settings, issuer="erp-sales-backend", scope="chat.state.read")
    payload = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {
            "name": "chat.turn",
            "arguments": {"thread_id": str(THREAD_ID), "message": "hi"},
        },
    }
    with TestClient(_mounted_app(settings)) as client:
        listed = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"x-service-authorization": token},
        )
        denied = client.post("/mcp", json=payload, headers={"x-service-authorization": token})

    assert listed.status_code == 200
    assert denied.status_code == 200  # MCP errors ride in the envelope…
    assert denied.json()["error"]["data"]["code"] == "CHATBOT-0403"  # …with the domain code


def test_a_malformed_jsonrpc_body_is_a_protocol_error() -> None:
    settings = Settings(app_env="test", chatbot_mcp_server_enabled=True)
    token = _token(settings, issuer="erp-sales-backend", scope="chat.state.read")
    with TestClient(_mounted_app(settings)) as client:
        response = client.post(
            "/mcp",
            json={"id": 1, "method": "tools/list"},
            headers={"x-service-authorization": token},
        )
    assert response.json()["error"]["code"] == -32600


def test_the_tools_by_name_index_covers_the_manifest() -> None:
    assert set(TOOLS_BY_NAME) == set(TOOL_NAMES)
