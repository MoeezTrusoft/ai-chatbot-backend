"""BookCraft → Sales MCP client (SALES-AI-CHATBOT-INTEGRATION-PLAN.md §P3, D2).

The chatbot writes CRM state by calling Sales' MCP `tools/call` endpoint
directly — no bespoke REST bridge (D2). Every call carries:

* ``X-MCP-Internal: true`` — the coarse perimeter gate (defense in depth only);
* ``X-Service-Authorization: <EdDSA JWT>`` — the real identity. The tenant comes
  *only* from the verified claim, never from request input;
* ``X-Correlation-Id`` — echoed by Sales and both services' logs (P1.3).

A chat turn must never hard-fail on an upstream problem: every failure is
returned as a :class:`SalesToolResult` with ``degraded=True`` so the caller can
fall back to the local scheduler (D3) instead of losing a lead (§6.4 rule 6).

The wire contract (tool allow-list, error codes, reaction map, timeouts) is
frozen in ``erp-contracts/src/mcp/sales-chatbot.js``; the mirrors here are
asserted against it by ``tests/unit/test_sales_mcp_contract_parity.py``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Final, Literal
from uuid import UUID, uuid4

import httpx
import structlog
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bookcraft.infra.config import Settings

_logger = structlog.get_logger(__name__)

# ── Frozen wire constants (mirror of contracts/src/mcp/sales-chatbot.js) ─────
CONTRACT_VERSION: Final = "sales-chatbot-mcp@1"
MCP_PROTOCOL_VERSION: Final = "2025-06-18"
SERVICE_ISSUER: Final = "ai-chatbot-backend"
SERVICE_PRINCIPAL: Final = "svc:ai-chatbot-backend"
SERVICE_AUDIENCE: Final = "internal"
SERVICE_ALGORITHM: Final = "EdDSA"

HEADER_INTERNAL: Final = "x-mcp-internal"
HEADER_SERVICE_AUTHORIZATION: Final = "x-service-authorization"
HEADER_CORRELATION: Final = "x-correlation-id"

# Service tokens are short-lived: they authenticate one hop and are never
# cached across turns, so a leaked token has a bounded blast radius.
TOKEN_TTL_SECONDS: Final = 120

# Tool → required Sales permission. Pinned so the minted `scope` claim is the
# exact grant set Sales registers for this principal (S3.1.3) — anything wider
# is a privilege leak, anything narrower fails the tool's own guard.
TOOL_PERMISSIONS: Final[dict[str, str]] = {
    "sales_lead_create": "sales.lead.create",
    "sales_lead_update": "sales.lead.update",
    "sales_lead_get": "sales.lead.read",
    "sales_lead_list": "sales.lead.read",
    "sales_chatroom_create": "sales.chatroom.create",
    "sales_chatroom_get": "sales.chatroom.read",
    "sales_chatroom_status_set": "sales.chatroom.update",
    "sales_message_create": "sales.lead.update",
    "sales_note_create": "sales.chatroom.update",
    "sales_consultation_availability": "sales.lead.read",
    "sales_consultation_schedule": "sales.lead.update",
    "sales_consultation_confirm": "sales.lead.update",
    "sales_consultation_reschedule": "sales.lead.update",
    "sales_consultation_cancel": "sales.lead.update",
    "sales_consultation_list": "sales.lead.read",
    "sales_consultation_assign": "sales.consultation_assignment.update",
    "sales_capture_submission_list": "sales.capture.read",
    "sales_activity_create": "sales.activity.create",
}

# Granted rate class, mirrored from the contract's BOT_TO_SALES_GRANTS. It is
# what the tool *is* (a read must never wait on a write timeout); tools absent
# here are writes.
TOOL_RATE_CLASSES: Final[dict[str, Literal["read", "write"]]] = {
    "sales_lead_get": "read",
    "sales_lead_list": "read",
    "sales_chatroom_get": "read",
    "sales_consultation_availability": "read",
    "sales_consultation_list": "read",
    "sales_capture_submission_list": "read",
}

# Booking mutations hold a tenant lock and revalidate the calendar, so they get
# the contract's longer `consultation` timeout rather than the plain write one.
CONSULTATION_TIMEOUT_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "sales_consultation_schedule",
        "sales_consultation_confirm",
        "sales_consultation_reschedule",
        "sales_consultation_cancel",
        "sales_consultation_assign",
    }
)

# `ThreadState.sales_actions` keys → Sales tools (D1: ids returned by Sales are
# stored in thread state, never in the bot's own CRM tables).
ACTION_TOOL_MAP: Final[dict[str, str]] = {
    "lead_create": "sales_lead_create",
    "lead_update": "sales_lead_update",
    "lead_lookup": "sales_lead_list",
    "chatroom_open": "sales_chatroom_create",
    "message_post": "sales_message_create",
    "consultation_availability": "sales_consultation_availability",
    "consultation_schedule": "sales_consultation_schedule",
    "consultation_confirm": "sales_consultation_confirm",
    "consultation_reschedule": "sales_consultation_reschedule",
    "consultation_cancel": "sales_consultation_cancel",
    "consultation_list": "sales_consultation_list",
}

# Sales `error.data.code` → how the bot must react. `retryable` calls may be
# retried with the SAME requestId (Sales dedupes on it); `degrade` keeps the turn
# alive on the local path.
CODE_REACTIONS: Final[dict[str, tuple[str, bool, str]]] = {
    "SALES-0301": ("degrade", False, "sales_tenant_missing"),
    "SALES-0330": ("degrade", False, "sales_tool_unavailable"),
    "SALES-0331": ("degrade", False, "sales_no_scope"),
    "SALES-0332": ("degrade", False, "sales_forbidden"),
    "SALES-0403": ("degrade", False, "sales_forbidden"),
    "SALES-0404": ("degrade", False, "sales_not_found"),
    "SALES-0409": ("retry", True, "sales_conflict"),
    "SALES-0422": ("repair", False, "sales_invalid_args"),
    "SALES-0500": ("retry", True, "sales_unavailable"),
}

# Reactions the bot produces locally, keyed the same way so callers switch on
# one vocabulary regardless of whether Sales or the transport failed.
TRANSPORT_REACTION: Final[tuple[str, bool, str]] = ("retry", True, "sales_unavailable")
PERIMETER_REACTION: Final[tuple[str, bool, str]] = ("degrade", False, "sales_forbidden")
CONTRACT_REACTION: Final[tuple[str, bool, str]] = ("degrade", False, "sales_unavailable")


class SalesMcpConfigError(RuntimeError):
    """The integration is switched on but is not safely configured."""


class SalesTenantNotAllowed(SalesMcpConfigError):
    """The site/tenant is outside this bot's allow-list (S3.3.3)."""


