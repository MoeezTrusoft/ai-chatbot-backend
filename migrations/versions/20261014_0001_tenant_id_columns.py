"""add nullable tenant_id to threads, sales_leads and sales_consultations

Part of the Sales ⇄ chatbot integration (SALES-AI-CHATBOT-INTEGRATION-PLAN.md
§P1 T1.2.2). Sales signs the conversation's tenant into the turn token; this
stores that tenant on the three rows the bot owns so a multi-tenant deployment
can partition them, and so the bot can address the same tenant when it writes
CRM records to Sales over MCP.

Additive and reversible: the column is nullable with no default, so existing
single-tenant rows are untouched and a rollout without a tenant claim behaves
exactly as it did before. Nothing is backfilled — a NULL tenant means "predates
the integration", which is the honest reading — and the column can be dropped
on rollback without losing data.

Guard note: this repo's schema is created additively by
``bookcraft.components.storage.db.create_all``, and the baseline migration
chain does not create every model table (``sales_leads`` is one of them). So
each step is guarded on the table already existing; on a fresh database
``create_all`` later creates the table with ``tenant_id`` already present, and
the guarded migration is a no-op.

Revision ID: 20261014_0001
Revises: 20260802_0001
Create Date: 2026-10-14 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261014_0001"
down_revision: str | tuple[str, ...] | None = "20260802_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("threads", "sales_leads", "sales_consultations")


def upgrade() -> None:
    for table in TABLES:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF to_regclass('{table}') IS NOT NULL THEN
                    ALTER TABLE {table} ADD COLUMN IF NOT EXISTS tenant_id UUID NULL;
                    CREATE INDEX IF NOT EXISTS ix_{table}_tenant_id ON {table} (tenant_id);
                END IF;
            END
            $$;
            """
        )


def downgrade() -> None:
    for table in TABLES:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF to_regclass('{table}') IS NOT NULL THEN
                    DROP INDEX IF EXISTS ix_{table}_tenant_id;
                    ALTER TABLE {table} DROP COLUMN IF EXISTS tenant_id;
                END IF;
            END
            $$;
            """
        )
