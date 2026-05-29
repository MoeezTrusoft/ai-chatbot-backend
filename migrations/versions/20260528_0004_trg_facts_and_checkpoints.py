"""TRG fact records and conversation checkpoints

Revision ID: 20260528_0004
Revises: 20260521_0003
Create Date: 2026-05-28

Adds two tables introduced in the 3-tier context management plan:
  - trg_fact_records   — PostgreSQL persistence for TRG semantic facts,
                         surviving Redis TTL expiry for cold-start reload.
  - conversation_checkpoints — permanent state snapshots at sales milestones
                               (lead_created, service_confirmed, etc.).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260528_0004"
down_revision: str | None = "20260521_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trg_fact_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("fact_path", sa.String(length=200), nullable=False),
        sa.Column("fact_value", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("source_extraction", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("turn_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("raw_excerpt", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trg_fact_records_thread_id", "trg_fact_records", ["thread_id"])
    op.create_index("ix_trg_fact_records_fact_path", "trg_fact_records", ["fact_path"])
    op.create_index("ix_trg_fact_records_created_at", "trg_fact_records", ["created_at"])
    # Unique constraint used by upsert logic (thread_id, fact_path) → one row per fact.
    op.create_unique_constraint(
        "uq_trg_fact_records_thread_fact", "trg_fact_records", ["thread_id", "fact_path"]
    )

    op.create_table(
        "conversation_checkpoints",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("milestone", sa.String(length=64), nullable=False),
        sa.Column("state_snapshot", sa.JSON(), nullable=False),
        sa.Column("turn_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_conversation_checkpoints_thread_id", "conversation_checkpoints", ["thread_id"]
    )
    op.create_index(
        "ix_conversation_checkpoints_milestone", "conversation_checkpoints", ["milestone"]
    )
    op.create_index(
        "ix_conversation_checkpoints_created_at", "conversation_checkpoints", ["created_at"]
    )


def downgrade() -> None:
    op.drop_table("conversation_checkpoints")
    op.drop_table("trg_fact_records")
