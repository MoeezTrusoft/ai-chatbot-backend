"""Sales' internal JSON-RPC transport. Credentials come from the ERP token issuer."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

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


class SalesMcpClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.sales_timeout_seconds, connect=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            follow_redirects=False,
            trust_env=False,
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _credential(self, tool: str) -> str:
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
                or TOOL_SCOPES[tool] not in scopes
            ):
                raise ValueError("credential context mismatch")
            # This is a trusted local issuer output, not an inbound bearer token.
            # Sales verifies the signature. Checks here prevent changing the actor
            # or tenant when retrying a durable request after token rotation.
            return token
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise SalesError("sales_service_credential_unavailable") from exc

    async def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        try:
            async with asyncio.timeout(self.settings.sales_timeout_seconds):
                return await self._call(tool, arguments)
        except TimeoutError as exc:
            raise SalesError("sales_outcome_unknown_retry_same_request", uncertain=True) from exc

    async def _call(self, tool: str, arguments: dict[str, Any]) -> Any:
        if tool not in TOOL_SCOPES:
            raise SalesError("sales_tool_not_allowed", 403)
        token = await self._credential(tool)
        rpc_id = str(uuid4())
        try:
            async with self.client.stream(
                "POST",
                str(self.settings.sales_mcp_url),
                headers={
                    "X-Service-Authorization": f"Bearer {token}",
                    "X-MCP-Internal": str(self.settings.sales_mcp_internal_secret),
                    "X-Correlation-ID": rpc_id,
                },
                json={
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "method": "tools/call",
                    "params": {"name": tool, "arguments": arguments},
                },
            ) as response:
                if response.status_code != 200:
                    code = response.status_code
                    raise SalesError(
                        "sales_service_unavailable" if code >= 500 else "sales_request_rejected",
                        503 if code >= 500 or code in {401, 403, 429} else 502,
                        uncertain=code >= 500,
                    )
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 1_048_576:
                        raise SalesError("sales_invalid_response", 502, uncertain=True)
            data = json.loads(raw)
            if (
                not isinstance(data, dict)
                or data.get("jsonrpc") != "2.0"
                or data.get("id") != rpc_id
            ):
                raise ValueError("invalid envelope")
            if "error" in data:
                error = data["error"]
                status = (error.get("data") or {}).get("status")
                public_status = status if status in {404, 409, 422} else 503
                raise SalesError(
                    {
                        404: "sales_record_not_found",
                        409: "sales_conflict_refresh_required",
                        422: "sales_invalid_request",
                    }.get(public_status, "sales_request_rejected"),
                    public_status,
                    uncertain=status is None or status >= 500,
                )
            result = data["result"]
            if result.get("isError"):
                raise ValueError("tool failure")
            content = result["content"]
            if len(content) != 1 or content[0].get("type") != "text":
                raise ValueError("invalid content")
            return json.loads(content[0]["text"])
        except httpx.HTTPError as exc:
            raise SalesError("sales_outcome_unknown_retry_same_request", uncertain=True) from exc
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise SalesError("sales_invalid_response", 502, uncertain=True) from exc
