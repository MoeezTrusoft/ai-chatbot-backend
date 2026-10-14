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
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
import phonenumbers
import structlog
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bookcraft.domain.enums import SalesStage
from bookcraft.infra.config import Settings
from bookcraft.infra.sales_mcp_metrics import observe_sales_mcp_result

_logger = structlog.get_logger(__name__)

# ── Frozen wire constants (mirror of contracts/src/mcp/sales-chatbot.js) ─────
CONTRACT_VERSION: Final = "sales-chatbot-mcp@2"
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
    "sales_consultation_config_get": "sales.config.read",
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
    "sales_consultation_config_get": "read",
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
LEAD_SOURCE_CHANNEL: Final = "Chatbot"

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
class SalesTurnContext:
    """The verified tenant/site of the turn being served (S3.3.1).

    Filled from the Sales-signed inbound token claim, never from request input:
    a caller cannot choose which tenant its conversation is written into.
    """

    tenant_id: UUID | None = None
    site_id: str | None = None
    thread_id: UUID | None = None
    # The Sales lead id this conversation already created (S7.1.1). Sales books a
    # consultation against a LEAD it owns, so its own id — not the bot's local
    # one — is the only handle `sales_consultation_schedule` will accept.
    lead_id: str | None = None
    # Where the visitor came from (S6.3.1): the page and keyword Sales sent on the
    # turn. They become the created lead's attribution instead of being dropped
    # once the conversation starts.
    landing_page: str | None = None
    landing_keyword: str | None = None

    @property
    def usable(self) -> bool:
        return bool(self.tenant_id or self.site_id)


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


def lead_source(site_id: str) -> str:
    """`Chatbot:<siteId>` provenance, validated the way the contract validates it."""
    site = str(site_id or "").strip()
    if not re.fullmatch(r"[a-z0-9_-]{3,80}", site, flags=re.IGNORECASE):
        raise SalesMcpConfigError(f"cannot build a lead source from site id {site_id!r}")
    return f"{LEAD_SOURCE_CHANNEL}:{site}"


# The chatbot's funnel stages, as Sales receives them. Sales owns the frozen
# translation to a `LeadStage` (`SALES_STAGE_MAP`), so the bot sends its OWN
# vocabulary and never has to mirror Sales' enum (S6.2.1).
CHATBOT_SALES_STAGES: Final = frozenset(member.value for member in SalesStage)


def lead_stage_value(stage: str) -> str:
    """The stage value to put on the wire, rejected if it is not one the bot has.

    An unmapped stage would be a hard failure inside Sales' `mapSalesStage`, so it
    is caught here as a configuration error and the caller degrades locally
    instead of shipping a lead the bot cannot describe.
    """
    value = str(stage).strip().lower()
    if value not in CHATBOT_SALES_STAGES:
        raise SalesMcpConfigError(f"cannot send unknown chatbot stage {stage!r} to Sales")
    return value


def landing_slug(landing_page: str | None) -> str | None:
    """Last meaningful path segment of a landing page (S6.3.1).

    `https://x.com/book-cover-design/?utm_source=g` and `/book-cover-design/`
    both resolve to `book-cover-design`, which is a niche Sales can report on;
    a query string or a trailing slash is not.
    """
    text = (landing_page or "").strip()
    if not text:
        return None
    path = urlsplit(text).path if "//" in text else text
    segments = [segment for segment in path.split("/") if segment.strip()]
    if not segments:
        return None
    return segments[-1].strip().lower()[:120] or None


def lead_attribution(
    *, landing_page: str | None = None, landing_keyword: str | None = None
) -> dict[str, str]:
    """Attribution arguments for `sales_lead_create` (S6.3.1).

    Only fields the conversation actually knows: a missing keyword never invents
    one, so a lead with no landing context keeps the exact argument shape it had
    before this existed.
    """
    attribution: dict[str, str] = {}
    slug = landing_slug(landing_page)
    keyword = (landing_keyword or "").strip()[:200]
    if slug:
        attribution["lpNiche"] = slug
    if keyword:
        attribution["adSource"] = keyword
    return attribution


