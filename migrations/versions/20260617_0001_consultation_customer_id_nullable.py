"""make sales_consultations.customer_id nullable

The ORM model (ConsultationRecord) declares ``customer_id: UUID | None`` —
matching ``sales_leads.customer_id``, which is nullable so anonymous web-chat
sessions (no authenticated customer) can still create leads. The original
consultations DDL (8ecbc096ba76) hard-coded ``customer_id VARCHAR(64) NOT NULL``,
which diverged from the model and from sales_leads. As a result a confirmed
booking from an anonymous session reached ready_to_schedule, dispatched, then
died on insert with a NotNullViolationError on customer_id — so
consultation_scheduled was never emitted (BUG-6040 tail).

This drops the NOT NULL constraint so the booking completes for sessions
without a customer_id.

Revision ID: 20260617_0001
Revises: 8ecbc096ba76
Create Date: 2026-06-17 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260617_0001"
down_revision: str | None = "8ecbc096ba76"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE sales_consultations ALTER COLUMN customer_id DROP NOT NULL")


def downgrade() -> None:
    # Re-asserting NOT NULL would fail if any anonymous-session rows exist; this
    # mirrors the original (pre-fix) schema and is best-effort.
    op.execute("ALTER TABLE sales_consultations ALTER COLUMN customer_id SET NOT NULL")
