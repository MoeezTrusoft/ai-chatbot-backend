"""Staff workflows, customer feedback and companion receiver."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261010_0003"
down_revision: str | None = "20261010_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "management_workflows",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("thread_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for name in ("thread_id", "kind", "status", "created_at"):
        op.create_index(f"ix_management_workflows_{name}", "management_workflows", [name])
    op.create_table(
        "conversation_feedback",
        sa.Column("thread_id", sa.Uuid(), primary_key=True),
        sa.Column("turn_sequence", sa.Integer(), primary_key=True),
        sa.Column("helpful", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("comment", sa.String(2000), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_conversation_feedback_created_at", "conversation_feedback", ["created_at"])
    op.create_table(
        "csr_appointments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_csr_appointments_thread_id", "csr_appointments", ["thread_id"])
    op.create_index("ix_csr_appointments_starts_at", "csr_appointments", ["starts_at"])
    op.create_table(
        "csr_receipts",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("appointment_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_csr_receipts_appointment_id", "csr_receipts", ["appointment_id"])
    op.create_table(
        "csr_deleted_threads",
        sa.Column("thread_id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "csr_reminders",
        sa.Column("appointment_id", sa.Uuid(), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    for table in (
        "csr_reminders",
        "csr_deleted_threads",
        "csr_receipts",
        "csr_appointments",
        "conversation_feedback",
        "management_workflows",
    ):
        op.drop_table(table)
