from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Column, String
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel

from bookcraft.domain.enums import SalesStage
from bookcraft.domain.state import ThreadState


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Customer(SQLModel, table=True):
    __tablename__ = "customers"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    email: str | None = Field(default=None, index=True, max_length=255)
    phone: str | None = Field(default=None, index=True, max_length=50)
    name: str | None = Field(default=None, max_length=255)
    first_seen_at: datetime = Field(default_factory=utc_now)
    last_seen_at: datetime = Field(default_factory=utc_now)
    total_threads: int = 0
    total_quotes_value: float = 0.0
    has_signed_agreement: bool = Field(default=False, index=True)
    merged_into_id: UUID | None = Field(default=None, foreign_key="customers.id")
    metadata_: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSON, nullable=False),
    )
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    deleted_at: datetime | None = Field(default=None, index=True)


class SalesLeadRecord(SQLModel, table=True):
    __tablename__ = "sales_leads"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    customer_id: UUID | None = Field(default=None, foreign_key="customers.id", index=True)
    thread_id: UUID | None = Field(default=None, index=True)
    name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, index=True, max_length=255)
    phone: str | None = Field(default=None, index=True, max_length=50)
    preferred_contact_method: str | None = Field(default=None, max_length=32)
    services: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False),
    )
    genre: str | None = Field(default=None, max_length=255)
    word_count: int | None = None
    page_count: int | None = None
    manuscript_status: str | None = Field(default=None, max_length=64)
    deadline: str | None = Field(default=None, max_length=255)
    source: str = Field(default="chatbot", max_length=64)
    status: str = Field(default="new", max_length=32, index=True)
    notes: str | None = None
    metadata_: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSON, nullable=False),
    )
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    deleted_at: datetime | None = Field(default=None, index=True)


class SalesPricingQuoteRecord(SQLModel, table=True):
    __tablename__ = "sales_pricing_quotes"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    quote_id: UUID = Field(default_factory=uuid4, index=True)
    lead_id: UUID | None = Field(default=None, foreign_key="sales_leads.id", index=True)
    customer_id: UUID | None = Field(default=None, foreign_key="customers.id", index=True)
    thread_id: UUID | None = Field(default=None, index=True)
    services: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False),
    )
    input_params: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    used_default_assumptions: bool = False
    assumptions: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    quote_output: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    customer_safe_summary: str | None = None
    status: str = Field(default="created", max_length=64, index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ThreadRecord(SQLModel, table=True):
    __tablename__ = "threads"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    customer_id: UUID | None = Field(default=None, foreign_key="customers.id", index=True)
    sales_stage: SalesStage = Field(
        default=SalesStage.NEW,
        sa_column=Column("sales_stage", String(32), nullable=False, index=True),
    )
    priority: str = Field(default="medium", max_length=16)
    language: str = Field(default="en", max_length=8)
    is_lead_created: bool = False
    is_escalated: bool = False
    version: int = 0
    turn_count: int = 0
    last_redetect_turn: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    last_message_at: datetime | None = Field(default=None, index=True)
    state: dict[str, Any] = Field(
        default_factory=lambda: ThreadState().model_dump(mode="json"),
        sa_column=Column(JSON, nullable=False),
    )
    deleted_at: datetime | None = Field(default=None, index=True)
    deletion_reason: str | None = None
    retention_until: datetime | None = None


class ThreadEvent(SQLModel, table=True):
    __tablename__ = "thread_events"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    thread_id: UUID = Field(index=True)
    sequence: int = Field(index=True)
    event_type: str = Field(max_length=64, index=True)
    actor: str = Field(default="system", max_length=32)
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    confidence: float | None = None
    previous_hash: str | None = Field(default=None, max_length=64)
    event_hash: str = Field(max_length=64)
    created_at: datetime = Field(default_factory=utc_now, index=True)


class IntentClassificationLog(SQLModel, table=True):
    __tablename__ = "intent_classifications"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    thread_id: UUID = Field(index=True)
    turn_sequence: int
    message_text: str
    votes: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    trimatch_result: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    final_decision: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    trimatch_diverged: bool = False
    llms_diverged: bool = False
    created_at: datetime = Field(default_factory=utc_now, index=True)


class ToolInvocationLog(SQLModel, table=True):
    __tablename__ = "tool_invocation_logs"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    correlation_id: str = Field(index=True, max_length=128)
    tool_name: str = Field(index=True, max_length=128)
    thread_id: UUID = Field(index=True)
    turn_sequence: int
    invoked_by: str = Field(max_length=32)
    idempotency_key: str = Field(index=True, max_length=256)
    params_hash: str = Field(max_length=64)
    params: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    started_at: datetime = Field(default_factory=utc_now, index=True)
    completed_at: datetime | None = None
    duration_ms: int | None = None
    status: str = Field(max_length=32, index=True)
    result: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    error_kind: str | None = Field(default=None, max_length=64)
    error_detail: str | None = None


class DeferredToolInvocation(SQLModel, table=True):
    __tablename__ = "deferred_tool_invocations"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    queue_id: str = Field(index=True, max_length=64)
    tool_name: str = Field(index=True, max_length=128)
    thread_id: UUID = Field(index=True)
    turn_sequence: int
    params: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    context: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    status: str = Field(default="pending", index=True, max_length=16)
    deferred_at: datetime = Field(default_factory=utc_now)
    decided_at: datetime | None = None
    decided_by: str | None = Field(default=None, max_length=128)
    decision_notes: str | None = None
    invocation_result: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    expires_at: datetime


class GraphNodeRecord(SQLModel, table=True):
    __tablename__ = "graph_nodes"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    thread_id: UUID = Field(index=True)
    node_type: str = Field(max_length=64, index=True)
    label: str = Field(max_length=255)
    text: str | None = None
    turn_sequence: int = Field(index=True)
    metadata_: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSON, nullable=False),
    )
    created_at: datetime = Field(default_factory=utc_now, index=True)


class GraphEdgeRecord(SQLModel, table=True):
    __tablename__ = "graph_edges"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    thread_id: UUID = Field(index=True)
    source_node_id: UUID = Field(index=True)
    target_node_id: UUID = Field(index=True)
    relation_type: str = Field(max_length=64, index=True)
    confidence: float = 1.0
    compliance_score: float = 1.0
    evidence: str | None = None
    metadata_: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSON, nullable=False),
    )
    created_at: datetime = Field(default_factory=utc_now, index=True)
