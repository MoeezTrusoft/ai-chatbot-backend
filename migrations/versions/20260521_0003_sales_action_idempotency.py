"""sales_action idempotency table

Revision ID: 20260521_0003
Revises: 20260508_0002
Create Date: 2026-05-21

Batch 4: durable idempotency for SalesActionDispatcher.
The unique constraint on idempotency_key prevents multi-worker double-dispatch.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260521_0003"
down_revision: str | None = "20260508_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sales_actions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False, unique=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("slots_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_summary", sa.String(length=512), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_sales_actions_thread", "sales_actions", ["thread_id"])
    op.create_index("ix_sales_actions_type", "sales_actions", ["action_type"])
    op.create_index("ix_sales_actions_status", "sales_actions", ["status"])
    op.create_index(
        "ix_sales_actions_idempotency_key",
        "sales_actions",
        ["idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("sales_actions")
