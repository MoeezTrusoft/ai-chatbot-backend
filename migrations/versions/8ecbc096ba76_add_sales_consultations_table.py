"""add sales consultations table

Revision ID: 8ecbc096ba76
Revises: 20260508_0002
Create Date: 2026-05-18 09:00:30.937709
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '8ecbc096ba76'
down_revision: str | None = '20260508_0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS sales_consultations (
            id VARCHAR(64) PRIMARY KEY,
            customer_id VARCHAR(64) NOT NULL,
            lead_id VARCHAR(64),
            thread_id VARCHAR(64),
            customer_name VARCHAR(255) NOT NULL,
            customer_email VARCHAR(320),
            customer_phone VARCHAR(64),
            services JSONB NOT NULL DEFAULT '[]'::jsonb,
            csr_id VARCHAR(128) NOT NULL,
            csr_name VARCHAR(255) NOT NULL,
            priority_rank INTEGER NOT NULL,
            requested_time_text TEXT NOT NULL,
            customer_timezone VARCHAR(128),
            business_timezone VARCHAR(128) NOT NULL DEFAULT 'America/Chicago',
            starts_at_utc TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            ends_at_utc TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            houston_display_time VARCHAR(255) NOT NULL,
            customer_display_time VARCHAR(255),
            duration_minutes INTEGER NOT NULL DEFAULT 30,
            status VARCHAR(64) NOT NULL DEFAULT 'scheduled',
            source VARCHAR(64) NOT NULL DEFAULT 'chatbot',
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            cancelled_at TIMESTAMP WITHOUT TIME ZONE
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_sales_consultations_customer_id
        ON sales_consultations (customer_id)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_sales_consultations_thread_id
        ON sales_consultations (thread_id)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_sales_consultations_lead_id
        ON sales_consultations (lead_id)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_sales_consultations_csr_status_time
        ON sales_consultations (csr_id, status, starts_at_utc, ends_at_utc)
        WHERE cancelled_at IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_sales_consultations_csr_status_time")
    op.execute("DROP INDEX IF EXISTS ix_sales_consultations_lead_id")
    op.execute("DROP INDEX IF EXISTS ix_sales_consultations_thread_id")
    op.execute("DROP INDEX IF EXISTS ix_sales_consultations_customer_id")
    op.execute("DROP TABLE IF EXISTS sales_consultations")
