"""Enforce booking exclusion and version durable deliveries."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261010_0002"
down_revision: str | None = "20261010_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sales_consultations",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "sales_consultations",
        sa.Column("reminders_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("delivery_outbox", sa.Column("dead_letter_at", sa.DateTime(), nullable=True))
    op.add_column("delivery_outbox", sa.Column("replayed_by", sa.String(255), nullable=True))
    op.create_index("ix_delivery_outbox_dead_letter_at", "delivery_outbox", ["dead_letter_at"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
        # Fail the migration on existing overlaps; do not silently discard bookings.
        op.execute("""
            ALTER TABLE sales_consultations ADD CONSTRAINT consultation_no_overlap
            EXCLUDE USING gist
            (csr_id WITH =, tsrange(starts_at_utc, ends_at_utc, '[)') WITH &&)
            WHERE (status = 'scheduled' AND cancelled_at IS NULL)
        """)
        op.create_check_constraint(
            "consultation_positive_duration", "sales_consultations", "ends_at_utc > starts_at_utc"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("consultation_positive_duration", "sales_consultations", type_="check")
        op.drop_constraint("consultation_no_overlap", "sales_consultations")
    op.drop_index("ix_delivery_outbox_dead_letter_at", table_name="delivery_outbox")
    op.drop_column("delivery_outbox", "replayed_by")
    op.drop_column("delivery_outbox", "dead_letter_at")
    op.drop_column("sales_consultations", "reminders_enabled")
    op.drop_column("sales_consultations", "version")
