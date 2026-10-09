from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Column
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel

from bookcraft.components.storage.models import utc_now


class WorkflowRecord(SQLModel, table=True):
    __tablename__ = "management_workflows"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    thread_id: UUID | None = Field(default=None, index=True)
    kind: str = Field(max_length=32, index=True)
    status: str = Field(max_length=32, index=True)
    version: int = 1
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now)


class FeedbackRecord(SQLModel, table=True):
    __tablename__ = "conversation_feedback"
    thread_id: UUID = Field(primary_key=True)
    turn_sequence: int = Field(primary_key=True)
    helpful: bool
    reason: str = Field(max_length=32)
    comment: str | None = Field(default=None, max_length=2000)
    created_at: datetime = Field(default_factory=utc_now, index=True)
