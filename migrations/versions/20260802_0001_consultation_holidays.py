"""add consultation_holidays table (holiday blackout for scheduling)

Also merges the two open heads (20260528_0004, 20260617_0001) into one.

Revision ID: 20260802_0001
Revises: 20260528_0004, 20260617_0001
Create Date: 2026-08-02 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260802_0001"
down_revision: str | tuple[str, ...] | None = ("20260528_0004", "20260617_0001")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS consultation_holidays (
            holiday_date DATE PRIMARY KEY,
            label VARCHAR(255),
            created_by VARCHAR(128),
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS consultation_holidays")
