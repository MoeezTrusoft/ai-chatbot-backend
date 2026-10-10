"""Align legacy consultation identity columns with the UUID model."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261010_0004"
down_revision: str | None = "20261010_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for name in ("customer_id", "lead_id", "thread_id"):
            # Invalid legacy identities fail atomically for operator reconciliation.
            # Never replace them with NULL or invent a new customer association.
            op.alter_column(
                "sales_consultations",
                name,
                existing_type=sa.String(64),
                type_=sa.Uuid(),
                postgresql_using=f"{name}::uuid",
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for name in ("customer_id", "lead_id", "thread_id"):
            op.alter_column(
                "sales_consultations",
                name,
                existing_type=sa.Uuid(),
                type_=sa.String(64),
                postgresql_using=f"{name}::text",
            )
