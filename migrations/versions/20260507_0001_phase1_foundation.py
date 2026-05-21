"""phase1 foundation tables

Revision ID: 20260507_0001
Revises:
Create Date: 2026-05-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260507_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_threads", sa.Integer(), nullable=False),
        sa.Column("total_quotes_value", sa.Float(), nullable=False),
        sa.Column("has_signed_agreement", sa.Boolean(), nullable=False),
        sa.Column("merged_into_id", sa.Uuid(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["merged_into_id"], ["customers.id"]),
    )
    op.create_index("ix_customers_email", "customers", ["email"])
    op.create_index("ix_customers_phone", "customers", ["phone"])
    op.create_index("ix_customers_signed", "customers", ["has_signed_agreement"])

    op.create_table(
        "threads",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("customer_id", sa.Uuid(), nullable=True),
        sa.Column("sales_stage", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("is_lead_created", sa.Boolean(), nullable=False),
        sa.Column("is_escalated", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("turn_count", sa.Integer(), nullable=False),
        sa.Column("last_redetect_turn", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deletion_reason", sa.Text(), nullable=True),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
    )
    op.create_index("ix_threads_customer", "threads", ["customer_id"])
    op.create_index("ix_threads_stage", "threads", ["sales_stage"])
    op.create_index("ix_threads_active", "threads", ["last_message_at"])

    op.create_table(
        "thread_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("previous_hash", sa.String(length=64), nullable=True),
        sa.Column("event_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_thread_events_thread_sequence",
        "thread_events",
        ["thread_id", "sequence"],
        unique=True,
    )
    op.create_index("ix_thread_events_type", "thread_events", ["event_type"])

    op.create_table(
        "intent_classifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("turn_sequence", sa.Integer(), nullable=False),
        sa.Column("message_text", sa.Text(), nullable=False),
        sa.Column("votes", sa.JSON(), nullable=False),
        sa.Column("trimatch_result", sa.JSON(), nullable=True),
        sa.Column("final_decision", sa.JSON(), nullable=False),
        sa.Column("trimatch_diverged", sa.Boolean(), nullable=False),
        sa.Column("llms_diverged", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_intent_thread", "intent_classifications", ["thread_id"])

    op.create_table(
        "tool_invocation_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("turn_sequence", sa.Integer(), nullable=False),
        sa.Column("invoked_by", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("params_hash", sa.String(length=64), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_kind", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
    )
    op.create_index("ix_tool_logs_idempotency", "tool_invocation_logs", ["idempotency_key"])
    op.create_index("ix_tool_logs_tool", "tool_invocation_logs", ["tool_name"])
    op.create_index("ix_tool_logs_thread", "tool_invocation_logs", ["thread_id"])

    op.create_table(
        "deferred_tool_invocations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("queue_id", sa.String(length=64), nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("turn_sequence", sa.Integer(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("deferred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(length=128), nullable=True),
        sa.Column("decision_notes", sa.Text(), nullable=True),
        sa.Column("invocation_result", sa.JSON(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_deferred_queue", "deferred_tool_invocations", ["queue_id", "status"])
    op.create_index("ix_deferred_thread", "deferred_tool_invocations", ["thread_id"])


def downgrade() -> None:
    op.drop_table("deferred_tool_invocations")
    op.drop_table("tool_invocation_logs")
    op.drop_table("intent_classifications")
    op.drop_table("thread_events")
    op.drop_table("threads")
    op.drop_table("customers")
