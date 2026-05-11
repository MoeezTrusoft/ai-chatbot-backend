from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException, Request, WebSocket, status

from bookcraft.infra.config import Settings


class AuthError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AuthPrincipal:
    subject: str
    customer_id: UUID | None = None
    scopes: frozenset[str] = frozenset()


def authenticate_request(request: Request, settings: Settings) -> AuthPrincipal:
    if settings.api_auth_mode == "off":
        return AuthPrincipal(subject="anonymous")

    token = _bearer_token_from_authorization(request.headers.get("authorization"))
    return verify_jwt_token(token, settings)


def authenticate_websocket(websocket: WebSocket, settings: Settings) -> AuthPrincipal:
    if settings.api_auth_mode == "off":
        return AuthPrincipal(subject="anonymous")

    token = _bearer_token_from_authorization(websocket.headers.get("authorization"))
    if token is None:
        # Browser WebSocket clients cannot reliably send custom Authorization headers.
        # Allow query-token auth only for WebSocket handshakes.
        token = websocket.query_params.get("access_token")

    return verify_jwt_token(token, settings)


def require_http_auth(request: Request, settings: Settings) -> AuthPrincipal:
    try:
        return authenticate_request(request, settings)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "unauthorized"},
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def verify_jwt_token(token: str | None, settings: Settings) -> AuthPrincipal:
    if not token:
        raise AuthError("missing bearer token")

    if not settings.jwt_signing_key:
        raise AuthError("jwt signing key is not configured")

    header, payload, signature = _decode_compact_jwt(token)
    algorithm = header.get("alg")
    if algorithm != "HS256":
        raise AuthError("unsupported jwt algorithm")

    expected = _sign(f"{payload.raw_header}.{payload.raw_payload}", settings.jwt_signing_key)
    if not hmac.compare_digest(signature, expected):
        raise AuthError("invalid jwt signature")

    now = int(time.time())
    exp = payload.claims.get("exp")
    if isinstance(exp, int | float) and now >= int(exp):
        raise AuthError("jwt expired")

    nbf = payload.claims.get("nbf")
    if isinstance(nbf, int | float) and now < int(nbf):
        raise AuthError("jwt not active")

    subject = payload.claims.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise AuthError("jwt subject is required")

    customer_id = _customer_id_from_claim(payload.claims.get("customer_id"))
    scopes = _scopes_from_claim(payload.claims.get("scope") or payload.claims.get("scopes"))

    return AuthPrincipal(
        subject=subject,
        customer_id=customer_id,
        scopes=frozenset(scopes),
    )


@dataclass(frozen=True, slots=True)
class _DecodedPayload:
    raw_header: str
    raw_payload: str
    claims: dict[str, Any]


def _decode_compact_jwt(token: str) -> tuple[dict[str, Any], _DecodedPayload, str]:
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthError("jwt must have three compact parts")

    raw_header, raw_payload, signature = parts
    header = _decode_json(raw_header)
    claims = _decode_json(raw_payload)

    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise AuthError("jwt header and payload must be objects")

    return (
        header,
        _DecodedPayload(
            raw_header=raw_header,
            raw_payload=raw_payload,
            claims=claims,
        ),
        signature,
    )


def _decode_json(value: str) -> Any:
    try:
        return json.loads(_b64url_decode(value))
    except (json.JSONDecodeError, ValueError) as exc:
        raise AuthError("invalid jwt json") from exc


def _b64url_decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except ValueError as exc:
        raise AuthError("invalid base64url value") from exc


def _sign(signing_input: str, key: str) -> str:
    digest = hmac.new(
        key.encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return _b64url_encode(digest)


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _bearer_token_from_authorization(value: str | None) -> str | None:
    if not value:
        return None

    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None

    return token.strip()


def _customer_id_from_claim(value: Any) -> UUID | None:
    if value is None:
        return None

    if not isinstance(value, str):
        raise AuthError("customer_id claim must be a string")

    try:
        return UUID(value)
    except ValueError as exc:
        raise AuthError("customer_id claim must be a UUID") from exc


def _scopes_from_claim(value: Any) -> set[str]:
    if value is None:
        return set()

    if isinstance(value, str):
        return {item for item in value.split() if item}

    if isinstance(value, list):
        scopes: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                raise AuthError("scope list values must be strings")
            if item:
                scopes.add(item)
        return scopes

    raise AuthError("scope claim must be string or list")
