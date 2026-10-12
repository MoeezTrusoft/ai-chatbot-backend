"""Sales policy boundary around the official MCP SDK and Streamable HTTP client."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from collections.abc import AsyncIterator
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx2 as httpx
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp_types import CONNECTION_CLOSED, PARSE_ERROR, REQUEST_TIMEOUT, TextContent

from bookcraft.infra.config import Settings

# Deliberately exclude administrative configuration, assignments, and completion.
TOOL_SCOPES = {
    "sales_customer_get": "sales.customer.read",
    "sales_customer_update": "sales.customer.update",
    "sales_lead_create": "sales.lead.create",
    "sales_consultation_config_get": "sales.config.read",
    **{
        f"sales_consultation_{action}": "sales.lead.read"
        for action in ("availability", "list", "get")
    },
    **{
        f"sales_consultation_{action}": "sales.lead.update"
        for action in ("schedule", "confirm", "reschedule", "cancel")
    },
}


class SalesError(RuntimeError):
    """Safe public error; never contains upstream messages, tokens, or customer data."""

    def __init__(self, code: str, status: int = 503, *, uncertain: bool = False):
        super().__init__(code)
        self.code = code
        self.status = status
        self.uncertain = uncertain


MAX_RESPONSE_BYTES = 1_048_576

_sales_call: ContextVar[bool] = ContextVar("sales_mcp_call", default=False)


class _SafeSdkLog(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # The SDK's debug messages and parse-error tracebacks contain raw tool
        # arguments/results. Keep severity/source while removing customer data.
        if _sales_call.get():
            record.msg = "Sales MCP protocol event"
            record.args = ()
            record.exc_info = record.exc_text = record.stack_info = None
        return True


def _protect_sdk_logs() -> None:
    for name in (
        "mcp.client.streamable_http",
        "mcp.client.client",
        "client",
        "mcp.shared.jsonrpc_dispatcher",
        "mcp.shared.dispatcher",
        "mcp.shared.tool_name_validation",
    ):
        logger = logging.getLogger(name)
        if not any(isinstance(item, _SafeSdkLog) for item in logger.filters):
            logger.addFilter(_SafeSdkLog())


class _LimitedStream(httpx.AsyncByteStream):
    """Bound HTTP bodies; JSON-RPC and SSE parsing belong entirely to the SDK."""

    def __init__(self, stream: httpx.AsyncByteStream):
        self.stream = stream

    async def __aiter__(self) -> AsyncIterator[bytes]:
        size = 0
        async for chunk in self.stream:
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise SalesError("sales_invalid_response", 502, uncertain=True)
            yield chunk

    async def aclose(self) -> None:
        await self.stream.aclose()


class _SalesHttpGuard(httpx.AsyncBaseTransport):
    """Borrow a connection pool and enforce the Sales endpoint's HTTP limits."""

    def __init__(self, pool: httpx.AsyncBaseTransport, url: str):
        self.pool, self.url = pool, httpx.URL(url)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url != self.url:
            raise SalesError("sales_endpoint_mismatch", 502)
        response = await self.pool.handle_async_request(request)
        code = response.status_code
        # Sales has no optional GET notification stream or DELETE session endpoint.
        optional_endpoint = request.method in {"GET", "DELETE"} and code in {404, 405}
        if not (200 <= code < 300 or optional_endpoint):
            await response.aclose()
            raise SalesError(
                "sales_service_unavailable" if code >= 500 else "sales_request_rejected",
                503 if code >= 500 or code in {401, 403, 429} else 502,
                uncertain=code >= 500,
            )
        # Request identity encoding so the byte limit also bounds decoded bodies.
        if response.headers.get("content-encoding", "identity") != "identity":
            await response.aclose()
            raise SalesError("sales_invalid_response", 502, uncertain=True)
        assert isinstance(response.stream, httpx.AsyncByteStream)
        response.stream = _LimitedStream(response.stream)
        return response

    # The parent Sales client owns the pool, not an individual MCP session.
    async def aclose(self) -> None:
        pass


def _public_error(exc: Exception) -> SalesError:
    if isinstance(exc, ExceptionGroup):
        # AnyIO owns the SDK's concurrent transport tasks. Preserve a domain/HTTP
        # failure when it is wrapped during session cleanup.
        return _public_error(exc.exceptions[0])
    if isinstance(exc, SalesError):
        return exc
    if isinstance(exc, MCPError):
        data = exc.data if isinstance(exc.data, dict) else {}
        status = data.get("status")
        if type(status) is not int:
            status = None
        # Sales uses -32000 for domain errors; that value also means connection
        # closed in the SDK. Its explicit HTTP-style domain status takes priority.
        if status is None and exc.code in {REQUEST_TIMEOUT, CONNECTION_CLOSED}:
            return SalesError("sales_outcome_unknown_retry_same_request", uncertain=True)
        if exc.code == PARSE_ERROR:
            return SalesError("sales_invalid_response", 502, uncertain=True)
        return SalesError(
            {
                404: "sales_record_not_found",
                409: "sales_conflict_refresh_required",
                422: "sales_invalid_request",
            }.get(status or 0, "sales_request_rejected"),
            status if status in {404, 409, 422} else 503,
            uncertain=status is None or status >= 500,
        )
    if isinstance(exc, (TimeoutError, httpx.HTTPError)):
        return SalesError("sales_outcome_unknown_retry_same_request", uncertain=True)
    return SalesError("sales_invalid_response", 502, uncertain=True)


