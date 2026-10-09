"""Thread ownership, replayable actions, and transactional CSR delivery outbox."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261010_0001"
down_revision: str | None = "20260802_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("threads", sa.Column("owner_subject", sa.String(255), nullable=True))
    op.create_index("ix_threads_owner_subject", "threads", ["owner_subject"])
    op.add_column("sales_actions", sa.Column("result_payload", sa.JSON(), nullable=True))
    op.create_table(
        "delivery_outbox",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("topic", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(64), nullable=True),
    )
    op.create_index("ix_delivery_outbox_available_at", "delivery_outbox", ["available_at"])
    op.create_index("ix_delivery_outbox_aggregate_id", "delivery_outbox", ["aggregate_id"])


def downgrade() -> None:
    op.drop_table("delivery_outbox")
    op.drop_column("sales_actions", "result_payload")
    op.drop_index("ix_threads_owner_subject", table_name="threads")
    op.drop_column("threads", "owner_subject")