def lead_create_arguments(
    slots: Mapping[str, Any],
    *,
    binding: TenantBinding,
    customer_id: UUID | None = None,
    request_id: str | None = None,
    stage: str | None = None,
    landing_page: str | None = None,
    landing_keyword: str | None = None,
) -> dict[str, Any]:
    """Map dispatcher slots onto `sales_lead_create` (S3.2.2).

    `source` and `ownerId` come from the allow-list binding rather than the chat
    payload: Sales' routing owner is a CRM decision, not something a visitor can
    assert by typing it. `stage` carries the thread's own funnel position, so a
    chat lead is not silently filed at Sales' default stage (S6.2.1).
    """
    arguments: dict[str, Any] = {
        "source": lead_source(binding.site_id),
        "ownerId": binding.owner_id,
    }
    for slot, field_name in (("name", "name"), ("email", "email"), ("phone", "phone")):
        value = slots.get(slot)
        if isinstance(value, str) and value.strip():
            arguments[field_name] = value.strip()[:255]
    if customer_id is not None:
        arguments["customerId"] = str(customer_id)
    if request_id:
        arguments["requestId"] = request_id
    if stage:
        arguments["stage"] = lead_stage_value(stage)
    # The page/keyword the visitor arrived on travels with the lead, so
    # attribution is not lost the moment the chat starts (S6.3.1).
    arguments.update(
        lead_attribution(landing_page=landing_page, landing_keyword=landing_keyword)
    )
    return arguments