class SalesMcpClient:
    """Tenant-bound SDK calls. No custom JSON-RPC framing or automatic write retries."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        _protect_sdk_logs()
        self.transport = transport or httpx.AsyncHTTPTransport(
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            retries=0,
            trust_env=False,
        )

    async def aclose(self) -> None:
        await self.transport.aclose()

    async def _credential(self, tool: str, *, full_contract: bool = False) -> str:
        try:
            path = Path(self.settings.sales_service_jwt_file or "")
            token = (await asyncio.to_thread(path.read_text, encoding="utf-8")).strip()
            if len(token) > 16384 or "\n" in token or "\r" in token:
                raise ValueError("invalid credential")
            header, payload, signature = token.split(".")

            def decode(value: str) -> dict[str, Any]:
                result = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
                if not isinstance(result, dict):
                    raise ValueError("invalid claims")
                return result

            claims, meta = decode(payload), decode(header)
            tenant = claims.get("tenantId") or claims.get("tid") or claims.get("tenant_id")
            actor = claims.get("userId") or claims.get("uid")
            audience = claims.get("aud")
            scope = claims.get("scope", [])
            scopes = scope.split() if isinstance(scope, str) else scope
            if (
                meta.get("alg") != "EdDSA"
                or not meta.get("kid")
                or not signature
                or claims.get("iss") not in {"erp-rbac", "erp-gateway"}
                or (audience != "internal" and audience != ["internal"])
                or tenant != self.settings.sales_tenant_id
                or actor != self.settings.sales_actor_id
                or not isinstance(claims.get("exp"), (int, float))
                or claims["exp"] <= time.time() + 5
                or not isinstance(scopes, list)
                or not (
                    set(TOOL_SCOPES.values()) if full_contract else {TOOL_SCOPES[tool]}
                ).issubset(scopes)
            ):
                raise ValueError("credential context mismatch")
            # This is a trusted local issuer output, not an inbound bearer token.
            # Sales verifies the signature. Checks here prevent changing the actor
            # or tenant when retrying a durable request after token rotation.
            return token
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise SalesError("sales_service_credential_unavailable") from exc

    async def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        return await self._call(tool, arguments)

    async def check_connection(self) -> dict[str, Any]:
        """Read-only preflight for every required tool and credential permission."""
        result = await self._call("sales_consultation_config_get", {}, full_contract=True)
        policy = result.get("policy") if isinstance(result, dict) else None
        if (
            not isinstance(policy, dict)
            or type(policy.get("enabled")) is not bool
            or not isinstance(policy.get("timezone"), str)
        ):
            raise SalesError("sales_invalid_response", 502)
        return {
            "status": "connected",
            "required_tools": len(TOOL_SCOPES),
            "booking_enabled": policy["enabled"],
            "business_timezone": policy["timezone"],
        }

    async def _call(
        self, tool: str, arguments: dict[str, Any], *, full_contract: bool = False
    ) -> Any:
        if tool not in TOOL_SCOPES:
            raise SalesError("sales_tool_not_allowed", 403)
        log_context = _sales_call.set(True)
        try:
            async with asyncio.timeout(self.settings.sales_timeout_seconds):
                token = await self._credential(tool, full_contract=full_contract)
                # Each call owns its MCP session and immutable headers. Concurrent
                # customers/rotated tokens cannot share session IDs or credentials;
                # the underlying HTTP connection pool is still reused.
                async with httpx.AsyncClient(
                    transport=_SalesHttpGuard(self.transport, str(self.settings.sales_mcp_url)),
                    timeout=httpx.Timeout(self.settings.sales_timeout_seconds, connect=5.0),
                    headers={
                        "X-Service-Authorization": f"Bearer {token}",
                        "X-MCP-Internal": str(self.settings.sales_mcp_internal_secret),
                        "X-Correlation-ID": str(uuid4()),
                        "Accept-Encoding": "identity",
                    },
                    follow_redirects=False,
                    trust_env=False,
                ) as http_client:
                    transport = streamable_http_client(
                        str(self.settings.sales_mcp_url),
                        http_client=http_client,
                        max_sse_event_size=MAX_RESPONSE_BYTES,
                    )
                    # Sales negotiates the 2025-06-18 handshake. Skip the newer
                    # server/discover probe, which this server does not implement.
                    async with Client(
                        transport,
                        mode="legacy",
                        cache=None,
                        read_timeout_seconds=self.settings.sales_timeout_seconds,
                    ) as mcp:
                        if mcp.server_capabilities.tools is None:
                            raise SalesError("sales_tools_unavailable", 503)
                        cursor = None
                        missing = set(TOOL_SCOPES) if full_contract else {tool}
                        for _ in range(20):
                            listing = await mcp.list_tools(cursor=cursor)
                            missing.difference_update(item.name for item in listing.tools)
                            if not missing:
                                break
                            cursor = listing.next_cursor
                            if cursor is None:
                                raise SalesError("sales_tool_unavailable", 503)
                        else:
                            raise SalesError("sales_invalid_response", 502)
                        result = await mcp.call_tool(tool, arguments)
                        if result.is_error:
                            raise SalesError("sales_request_rejected", 503, uncertain=True)
                        if result.structured_content is not None:
                            return result.structured_content
                        if len(result.content) != 1 or not isinstance(
                            result.content[0], TextContent
                        ):
                            raise SalesError("sales_invalid_response", 502, uncertain=True)
                        return json.loads(result.content[0].text)
        except Exception as exc:
            # Do not expose SDK errors, upstream text, validation payloads or tokens.
            # CancelledError is a BaseException and deliberately propagates.
            raise _public_error(exc) from None
        finally:
            _sales_call.reset(log_context)
