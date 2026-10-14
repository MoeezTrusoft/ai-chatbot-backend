"""Reversible-migration proof for the tenant_id columns, on a real Postgres.

The unit suite checks the migration's structure; this one checks that the DDL
actually runs, that a NULL tenant is accepted, and that the downgrade removes
exactly what the upgrade added — the properties a rollback depends on.

The database is treated as disposable: the ``public`` schema is dropped and
recreated before and after, so the test is deterministic regardless of what a
previous run left behind. Point it only at a throwaway database:

    SALES_INTEGRATION_MIGRATION_DATABASE_URL=postgresql+asyncpg://user:pw@host:port/db \\
        pytest tests/integration/test_sales_integration_migration_live.py -v -s

Note on the event loop: ``migrations/env.py`` runs Alembic's async migration in
its own ``asyncio.run``, so the Alembic commands here are driven synchronously
and every async probe builds its own short-lived engine rather than sharing a
pool across loops.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from bookcraft.infra.config import get_settings

MIGRATION_DATABASE_URL = os.getenv("SALES_INTEGRATION_MIGRATION_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not MIGRATION_DATABASE_URL,
    reason="SALES_INTEGRATION_MIGRATION_DATABASE_URL not set",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TENANT_COLUMN_TABLES = ("threads", "sales_leads", "sales_consultations")
PREVIOUS_HEAD = "20260802_0001"
UNFILED_THREAD_ID = "00000000-0000-4000-8000-0000000000aa"


@pytest.fixture(autouse=True)
def _point_alembic_at_the_disposable_database() -> None:
    """``migrations/env.py`` resolves its URL from settings, not from the Alembic
    config, so the disposable URL has to be exported as DATABASE_URL."""
    assert MIGRATION_DATABASE_URL
    os.environ["DATABASE_URL"] = MIGRATION_DATABASE_URL
    get_settings.cache_clear()


def _config() -> AlembicConfig:
    config = AlembicConfig()
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", MIGRATION_DATABASE_URL)
    return config


async def _using_engine(fn: Callable[[AsyncEngine], Awaitable[Any]]) -> Any:
    engine = create_async_engine(MIGRATION_DATABASE_URL)
    try:
        return await fn(engine)
    finally:
        await engine.dispose()


async def _reset_schema() -> None:
    async def go(engine: AsyncEngine) -> None:
        async with engine.begin() as connection:
            await connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public"))

    await _using_engine(go)


async def _execute(statement: str, params: dict[str, Any] | None = None) -> None:
    async def go(engine: AsyncEngine) -> None:
        async with engine.begin() as connection:
            await connection.execute(text(statement), params or {})

    await _using_engine(go)


async def _columns(table: str) -> dict[str, dict[str, Any]]:
    async def go(engine: AsyncEngine) -> dict[str, dict[str, Any]]:
        async with engine.connect() as connection:
            return await connection.run_sync(
                lambda sync: {
                    column["name"]: column for column in inspect(sync).get_columns(table)
                }
            )

    return await _using_engine(go)


async def _indexes(tables: tuple[str, ...]) -> set[str]:
    async def go(engine: AsyncEngine) -> set[str]:
        async with engine.connect() as connection:
            return await connection.run_sync(
                lambda sync: {
                    index["name"]
                    for table in tables
                    for index in inspect(sync).get_indexes(table)
                }
            )

    return await _using_engine(go)


async def _scalar(statement: str, params: dict[str, Any]) -> Any:
    async def go(engine: AsyncEngine) -> Any:
        async with engine.connect() as connection:
            result = await connection.execute(text(statement), params)
            return result.scalar_one()

    return await _using_engine(go)


def test_the_tenant_migration_applies_and_rolls_back() -> None:
    config = _config()
    try:
        asyncio.run(_reset_schema())

        # A clean baseline: the chain immediately before the tenant columns, plus
        # the one table the baseline chain does not create (sales_leads is owned
        # by create_all), so both branches of the guarded migration are exercised.
        command.upgrade(config, PREVIOUS_HEAD)
        asyncio.run(_execute("CREATE TABLE sales_leads (id text)"))

        command.upgrade(config, "head")
        for table in TENANT_COLUMN_TABLES:
            column = asyncio.run(_columns(table)).get("tenant_id")
            assert column is not None, f"{table}.tenant_id missing after upgrade"
            assert column["nullable"] is True
            assert f"ix_{table}_tenant_id" in asyncio.run(_indexes((table,)))

        # A NULL tenant stays valid: a row written before the integration has
        # none. (The baseline `threads` DDL predates the ORM defaults, so every
        # NOT NULL column has to be supplied explicitly.)
        asyncio.run(
            _execute(
                """
                INSERT INTO threads (
                    id, tenant_id, sales_stage, priority, language, is_lead_created,
                    is_escalated, version, turn_count, last_redetect_turn,
                    created_at, updated_at, state
                ) VALUES (
                    :id, NULL, 'new', 'medium', 'en', FALSE,
                    FALSE, 0, 0, 0, now(), now(), '{}'
                )
                """,
                {"id": UNFILED_THREAD_ID},
            )
        )

        # Rollback removes exactly what the upgrade added.
        command.downgrade(config, PREVIOUS_HEAD)
        for table in TENANT_COLUMN_TABLES:
            assert "tenant_id" not in asyncio.run(_columns(table))
        remaining = asyncio.run(_indexes(TENANT_COLUMN_TABLES))
        assert not any(name.endswith("_tenant_id") for name in remaining)

        # The pre-integration row is untouched by the round trip.
        survivors = asyncio.run(
            _scalar(
                "SELECT count(*) FROM threads WHERE id = :id", {"id": UNFILED_THREAD_ID}
            )
        )
        assert survivors == 1

        command.upgrade(config, "head")
    finally:
        asyncio.run(_reset_schema())