def lead_payload_from_sales(
    data: Mapping[str, Any], *, contact: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Shape a Sales lead record into the `payload["lead"]` the state writer reads.

    Sales keeps the person on `customer` and the pipeline fields on the lead, so the
    contact details the conversation collected are used when Sales has none yet.
    """
    raw_customer = data.get("customer")
    customer: Mapping[str, Any] = raw_customer if isinstance(raw_customer, Mapping) else {}
    collected = contact or {}

    def contact_field(key: str) -> str | None:
        for source in (customer, collected):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    return {
        "id": str(data.get("id") or ""),
        "name": contact_field("name"),
        "email": contact_field("email"),
        "phone": contact_field("phone"),
        "customer_id": data.get("customerId"),
        "stage": data.get("stage"),
        "score": data.get("score"),
        "owner_id": data.get("ownerId"),
        "source": data.get("source"),
        "origin": "sales",
    }


def normalize_e164(phone: str | None, *, default_region: str | None = None) -> str | None:
    """Best-effort E.164 for the number a visitor typed (S7.2.1).

    Sales' scheduling requires the lead's customer to carry a **E.164** phone, so
    any other shape is a booking that gets refused after the visitor has already
    agreed to a time. Returns ``None`` when the number cannot be interpreted at
    all, and the caller then reports a contact problem instead of sending Sales
    something it will reject.
    """
    raw = (phone or "").strip()
    if not raw:
        return None
    try:
        parsed = phonenumbers.parse(raw, default_region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def consultation_request_id(idempotency_key: str) -> str:
    """A stable UUID for `sales_consultation_schedule`'s required `requestId`.

    Sales types that field as a UUID, and the bot's durable idempotency key is a
    hex digest, so the two are bridged deterministically: a retried booking
    reuses the same UUID and Sales dedupes it instead of booking twice.
    """
    return str(uuid5(NAMESPACE_URL, f"sales-consultation-schedule:{idempotency_key}"))


def consultation_availability_arguments(
    *, lead_id: str, start_time: str, customer_timezone: str, preferred_owner_id: str | None = None
) -> dict[str, Any]:
    """Arguments for `sales_consultation_availability`.

    That tool's schema is strict and has no `requestId` field, so the idempotency
    key is deliberately left to the JSON-RPC envelope here.
    """
    arguments: dict[str, Any] = {
        "leadId": str(lead_id),
        "startTime": str(start_time),
        "userTimezone": str(customer_timezone),
        "limit": 1,
    }
    if preferred_owner_id:
        arguments["preferredOwnerId"] = str(preferred_owner_id)
    return arguments


def consultation_schedule_arguments(
    *,
    lead_id: str,
    start_time: str,
    customer_timezone: str,
    request_id: str,
    preferred_owner_id: str | None = None,
) -> dict[str, Any]:
    """Arguments for `sales_consultation_schedule` (S3.2.2).

    `startTime` must be an instant Sales actually offers: availability is asked
    first, and whatever it returns is what gets booked.
    """
    arguments: dict[str, Any] = {
        "leadId": str(lead_id),
        "startTime": str(start_time),
        "userTimezone": str(customer_timezone),
        "requestId": str(request_id),
    }
    if preferred_owner_id:
        arguments["preferredOwnerId"] = str(preferred_owner_id)
    return arguments


def consultation_confirm_arguments(
    *, consultation_id: str, expected_version: int, request_id: str
) -> dict[str, Any]:
    """Arguments for `sales_consultation_confirm` (S7.1.2).

    Accepting a persisted offer needs the exact row and its version: Sales re-checks
    availability under an optimistic lock, so a stale version is refused rather than
    silently double-booking the slot.
    """
    return {
        "consultationId": str(consultation_id),
        "expectedVersion": int(expected_version),
        "requestId": str(request_id),
    }


def consultation_payload_from_sales(data: Mapping[str, Any]) -> dict[str, Any]:
    """Shape a Sales consultation record into the payload the state layer reads.

    A `Proposed` result is an expiring offer, not a booking — the caller has to
    confirm it separately — so the status is carried through verbatim and never
    flattened into "booked" here.
    """
    return {
        "id": str(data.get("id") or ""),
        "status": data.get("status"),
        "owner_id": data.get("ownerId"),
        "lead_id": data.get("leadId"),
        "customer_id": data.get("customerId"),
        "start_time": data.get("startTime"),
        "end_time": data.get("endTime"),
        "timezone": data.get("timezone"),
        "user_timezone": data.get("userTimezone"),
        "version": data.get("version"),
        "offer_expires_at": data.get("offerExpiresAt"),
        "origin": "sales",
    }


def consultation_slot_from_sales(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The `slots` array `sales_consultation_availability` returns."""
    raw = data.get("slots")
    if not isinstance(raw, list):
        return []
    return [dict(slot) for slot in raw if isinstance(slot, Mapping)]


# An ISO `YYYY-MM-DD` day, the shape Sales' policy carries in `blackoutDates`.
_ISO_DATE_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def consultation_config_arguments() -> dict[str, Any]:
    """Arguments for `sales_consultation_config_get` (S7.2.2).

    The policy is scoped by the verified tenant claim, so the tool takes no
    input — the bot never picks which tenant's calendar it reads.
    """
    return {}


def consultation_policy_from_sales(data: Mapping[str, Any]) -> dict[str, Any]:
    """The tenant consultation policy returned by `sales_consultation_config_get`.

    Sales is the single owner of the consultation calendar (S7.2.2/S7.3.1), so the
    bot reads the business window, timezone and holiday blackout dates from here
    instead of enforcing its own 10–19 America/Chicago assumptions. A malformed or
    absent payload yields `{}` rather than raising: the caller then keeps its
    local defaults, and a booking is never blocked by a policy read.
    """
    policy = data.get("policy") if isinstance(data, Mapping) else None
    if not isinstance(policy, Mapping):
        return {}
    blackout: list[str] = []
    raw_blackout = policy.get("blackoutDates")
    if isinstance(raw_blackout, list):
        blackout = [day for day in raw_blackout if isinstance(day, str) and _ISO_DATE_RE.match(day)]
    weekdays: list[int] = []
    raw_weekdays = policy.get("weekdays")
    if isinstance(raw_weekdays, list):
        weekdays = [day for day in raw_weekdays if isinstance(day, int) and 0 <= day <= 6]
    timezone = policy.get("timezone")
    return {
        "enabled": bool(policy.get("enabled")),
        "timezone": str(timezone) if timezone else None,
        "open_minute": _optional_int(policy.get("openMinute")),
        "close_minute": _optional_int(policy.get("closeMinute")),
        "weekdays": weekdays,
        "max_days_ahead": _optional_int(policy.get("maxDaysAhead")),
        "min_notice_minutes": _optional_int(policy.get("minNoticeMinutes")),
        "blackout_dates": blackout,
    }


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


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
    def local_scheduler(self) -> bool:
        """`CHATBOT_LOCAL_SCHEDULER=true`: the bot books in-process (D3/T3.4).

        Standalone and dev deployments have no Sales calendar to book against, so
        the in-process dispatcher stays authoritative for them; production turns
        this off and Sales owns the booking.
        """
        return bool(self.settings.chatbot_local_scheduler)

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

    def resolve_binding(
        self, *, tenant_id: UUID | str | None = None, site_id: str | None = None
    ) -> TenantBinding:
        """Pick the binding for a turn, refusing a site/tenant pair that disagrees.

        A token that names a site resolves through the site; a token that names only
        a tenant resolves by tenant. Both at once must agree, so a token cannot pair
        one tenant's id with another tenant's site and write into either.
        """
        site = str(site_id or "").strip()
        if site:
            binding = self._bindings.get(site)
            if binding is None:
                raise SalesTenantNotAllowed(f"site {site!r} is not in the tenant allow-list")
            claimed = str(tenant_id or "").strip().lower()
            if claimed and claimed != str(binding.tenant_id).lower():
                raise SalesTenantNotAllowed(
                    f"site {site!r} does not belong to the claimed tenant {tenant_id}"
                )
            return binding
        binding = self.assert_tenant_allowed(tenant_id)
        if binding is None:
            raise SalesTenantNotAllowed("the turn carries no tenant or site claim")
        return binding

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
        tenant_id: UUID | str | None,
        site_id: str | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> SalesToolResult:
        """Call one Sales MCP tool and record its outcome (S9.2.2).

        The result is exactly what `_call_tool` produced — this wrapper only
        counts it, so no caller can observe a different value.
        """
        result = await self._call_tool(
            tool,
            arguments,
            tenant_id=tenant_id,
            site_id=site_id,
            request_id=request_id,
            correlation_id=correlation_id,
        )
        observe_sales_mcp_result(tool, result)
        return result

    async def _call_tool(
        self,
        tool: str,
        arguments: dict[str, Any] | None = None,
        *,
        tenant_id: UUID | str | None,
        site_id: str | None = None,
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
            binding = self.resolve_binding(tenant_id=tenant_id, site_id=site_id)
        except SalesTenantNotAllowed as exc:
            _logger.warning(
                "sales_mcp_tenant_rejected",
                tool=tool,
                tenant_id=str(tenant_id),
                site_id=site_id,
                detail=str(exc),
            )
            code = (
                "sales_tenant_missing"
                if not str(tenant_id or "").strip() and not str(site_id or "").strip()
                else "sales_tenant_not_allowed"
            )
            return SalesToolResult(ok=False, tool=tool, code=code, degraded=True, attempts=0)

        call_id = request_id or str(uuid4())
        payload = {
            "jsonrpc": "2.0",
            "id": call_id,
            "method": "tools/call",
            "params": {"name": tool, "arguments": dict(arguments or {})},
        }
        base_headers = {
            HEADER_INTERNAL: "true",
            "content-type": "application/json",
        }
        if correlation_id:
            base_headers[HEADER_CORRELATION] = correlation_id

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
            # One token per attempt: Sales refuses a `jti` it has already
            # accepted (S10.3.2), so retrying with the previous attempt's token
            # would turn a legitimate retry into a 401. The stable `request_id`
            # — not the token — is what makes the retry the same call (S3.2.4).
            headers = {
                **base_headers,
                # The token's tenant comes from the allow-list binding, never
                # from request input (S3.3.1/S3.3.3).
                HEADER_SERVICE_AUTHORIZATION: self._signer.mint(
                    binding.tenant_id, scopes=pinned_scope(self.settings)
                ),
            }
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
            raw_data = error.get("data")
            data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
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
    "SalesTurnContext",
    "ServiceTokenSigner",
    "TenantBinding",
    "call_action",
    "consultation_availability_arguments",
    "consultation_config_arguments",
    "consultation_policy_from_sales",
    "consultation_payload_from_sales",
    "consultation_request_id",
    "consultation_schedule_arguments",
    "consultation_slot_from_sales",
    "lead_create_arguments",
    "lead_payload_from_sales",
    "lead_source",
    "parse_tenant_allowlist",
    "pinned_scope",
    "tool_for_action",
    "unwrap_tool_result",
]
