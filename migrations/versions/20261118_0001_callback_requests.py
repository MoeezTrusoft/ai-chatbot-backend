"""add sales_callback_requests table (mobile call-now and scheduled callbacks)

Revision ID: 20261118_0001
Revises: 20260802_0001
Create Date: 2026-11-18 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261118_0001"
down_revision: str | tuple[str, ...] | None = "20260802_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS sales_callback_requests (
            id UUID PRIMARY KEY,
            thread_id UUID NOT NULL,
            customer_id UUID,
            visit_id VARCHAR(64),
            mode VARCHAR(16) NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'requested',
            customer_phone VARCHAR(50) NOT NULL,
            customer_name VARCHAR(255),
            requested_time_text TEXT,
            customer_timezone VARCHAR(64),
            summary TEXT NOT NULL DEFAULT '',
            promised_within_minutes INTEGER,
            idempotency_key VARCHAR(64) NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_sales_callback_requests_idempotency_key "
        "ON sales_callback_requests (idempotency_key)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_sales_callback_requests_thread_id "
        "ON sales_callback_requests (thread_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_sales_callback_requests_status_created "
        "ON sales_callback_requests (status, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sales_callback_requests")