@dataclass(frozen=True, slots=True)
class TenantBinding:
    """One allow-listed site → tenant pairing (mirrors ChatbotTenantBinding)."""

    site_id: str
    tenant_id: UUID
    owner_id: str
    chat_mode: Literal["ai", "human"] = "ai"


@dataclass(frozen=True, slots=True)
class SalesToolResult:
    """Outcome of a `tools/call`, degraded rather than raised so a turn survives."""

    ok: bool
    tool: str
    data: dict[str, Any] | None = None
    # Bot-facing code the reply layer can voice (e.g. `sales_invalid_args`).
    code: str | None = None
    # The raw Sales code (`SALES-0422`), kept for logs and support.
    sales_code: str | None = None
    retryable: bool = False
    # True when the caller must not fail the turn: fall back to the local path
    # (D3). Set on every failure — `retryable` only says whether a retry is
    # worth attempting first, and losing a lead is never acceptable.
    degraded: bool = False
    attempts: int = 0

    def as_sales_actions_entry(self) -> dict[str, Any]:
        """Minimal, JSON-safe record for `ThreadState.sales_actions`."""
        return {
            "tool": self.tool,
            "ok": self.ok,
            "code": self.code,
            "sales_code": self.sales_code,
        }


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


@dataclass(slots=True)
class ServiceTokenSigner:
    """Mints the outbound EdDSA service-JWT.

    Only a *handle* to the loaded key is cached (a fingerprint, the key object),
    never the raw secret, so the private key cannot be read back out of the
    process through this object or a repr (S3.3.2).
    """

    # repr=False: the settings object carries the raw key material.
    settings: Settings = field(repr=False)
    _key: Ed25519PrivateKey | None = field(default=None, init=False, repr=False)
    _fingerprint: str | None = field(default=None, init=False, repr=False)

    def _load(self) -> Ed25519PrivateKey:
        material = self.settings.sales_mcp_signing_key
        if not material or not material.strip():
            raise SalesMcpConfigError("SALES_MCP_SIGNING_KEY is not configured")
        fingerprint = hashlib.sha256(material.encode()).hexdigest()[:16]
        if self._key is not None and self._fingerprint == fingerprint:
            return self._key
        key = self._parse(material)
        self._key = key
        self._fingerprint = fingerprint
        return key

    @staticmethod
    def _parse(material: str) -> Ed25519PrivateKey:
        text = material.strip()
        if text.startswith("{"):
            try:
                jwk = json.loads(text)
            except json.JSONDecodeError as exc:  # pragma: no cover - defensive
                raise SalesMcpConfigError("SALES_MCP_SIGNING_KEY is not valid JWK JSON") from exc
            raw = jwk.get("d")
            if not isinstance(raw, str) or not raw:
                raise SalesMcpConfigError("SALES_MCP_SIGNING_KEY JWK has no 'd' private scalar")
            padded = raw + "=" * (-len(raw) % 4)
            return Ed25519PrivateKey.from_private_bytes(base64.urlsafe_b64decode(padded))
        try:
            loaded = serialization.load_pem_private_key(text.encode(), password=None)
        except Exception as exc:
            raise SalesMcpConfigError("SALES_MCP_SIGNING_KEY is neither PEM nor JWK") from exc
        if not isinstance(loaded, Ed25519PrivateKey):
            raise SalesMcpConfigError("SALES_MCP_SIGNING_KEY must be an Ed25519 key")
        return loaded

    def mint(self, tenant_id: UUID | str, *, scopes: str, now: float | None = None) -> str:
        """Return a compact EdDSA JWS for one tenant (S3.3.2)."""
        issued_at = int(now if now is not None else time.time())
        header = {"alg": SERVICE_ALGORITHM, "typ": "JWT", "kid": self.settings.sales_mcp_kid}
        claims = {
            "iss": self.settings.sales_mcp_issuer or SERVICE_ISSUER,
            "aud": self.settings.sales_mcp_audience or SERVICE_AUDIENCE,
            "sub": SERVICE_PRINCIPAL,
            "iat": issued_at,
            "exp": issued_at + TOKEN_TTL_SECONDS,
            "jti": str(uuid4()),
            "tid": str(tenant_id),
            "tenantId": str(tenant_id),
            "scope": scopes,
        }
        signing_input = f"{_b64url(_json_bytes(header))}.{_b64url(_json_bytes(claims))}"
        signature = self._load().sign(signing_input.encode("ascii"))
        return f"{signing_input}.{_b64url(signature)}"


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def pinned_scope(settings: Settings) -> str:
    """The scope claim: the pinned grant set, space-delimited (S3.1.3)."""
    override = (settings.sales_mcp_scope or "").strip()
    if override:
        return override
    return " ".join(sorted(set(TOOL_PERMISSIONS.values())))


