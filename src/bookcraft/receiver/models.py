from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Column
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel

from bookcraft.components.storage.models import utc_now


class ReceiverAppointment(SQLModel, table=True):
    __tablename__ = "csr_appointments"
    id: UUID = Field(primary_key=True)
    thread_id: UUID = Field(index=True)
    starts_at: datetime = Field(index=True)
    version: int
    status: str = Field(max_length=32)
    payload: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    updated_at: datetime = Field(default_factory=utc_now)


class ReceiverReceipt(SQLModel, table=True):
    __tablename__ = "csr_receipts"
    id: str = Field(primary_key=True, max_length=128)
    payload_hash: str = Field(max_length=64)
    appointment_id: UUID = Field(index=True)
    created_at: datetime = Field(default_factory=utc_now)


class ReceiverTombstone(SQLModel, table=True):
    __tablename__ = "csr_deleted_threads"
    thread_id: UUID = Field(primary_key=True)
    created_at: datetime = Field(default_factory=utc_now)


class ReceiverReminder(SQLModel, table=True):
    __tablename__ = "csr_reminders"
    appointment_id: UUID = Field(primary_key=True)
    version: int = Field(primary_key=True)
    status: str = Field(default="pending", max_length=32)
    updated_at: datetime = Field(default_factory=utc_now)
