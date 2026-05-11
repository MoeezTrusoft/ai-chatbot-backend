import base64
import hashlib
import hmac
import json
import time
from uuid import uuid4

import pytest

from bookcraft.api.auth import AuthError, verify_jwt_token
from bookcraft.infra.config import Settings

SIGNING_KEY = "unit-test-signing-key"  # noqa: S105


def make_token(claims: dict[str, object], key: str = SIGNING_KEY) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    raw_header = b64(json.dumps(header, separators=(",", ":")).encode())
    raw_payload = b64(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{raw_header}.{raw_payload}"
    digest = hmac.new(key.encode(), signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{b64(digest)}"


def b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def test_verify_jwt_token_accepts_valid_hs256_token() -> None:
    customer_id = uuid4()
    token = make_token(
        {
            "sub": "customer@example.com",
            "customer_id": str(customer_id),
            "scope": "chat:write",
            "exp": int(time.time()) + 3600,
        }
    )

    principal = verify_jwt_token(
        token,
        Settings(app_env="test", api_auth_mode="jwt", jwt_signing_key=SIGNING_KEY),
    )

    assert principal.subject == "customer@example.com"
    assert principal.customer_id == customer_id
    assert "chat:write" in principal.scopes


def test_verify_jwt_token_rejects_bad_signature() -> None:
    token = make_token({"sub": "customer@example.com", "exp": int(time.time()) + 3600})

    with pytest.raises(AuthError):
        verify_jwt_token(
            token,
            Settings(app_env="test", api_auth_mode="jwt", jwt_signing_key="wrong-key"),
        )


def test_verify_jwt_token_rejects_expired_token() -> None:
    token = make_token({"sub": "customer@example.com", "exp": int(time.time()) - 1})

    with pytest.raises(AuthError):
        verify_jwt_token(
            token,
            Settings(app_env="test", api_auth_mode="jwt", jwt_signing_key=SIGNING_KEY),
        )