def parse_tenant_allowlist(raw: str) -> dict[str, TenantBinding]:
    """Parse `SALES_MCP_TENANT_ALLOWLIST_JSON` into site_id → binding.

    A malformed entry is fatal rather than skipped: silently dropping a tenant
    would silently disable (or mis-route) the tenant filter.
    """
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SalesMcpConfigError("SALES_MCP_TENANT_ALLOWLIST_JSON is not valid JSON") from exc
    if not isinstance(parsed, list):
        raise SalesMcpConfigError("SALES_MCP_TENANT_ALLOWLIST_JSON must be a JSON array")
    bindings: dict[str, TenantBinding] = {}
    for entry in parsed:
        if not isinstance(entry, dict):
            raise SalesMcpConfigError("SALES_MCP_TENANT_ALLOWLIST_JSON entries must be objects")
        try:
            site_id = str(entry["siteId"]).strip()
            tenant_id = UUID(str(entry["tenantId"]))
            owner_id = str(entry["ownerId"]).strip()
        except (KeyError, ValueError) as exc:
            raise SalesMcpConfigError(
                "each allow-list entry needs siteId, a UUID tenantId and ownerId"
            ) from exc
        chat_mode = str(entry.get("chatMode", "ai")).strip().lower()
        if not site_id or not owner_id:
            raise SalesMcpConfigError("each allow-list entry needs siteId, tenantId and ownerId")
        if chat_mode not in {"ai", "human"}:
            raise SalesMcpConfigError("chatMode must be 'ai' or 'human'")
        if site_id in bindings:
            raise SalesMcpConfigError(f"duplicate siteId in allow-list: {site_id}")
        bindings[site_id] = TenantBinding(
            site_id=site_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
            chat_mode=chat_mode,  # type: ignore[arg-type]
        )
    return bindings


