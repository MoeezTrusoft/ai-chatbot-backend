"""HTTP surface for the chatbot's MCP server (C4, S3.6).

Mounted only when ``CHATBOT_MCP_SERVER_ENABLED=true``: an off deployment has no
`/mcp` route at all, so this cannot change existing REST/WS behaviour (S3.6.4).

Authentication is the same shape the bot presents to Sales — an Ed25519 service
token in ``x-service-authorization`` with a registered `kid`, an allow-listed
issuer and ``aud=internal`` — so both halves of the bridge use one credential
format (S3.6.3). Identity comes only from that verified token.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request, status

from bookcraft.components.mcp import ChatbotMcpServer
from bookcraft.infra.config import Settings
from bookcraft.infra.service_tokens import (
    ServicePrincipal,
    ServiceTokenError,
    build_public_key_registry,
    parse_issuer_allowlist,
    verify_service_token,
)

_logger = structlog.get_logger(__name__)

router = APIRouter(tags=["mcp"])

HEADER_SERVICE_AUTHORIZATION = "x-service-authorization"
HEADER_INTERNAL = "x-mcp-internal"
HEADER_CORRELATION = "x-correlation-id"


def _service_token(request: Request) -> str | None:
    raw = request.headers.get(HEADER_SERVICE_AUTHORIZATION)
    if not isinstance(raw, str):
        return None
    trimmed = raw.strip()
    if trimmed.lower().startswith("bearer "):
        trimmed = trimmed[7:].strip()
    return trimmed or None


def _verify_caller(request: Request, settings: Settings) -> ServicePrincipal:
    registry = build_public_key_registry(settings.chatbot_mcp_public_keys_json)
    try:
        return verify_service_token(
            _service_token(request),
            registry=registry,
            issuers=parse_issuer_allowlist(settings.chatbot_mcp_eddsa_issuers),
            audience=settings.chatbot_mcp_audience,
        )
    except ServiceTokenError as exc:
        # The reason is logged, never returned: a caller learns only that it is
        # not authorized, not which key or claim it got wrong.
        _logger.warning(
            "chatbot_mcp_auth_rejected",
            reason=str(exc),
            correlation_id=request.headers.get(HEADER_CORRELATION),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "unauthorized"},
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


@router.post("/mcp")
async def mcp_endpoint(request: Request) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    if not settings.chatbot_mcp_server_enabled:
        # Defence in depth: even if the route is mounted by mistake, an off
        # deployment does not answer.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"error": "not_found"})

    principal = _verify_caller(request, settings)
    server: ChatbotMcpServer | None = request.app.state.mcp_server
    if server is None:  # pragma: no cover - the mount wires both together
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"error": "mcp_unavailable"}
        )

    try:
        message = await request.json()
    except ValueError:
        return _invalid_request(None)
    if not isinstance(message, dict):
        return _invalid_request(None)

    return await server.handle_rpc(message, principal)


def _invalid_request(call_id: object) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": call_id,
        "error": {"code": -32600, "message": "Invalid request"},
    }
