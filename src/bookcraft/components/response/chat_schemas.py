"""Shared request/response schemas for the chat API.

Placed here (rather than in api/) to break the circular import between
api/chat.py and services/chat.py. Both modules import from this shared location.
"""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from bookcraft.components.attachments.intake import ChatAttachment
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.response.schemas import FormattedBubble


class ChatTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: UUID | None = None
    customer_id: UUID | None = None
    message: str = Field(min_length=1, max_length=8000)
    correlation_id: str | None = Field(default=None, max_length=128)
    attachments: list[ChatAttachment] = Field(default_factory=list)


class ChatTurnResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: UUID
    bubbles: list[FormattedBubble]
    intent: IntentVote | None
    language_status: str
    debug_event_ids: list[str] = Field(default_factory=list)
    blocked: bool = False
    input_disabled: bool = False
    system_message: str | None = None
    action_events: list[dict[str, object]] = Field(default_factory=list)
