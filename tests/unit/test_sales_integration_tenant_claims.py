"""Unit coverage for the Sales integration surface added in P1.

Covers the three additive pieces the integration depends on before any MCP
traffic exists:

* the tenant/site/thread claims Sales signs into the turn token, including the
  no-claim path that must keep behaving exactly as before (plan §P1.2.3);
* the switches, which must all default OFF so an untouched deployment is
  unchanged (plan §6.4);
* the nullable ``tenant_id`` partition columns on the rows the bot owns.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory

from bookcraft.api.auth import AuthError, verify_jwt_token
from bookcraft.components.storage.models import (
    SalesConsultationRecord,
    SalesLeadRecord,
    ThreadRecord,
)
from bookcraft.infra.config import Settings

SIGNING_KEY = "unit-test-signing-key"  # noqa: S105
REPO_ROOT = Path(__file__).resolve().parents[2]
TENANT_COLUMN_TABLES = ("threads", "sales_leads", "sales_consultations")


def b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def make_token(claims: dict[str, object], key: str = SIGNING_KEY) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    raw_header = b64(json.dumps(header, separators=(",", ":")).encode())
    raw_payload = b64(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{raw_header}.{raw_payload}"
    digest = hmac.new(key.encode(), signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{b64(digest)}"


def settings() -> Settings:
    return Settings(app_env="test", api_auth_mode="jwt", jwt_signing_key=SIGNING_KEY)


def verify(claims: dict[str, object]):
    return verify_jwt_token(make_token({"exp": int(time.time()) + 3600, **claims}), settings())


# ── Tenant claims ───────────────────────────────────────────────────────────


def test_tenant_site_and_thread_claims_reach_the_principal() -> None:
    tenant_id, thread_id, customer_id = uuid4(), uuid4(), uuid4()
    principal = verify(
        {
            "sub": "sales-website-bridge",
            "customer_id": str(customer_id),
            "tenant_id": str(tenant_id),
            "site_id": "bookcraft-main",
            "thread_id": str(thread_id),
            "scope": "chat:turn",
        }
    )

    assert principal.tenant_id == tenant_id
    assert principal.site_id == "bookcraft-main"
    assert principal.thread_id == thread_id
    assert principal.customer_id == customer_id


def test_the_short_tid_alias_is_accepted() -> None:
    tenant_id = uuid4()
    principal = verify({"sub": "sales-website-bridge", "tid": str(tenant_id)})

    assert principal.tenant_id == tenant_id


def test_a_token_without_tenant_claims_stays_single_tenant() -> None:
    """Plan §P1.2.3: absent claims must not change any behaviour."""
    principal = verify({"sub": "customer@example.com", "customer_id": str(uuid4())})

    assert principal.tenant_id is None
    assert principal.site_id is None
    assert principal.thread_id is None


def test_a_blank_site_id_claim_is_absent_not_invalid() -> None:
    principal = verify({"sub": "customer@example.com", "site_id": "   "})

    assert principal.site_id is None


@pytest.mark.parametrize(
    ("claim", "value"),
    [
        ("tenant_id", "not-a-uuid"),
        ("tenant_id", 42),
        ("thread_id", "not-a-uuid"),
        ("site_id", "has spaces"),
        ("site_id", "x"),
        ("site_id", 7),
    ],
)
def test_a_malformed_tenant_claim_fails_closed(claim: str, value: object) -> None:
    """A tenant claim we cannot trust must reject the token, not be ignored."""
    with pytest.raises(AuthError):
        verify({"sub": "sales-website-bridge", claim: value})


# ── Integration switches ────────────────────────────────────────────────────


def test_every_integration_switch_defaults_off() -> None:
    configured = Settings(app_env="test")

    assert configured.sales_mcp_enabled is False
    assert configured.sales_mcp_url is None
    assert configured.sales_mcp_signing_key is None
    assert configured.sales_mcp_tenant_allowlist_json == "[]"
    assert configured.chatbot_local_scheduler is True
    assert configured.chatbot_mcp_server_enabled is False
    # Authentication is unchanged: the local default stays off.
    assert configured.api_auth_mode == "off"


def test_the_integration_switches_are_configurable_by_env() -> None:
    configured = Settings(
        app_env="test",
        sales_mcp_enabled=True,
        sales_mcp_url="https://sales.internal",
        sales_mcp_tenant_allowlist_json='[{"siteId":"bookcraft-main","tenantId":"x","ownerId":"1"}]',
        chatbot_local_scheduler=False,
        chatbot_mcp_server_enabled=True,
    )

    assert configured.sales_mcp_enabled is True
    assert configured.sales_mcp_url == "https://sales.internal"
    assert configured.chatbot_local_scheduler is False
    assert configured.chatbot_mcp_server_enabled is True
    assert "bookcraft-main" in configured.sales_mcp_tenant_allowlist_json


def test_the_outbound_token_matches_the_pinned_service_identity() -> None:
    configured = Settings(app_env="test")

    assert configured.sales_mcp_issuer == "ai-chatbot-backend"
    assert configured.sales_mcp_audience == "internal"
    assert configured.sales_mcp_kid == "ai-chatbot-backend"


# ── Tenant partition columns ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "model", [ThreadRecord, SalesLeadRecord, SalesConsultationRecord]
)
def test_the_bot_owned_rows_accept_a_nullable_tenant(model: type) -> None:
    """Additive + backward compatible: NULL means "predates the integration"."""
    field = model.model_fields["tenant_id"]

    assert field.default is None
    assert field.is_required() is False
    assert model.__table__.c.tenant_id.nullable is True
    # Indexed so a tenant-partitioned read does not scan the table.
    assert any(
        index.columns.keys() == ["tenant_id"] for index in model.__table__.indexes
    )


# ── Migration ───────────────────────────────────────────────────────────────


def test_the_tenant_migration_is_the_single_head_after_the_previous_one() -> None:
    config = AlembicConfig()
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    directory = ScriptDirectory.from_config(config)

    assert directory.get_heads() == ["20261014_0001"]
    revision = directory.get_revision("20261014_0001")
    assert revision.down_revision == "20260802_0001"


def _migration_statements(monkeypatch: pytest.MonkeyPatch) -> tuple[list[str], list[str]]:
    """Render the migration's DDL by capturing ``op.execute`` calls."""
    import importlib.util

    path = REPO_ROOT / "migrations" / "versions" / "20261014_0001_tenant_id_columns.py"
    spec = importlib.util.spec_from_file_location("tenant_id_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    upgrade_sql: list[str] = []
    downgrade_sql: list[str] = []
    recorder = _RecordingOp(upgrade_sql)
    monkeypatch.setattr(module, "op", recorder)

    module.upgrade()
    recorder.target = downgrade_sql
    module.downgrade()
    return upgrade_sql, downgrade_sql


class _RecordingOp:
    def __init__(self, target: list[str]) -> None:
        self.target = target

    def execute(self, statement: str) -> None:
        self.target.append(statement)

    def __getattr__(self, name: str):  # pragma: no cover - guard against other ops
        if name.startswith("__"):
            raise AttributeError(name)
        raise AssertionError("the tenant migration may only run raw DDL")


def test_the_tenant_migration_is_additive_and_reversible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upgrade_sql, downgrade_sql = _migration_statements(monkeypatch)

    assert len(upgrade_sql) == 3
    assert len(downgrade_sql) == 3
    for table, statement in zip(TENANT_COLUMN_TABLES, upgrade_sql, strict=True):
        assert f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS tenant_id UUID NULL" in statement
        index_ddl = f"CREATE INDEX IF NOT EXISTS ix_{table}_tenant_id ON {table} (tenant_id)"
        assert index_ddl in statement
        # Guarded so a database where create_all has not built the table yet
        # still migrates cleanly (the baseline chain does not create sales_leads).
        assert f"to_regclass('{table}') IS NOT NULL" in statement
    for table, statement in zip(TENANT_COLUMN_TABLES, downgrade_sql, strict=True):
        assert f"DROP INDEX IF EXISTS ix_{table}_tenant_id" in statement
        assert f"ALTER TABLE {table} DROP COLUMN IF EXISTS tenant_id" in statement


def test_the_migration_does_not_backfill_or_set_a_default() -> None:
    """Nothing may invent a tenant for the rows that predate the integration."""
    source = (
        REPO_ROOT / "migrations" / "versions" / "20261014_0001_tenant_id_columns.py"
    ).read_text()

    assert "UPDATE " not in source
    assert "NOT NULL" not in source.replace("IS NOT NULL", "")
    assert "DEFAULT" not in source
