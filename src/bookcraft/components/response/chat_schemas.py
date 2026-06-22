"""Shared request/response schemas for the chat API.

Placed here (rather than in api/) to break the circular import between
api/chat.py and services/chat.py. Both modules import from this shared location.
"""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bookcraft.components.attachments.intake import ChatAttachment
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.response.schemas import FormattedBubble


class ChatTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: UUID | None = None
    customer_id: UUID | None = None
    # An attachment-only turn (file uploaded with no typed text) is valid, so the
    # message itself may be empty — but a turn with neither text nor an attachment
    # is rejected by the validator below. (Previously min_length=1 here 422'd every
    # attachment-only upload, so the bot never replied to a bare file upload.)
    message: str = Field(default="", max_length=8000)
    correlation_id: str | None = Field(default=None, max_length=128)
    attachments: list[ChatAttachment] = Field(default_factory=list)
    # The page the visitor is chatting from / the keyword that brought them. Mirrors the
    # greet request so the active service can be anchored even when the frontend skips the
    # proactive /greet call (or calls it without landing data). An ambiguous first message
    # — a bare genre/premise description on a cover-design page — then stays on the correct
    # service instead of being mis-inferred (e.g. as ghostwriting).
    landing_page: str | None = Field(default=None, max_length=200)
    landing_keyword: str | None = Field(default=None, max_length=200)
    # Monotonic per-thread token from the realtime layer. When the client aborts an
    # in-flight turn and re-sends a concatenated burst, the newer turn carries a higher
    # token; an older (superseded) turn must not persist its state. None = not supplied
    # (treated as never superseded).
    turn_token: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _require_message_or_attachment(self) -> "ChatTurnRequest":
        if not self.message.strip() and not self.attachments:
            raise ValueError("a message or at least one attachment is required")
        return self


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
