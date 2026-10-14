"""Inbound EdDSA service-token verification (S3.6.3)."""

import base64
import json
import time
from uuid import UUID

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bookcraft.infra.service_tokens import (
    DEFAULT_ISSUERS,
    ServiceTokenError,
    build_public_key_registry,
    parse_issuer_allowlist,
    verify_service_token,
)

KID = "sales-ed25519"
TENANT_ID = "11111111-1111-4111-8111-111111111111"


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def keypair() -> tuple[Ed25519PrivateKey, dict[str, str]]:
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return key, {"kty": "OKP", "crv": "Ed25519", "x": b64(public)}


def registry_from(jwk: object) -> dict:
    return build_public_key_registry(json.dumps({KID: jwk}))


def sign(key: Ed25519PrivateKey, header: dict, claims: dict) -> str:
    signing_input = f"{b64(json.dumps(header).encode())}.{b64(json.dumps(claims).encode())}"
    return f"{signing_input}.{b64(key.sign(signing_input.encode()))}"


def valid_claims(**overrides: object) -> dict:
    claims = {
        "iss": "erp-sales-backend",
        "aud": "internal",
        "sub": "svc:erp-sales-backend",
        "scope": "chat.conversation.write chat.state.read",
        "tenantId": TENANT_ID,
        "exp": int(time.time()) + 300,
    }
    claims.update(overrides)
    return claims


def test_a_valid_token_yields_its_principal() -> None:
    key, jwk = keypair()
    token = sign(key, {"alg": "EdDSA", "typ": "JWT", "kid": KID}, valid_claims())

    principal = verify_service_token(
        token, registry=registry_from(jwk), issuers=DEFAULT_ISSUERS, audience="internal"
    )

    assert principal.subject == "svc:erp-sales-backend"
    assert principal.issuer == "erp-sales-backend"
    assert principal.scopes == {"chat.conversation.write", "chat.state.read"}
    assert principal.tenant_id == UUID(TENANT_ID)


def test_a_pem_public_key_is_accepted_in_the_registry() -> None:
    key = Ed25519PrivateKey.generate()
    pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    token = sign(key, {"alg": "EdDSA", "kid": KID}, valid_claims())

    principal = verify_service_token(
        token,
        registry=build_public_key_registry(json.dumps({KID: pem})),
        issuers=DEFAULT_ISSUERS,
        audience="internal",
    )
    assert principal.issuer == "erp-sales-backend"


@pytest.mark.parametrize(
    ("header", "claims", "reason"),
    [
        ({"alg": "HS256", "kid": KID}, {}, "unsupported-algorithm"),
        ({"alg": "EdDSA"}, {}, "no-kid"),
        ({"alg": "EdDSA", "kid": "other"}, {}, "unknown-kid"),
        ({"alg": "EdDSA", "kid": KID}, {"iss": "not-a-tier"}, "untrusted-issuer"),
        ({"alg": "EdDSA", "kid": KID}, {"aud": "elsewhere"}, "wrong-audience"),
        ({"alg": "EdDSA", "kid": KID}, {"exp": int(time.time()) - 1}, "expired"),
        ({"alg": "EdDSA", "kid": KID}, {"exp": None}, "no-expiry"),
        ({"alg": "EdDSA", "kid": KID}, {"nbf": int(time.time()) + 600}, "not-yet-valid"),
        ({"alg": "EdDSA", "kid": KID}, {"tenantId": "not-a-uuid"}, "malformed-tenant-claim"),
    ],
)
def test_every_rejection_fails_closed(header: dict, claims: dict, reason: str) -> None:
    key, jwk = keypair()
    token = sign(key, header, valid_claims(**claims))

    with pytest.raises(ServiceTokenError) as exc:
        verify_service_token(
            token, registry=registry_from(jwk), issuers=DEFAULT_ISSUERS, audience="internal"
        )
    assert str(exc.value) == reason


def test_a_signature_from_another_key_is_rejected() -> None:
    _, jwk = keypair()
    other = Ed25519PrivateKey.generate()
    token = sign(other, {"alg": "EdDSA", "kid": KID}, valid_claims())

    with pytest.raises(ServiceTokenError) as exc:
        verify_service_token(
            token, registry=registry_from(jwk), issuers=DEFAULT_ISSUERS, audience="internal"
        )
    assert str(exc.value) == "bad-signature"


@pytest.mark.parametrize(
    "token",
    [None, "", "not-a-token", "a.b", "a.b.c.d", "!!!.!!!.!!!"],
)
def test_malformed_input_is_rejected(token: str | None) -> None:
    key, jwk = keypair()
    with pytest.raises(ServiceTokenError):
        verify_service_token(
            token, registry=registry_from(jwk), issuers=DEFAULT_ISSUERS, audience="internal"
        )


def test_an_empty_registry_admits_nobody() -> None:
    key, _ = keypair()
    token = sign(key, {"alg": "EdDSA", "kid": KID}, valid_claims())
    with pytest.raises(ServiceTokenError) as exc:
        verify_service_token(
            token, registry={}, issuers=DEFAULT_ISSUERS, audience="internal"
        )
    assert str(exc.value) == "unknown-kid"


def test_issuer_config_parsing_is_deliberate() -> None:
    assert parse_issuer_allowlist(None) == DEFAULT_ISSUERS
    assert parse_issuer_allowlist("   ") == DEFAULT_ISSUERS
    assert parse_issuer_allowlist("erp-sales-backend") == {"erp-sales-backend"}
    assert parse_issuer_allowlist("a, b ,") == {"a", "b"}
    assert "erp-sales-backend" in DEFAULT_ISSUERS


def test_a_blank_audience_claim_is_rejected() -> None:
    key, jwk = keypair()
    token = sign(key, {"alg": "EdDSA", "kid": KID}, valid_claims(aud=None))
    with pytest.raises(ServiceTokenError) as exc:
        verify_service_token(
            token, registry=registry_from(jwk), issuers=DEFAULT_ISSUERS, audience="internal"
        )
    assert str(exc.value) == "wrong-audience"