@dataclass(slots=True)
class _CircuitBreaker:
    """Stops hammering a Sales deployment that is already failing every call."""

    threshold: int
    cooldown_seconds: float
    failures: int = 0
    opened_at: float | None = None

    def allows(self, now: float) -> bool:
        if self.opened_at is None:
            return True
        if now - self.opened_at >= self.cooldown_seconds:
            # Half-open: let one probe through.
            return True
        return False

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def record_failure(self, now: float) -> None:
        self.failures += 1
        if self.failures >= max(1, self.threshold):
            self.opened_at = now


class SalesMcpClient:
    """JSON-RPC 2.0 `tools/call` client for Sales' `/mcp` endpoint (S3.2.1)."""

    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.AsyncClient | None = None,
        signer: ServiceTokenSigner | None = None,
    ) -> None:
        self.settings = settings
        self._http = http_client
        self._signer = signer or ServiceTokenSigner(settings)
        self._breaker = _CircuitBreaker(
            threshold=settings.sales_mcp_circuit_failure_threshold,
            cooldown_seconds=settings.sales_mcp_circuit_cooldown_seconds,
        )
        self._bindings = parse_tenant_allowlist(settings.sales_mcp_tenant_allowlist_json)

    @property
    def enabled(self) -> bool:
        """Sales is authoritative only when explicitly switched on and configured."""
        return bool(
            self.settings.sales_mcp_enabled
            and self.settings.sales_mcp_url
            and self.settings.sales_mcp_signing_key
        )

    @property
    def bindings(self) -> dict[str, TenantBinding]:
        return self._bindings

    def resolve_site(self, site_id: str) -> TenantBinding:
        """Resolve a site to its pinned tenant, refusing anything not allow-listed."""
        binding = self._bindings.get(str(site_id or "").strip())
        if binding is None:
            raise SalesTenantNotAllowed(f"site {site_id!r} is not in the tenant allow-list")
        return binding

    def assert_tenant_allowed(self, tenant_id: UUID | str | None) -> TenantBinding | None:
        """Reject a tenant outside the allow-list *before* calling Sales (S3.3.3)."""
        wanted = str(tenant_id or "").strip().lower()
        if not wanted:
            return None
        for binding in self._bindings.values():
            if str(binding.tenant_id).lower() == wanted:
                return binding
        raise SalesTenantNotAllowed(f"tenant {tenant_id} is not in the tenant allow-list")

    def _timeout_for(self, tool: str) -> float:
        if tool in CONSULTATION_TIMEOUT_TOOLS:
            return self.settings.sales_mcp_timeout_consultation_seconds
        if TOOL_RATE_CLASSES.get(tool, "write") == "read":
            return self.settings.sales_mcp_timeout_read_seconds
        return self.settings.sales_mcp_timeout_write_seconds

    async def _post(
        self, payload: dict[str, Any], headers: dict[str, str], timeout_seconds: float
    ) -> httpx.Response:
        if self._http is not None:
            return await self._http.post(
                self._endpoint(), json=payload, headers=headers, timeout=timeout_seconds
            )
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            return await client.post(self._endpoint(), json=payload, headers=headers)

    def _endpoint(self) -> str:
        base = (self.settings.sales_mcp_url or "").rstrip("/")
        return base if base.endswith("/mcp") else f"{base}/mcp"

    async def call_tool(
        self,
        tool: str,
        arguments: dict[str, Any] | None = None,
        *,
        tenant_id: UUID | str,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> SalesToolResult:
        """Call one Sales MCP tool, degrading instead of raising.

        `request_id` is the stable per-action id: a retry (by this client or the
        caller) reuses it, so Sales dedupes the second attempt server-side
        (S3.2.4). It doubles as the JSON-RPC `id`, which is what makes the
        transport retry provably the same call rather than a new one.
        """
        if tool not in TOOL_PERMISSIONS:
            return SalesToolResult(
                ok=False,
                tool=tool,
                code="sales_tool_not_allowed",
                degraded=True,
                attempts=0,
            )
        if not self.enabled:
            return SalesToolResult(
                ok=False, tool=tool, code="sales_integration_disabled", degraded=True, attempts=0
            )
        try:
            binding = self.assert_tenant_allowed(tenant_id)
        except SalesTenantNotAllowed:
            _logger.warning("sales_mcp_tenant_rejected", tool=tool, tenant_id=str(tenant_id))
            return SalesToolResult(
                ok=False, tool=tool, code="sales_tenant_not_allowed", degraded=True, attempts=0
            )
        if binding is None:
            # No tenant claim on the turn: Sales would reject the call anyway,
            # so stay on the local path instead of making it (D1).
            return SalesToolResult(
                ok=False, tool=tool, code="sales_tenant_missing", degraded=True, attempts=0
            )

        call_id = request_id or str(uuid4())
        payload = {
            "jsonrpc": "2.0",
            "id": call_id,
            "method": "tools/call",
            "params": {"name": tool, "arguments": dict(arguments or {})},
        }
        headers = {
            HEADER_INTERNAL: "true",
            # The token's tenant comes from the allow-list binding, never from
            # request input (S3.3.1/S3.3.3).
            HEADER_SERVICE_AUTHORIZATION: self._signer.mint(
                binding.tenant_id, scopes=pinned_scope(self.settings)
            ),
            "content-type": "application/json",
        }
        if correlation_id:
            headers[HEADER_CORRELATION] = correlation_id

        timeout = self._timeout_for(tool)
        attempts = max(1, self.settings.sales_mcp_max_attempts)
        last: SalesToolResult | None = None
        for attempt in range(1, attempts + 1):
            now = time.monotonic()
            if not self._breaker.allows(now):
                return SalesToolResult(
                    ok=False,
                    tool=tool,
                    code="sales_unavailable",
                    retryable=True,
                    degraded=True,
                    attempts=attempt - 1,
                )
            try:
                response = await self._post(payload, headers, timeout_seconds=timeout)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                self._breaker.record_failure(now)
                _logger.warning(
                    "sales_mcp_transport_error",
                    tool=tool,
                    attempt=attempt,
                    request_id=call_id,
                    error=exc.__class__.__name__,
                )
                last = _failure(tool, TRANSPORT_REACTION, attempts=attempt)
                continue
            result = self._interpret(tool, response, attempts=attempt)
            if result.ok or not result.retryable:
                if result.ok:
                    self._breaker.record_success()
                else:
                    self._breaker.record_failure(now)
                if not result.ok:
                    _logger.warning(
                        "sales_mcp_call_failed",
                        tool=tool,
                        sales_code=result.sales_code,
                        code=result.code,
                        request_id=call_id,
                        attempt=attempt,
                    )
                return result
            self._breaker.record_failure(now)
            last = result
        assert last is not None  # the loop above always assigns on its last path
        return last

    def _interpret(self, tool: str, response: httpx.Response, *, attempts: int) -> SalesToolResult:
        if response.status_code in {408, 429} or response.status_code >= 500:
            return _failure(tool, TRANSPORT_REACTION, attempts=attempts)
        if response.status_code == 401 or response.status_code == 403:
            return _failure(tool, PERIMETER_REACTION, attempts=attempts)
        try:
            body = response.json()
        except ValueError:
            return _failure(tool, CONTRACT_REACTION, attempts=attempts)
        if not isinstance(body, dict):
            return _failure(tool, CONTRACT_REACTION, attempts=attempts)

        error = body.get("error")
        if isinstance(error, dict):
            data = error.get("data") if isinstance(error.get("data"), dict) else {}
            sales_code = str(data.get("code") or "") or None
            reaction = CODE_REACTIONS.get(sales_code or "", CONTRACT_REACTION)
            failure = _failure(tool, reaction, attempts=attempts)
            # Keep the wire id alongside the mapped code: it is what Sales'
            # idempotency record is keyed on when the caller retries.
            return SalesToolResult(
                ok=False,
                tool=tool,
                code=failure.code,
                sales_code=sales_code,
                retryable=failure.retryable,
                degraded=failure.degraded,
                attempts=attempts,
            )

        result = body.get("result")
        if not isinstance(result, dict):
            return _failure(tool, CONTRACT_REACTION, attempts=attempts)
        return SalesToolResult(
            ok=True,
            tool=tool,
            data=unwrap_tool_result(result),
            attempts=attempts,
        )


def _failure(tool: str, reaction: tuple[str, bool, str], *, attempts: int) -> SalesToolResult:
    _action, retryable, code = reaction
    return SalesToolResult(
        ok=False,
        tool=tool,
        code=code,
        retryable=retryable,
        degraded=True,
        attempts=attempts,
    )


def unwrap_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize an MCP tool result into a plain dict (S3.2.3).

    Sales returns `{content:[{type:"text",text:"<json>"}]}`. The JSON payload is
    preferred; anything else is preserved under ``text`` so a caller never has to
    guess which shape it got.
    """
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    blocks = result.get("content")
    if isinstance(blocks, list):
        parsed: dict[str, Any] = {}
        text_parts: list[str] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if not isinstance(text, str):
                continue
            try:
                candidate = json.loads(text)
            except json.JSONDecodeError:
                text_parts.append(text)
                continue
            if isinstance(candidate, dict):
                parsed.update(candidate)
            else:
                parsed.setdefault("items", candidate)
        if parsed:
            return parsed
        if text_parts:
            return {"text": "\n".join(text_parts)}
    return {"raw": result}


def tool_for_action(action: str) -> str | None:
    """Map a dispatcher action to its Sales tool, or None when unmapped."""
    return ACTION_TOOL_MAP.get(str(action or "").strip())


async def call_action(
    client: SalesMcpClient,
    action: str,
    arguments: dict[str, Any] | None = None,
    *,
    tenant_id: UUID | str,
    request_id: str | None,
    correlation_id: str | None = None,
) -> SalesToolResult:
    """Dispatch one bot action through Sales, degrading on an unmapped action."""
    tool = tool_for_action(action)
    if tool is None:
        return SalesToolResult(
            ok=False, tool=f"action:{action}", code="sales_action_unmapped", degraded=True
        )
    return await client.call_tool(
        tool,
        arguments,
        tenant_id=tenant_id,
        request_id=request_id,
        correlation_id=correlation_id,
    )


__all__ = [
    "ACTION_TOOL_MAP",
    "CODE_REACTIONS",
    "CONSULTATION_TIMEOUT_TOOLS",
    "CONTRACT_VERSION",
    "MCP_PROTOCOL_VERSION",
    "SalesMcpClient",
    "SalesMcpConfigError",
    "SalesTenantNotAllowed",
    "SalesToolResult",
    "ServiceTokenSigner",
    "TenantBinding",
    "call_action",
    "parse_tenant_allowlist",
    "pinned_scope",
    "tool_for_action",
    "unwrap_tool_result",
]
