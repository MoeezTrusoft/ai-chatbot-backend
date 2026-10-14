"""Verification of inbound EdDSA service-JWTs (S3.6.3).

The counterpart of :mod:`bookcraft.infra.sales_mcp`, which *signs* the bot's
outbound calls. A caller reaching the bot's own MCP server presents the same
kind of token (Ed25519 `kid` in a public-key registry, issuer allow-listed,
``aud=internal``), so one deployment can hold both halves of the bridge.

Failures are returned as a reason rather than raised-everywhere: the caller
decides the HTTP status, and every path fails closed.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_logger = structlog.get_logger(__name__)

ALLOWED_ALGORITHMS: frozenset[str] = frozenset({"EdDSA"})


class ServiceTokenError(RuntimeError):
    """The token is absent, malformed, or not trustworthy. Never carries the token."""


@dataclass(frozen=True, slots=True)
class ServicePrincipal:
    """The verified caller behind a service token."""

    subject: str
    issuer: str
    scopes: frozenset[str]
    tenant_id: UUID | None = None
    email: str | None = None
    claims: Mapping[str, Any] | None = None


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _b64url_decode_json(value: str) -> Any:
    try:
        return json.loads(_b64url_decode(value))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ServiceTokenError("token segment is not base64url JSON") from exc


def build_public_key_registry(raw: str | None) -> dict[str, Ed25519PublicKey]:
    """Parse the `kid` → public-key registry (JSON: kid → JWK or PEM).

    A malformed entry is skipped rather than fatal *here*: verification then
    fails closed with `unknown-kid`, which is the same outcome as an unregistered
    caller and cannot be mistaken for a working configuration.
    """
    registry: dict[str, Ed25519PublicKey] = {}
    text = (raw or "").strip()
    if not text:
        return registry
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        _logger.warning("service_token_key_registry_not_json")
        return registry
    if not isinstance(parsed, Mapping):
        _logger.warning("service_token_key_registry_not_an_object")
        return registry
    for kid, value in parsed.items():
        key = _to_public_key(value)
        if key is not None:
            registry[str(kid)] = key
    return registry


def _to_public_key(value: object) -> Ed25519PublicKey | None:
    try:
        if isinstance(value, str) and value.strip().startswith("-----"):
            loaded = serialization.load_pem_public_key(value.encode())
        elif isinstance(value, Mapping):
            loaded = Ed25519PublicKey.from_public_bytes(
                _b64url_decode(str(value.get("x", "")))
            )
        else:
            return None
    except (ValueError, TypeError, InvalidSignature):
        return None
    return loaded if isinstance(loaded, Ed25519PublicKey) else None


def parse_issuer_allowlist(raw: str | None) -> frozenset[str]:
    """Comma-separated issuers; blank falls back to the pinned default set."""
    if raw is None or not raw.strip():
        return DEFAULT_ISSUERS
    issuers = frozenset(part.strip() for part in raw.split(",") if part.strip())
    return issuers or DEFAULT_ISSUERS


# Callers that hold an internal key today (mirrors CHATBOT_MCP_ISSUERS in
# erp-contracts/src/mcp/chatbot.js).
DEFAULT_ISSUERS: frozenset[str] = frozenset(
    {"erp-gateway", "erp-rbac", "erp-sales-backend"}
)


def verify_service_token(
    token: str | None,
    *,
    registry: Mapping[str, Ed25519PublicKey],
    issuers: frozenset[str],
    audience: str,
    now: float | None = None,
) -> ServicePrincipal:
    """Verify a compact EdDSA JWS. Raises :class:`ServiceTokenError` on any doubt."""
    if not token or not isinstance(token, str):
        raise ServiceTokenError("missing-token")
    parts = token.split(".")
    if len(parts) != 3:
        raise ServiceTokenError("malformed-token")
    header_b64, payload_b64, signature_b64 = parts

    header = _b64url_decode_json(header_b64)
    if not isinstance(header, Mapping):
        raise ServiceTokenError("malformed-header")
    # Pinned: an unlisted algorithm must never reach the key, and there is no
    # shared-secret fallback for an internal caller to downgrade to.
    if header.get("alg") not in ALLOWED_ALGORITHMS:
        raise ServiceTokenError("unsupported-algorithm")
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise ServiceTokenError("no-kid")
    key = registry.get(kid)
    if key is None:
        raise ServiceTokenError("unknown-kid")

    try:
        key.verify(_b64url_decode(signature_b64), f"{header_b64}.{payload_b64}".encode("ascii"))
    except (InvalidSignature, ValueError) as exc:
        raise ServiceTokenError("bad-signature") from exc

    claims = _b64url_decode_json(payload_b64)
    if not isinstance(claims, Mapping):
        raise ServiceTokenError("malformed-claims")

    issuer = str(claims.get("iss") or "")
    if issuer not in issuers:
        raise ServiceTokenError("untrusted-issuer")
    if not _audience_matches(claims.get("aud"), audience):
        raise ServiceTokenError("wrong-audience")

    moment = time.time() if now is None else now
    expires_at = _numeric(claims.get("exp"))
    if expires_at is None:
        raise ServiceTokenError("no-expiry")
    if expires_at <= moment:
        raise ServiceTokenError("expired")
    not_before = _numeric(claims.get("nbf"))
    if not_before is not None and not_before > moment:
        raise ServiceTokenError("not-yet-valid")

    return ServicePrincipal(
        subject=str(claims.get("sub") or claims.get("svc") or issuer),
        issuer=issuer,
        scopes=frozenset(str(claims.get("scope") or "").split()),
        tenant_id=_tenant(claims),
        email=_optional_string(claims.get("email")),
        claims=claims,
    )


def _numeric(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _optional_string(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _audience_matches(value: object, expected: str) -> bool:
    if isinstance(value, str):
        return value == expected
    if isinstance(value, list):
        return expected in [str(item) for item in value]
    return False


def _tenant(claims: Mapping[str, Any]) -> UUID | None:
    for key in ("tenantId", "tid", "tenant_id"):
        raw = claims.get(key)
        if raw is None:
            continue
        try:
            return UUID(str(raw))
        except ValueError as exc:
            # A malformed tenant must not degrade into "no tenant": that would
            # silently widen the caller to every tenant.
            raise ServiceTokenError("malformed-tenant-claim") from exc
    return None
