import re
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bookcraft.api.auth import authenticate_websocket, require_http_auth
from bookcraft.api.correlation import sanitize_correlation_id
from bookcraft.api.security import is_origin_allowed
from bookcraft.components.response.chat_schemas import ChatTurnRequest, ChatTurnResponse
from bookcraft.infra.config import Settings
from bookcraft.infra.rate_limit import RateLimiter, client_ip_from_scope
from bookcraft.services.chat import ChatService

# Re-export so existing code that imports from bookcraft.api.chat still works.
__all__ = [
    "ChatTurnRequest",
    "ChatTurnResponse",
    "ChatGreetRequest",
    "CsrTurnRequest",
    "HandoverRequest",
    "HandoverResponse",
    "ChatFactsRequest",
    "ChatFactsResponse",
]

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


class ChatGreetRequest(BaseModel):
    """Proactive greeting request — sent when the chat widget opens.

    The frontend passes the page the visitor landed on and any keyword signal
    (e.g. UTM keyword or SEO search term) so the first message is personalised.
    """

    model_config = ConfigDict(extra="forbid")

    thread_id: UUID | None = None
    customer_id: UUID | None = None
    landing_page: str | None = Field(default=None, max_length=200)
    landing_keyword: str | None = Field(default=None, max_length=200)
    correlation_id: str | None = Field(default=None, max_length=128)


class CsrTurnRequest(BaseModel):
    """A single CSR + optional user message to be ingested for context (no bot response)."""

    model_config = ConfigDict(extra="forbid")

    thread_id: UUID
    csr_id: str = Field(max_length=128)
    csr_name: str = Field(max_length=255)
    user_message: str | None = Field(default=None, max_length=8000)
    csr_message: str = Field(min_length=1, max_length=8000)
    correlation_id: str | None = Field(default=None, max_length=128)


class HandoverRequest(BaseModel):
    """Signals a handover between bot and CSR in either direction."""

    model_config = ConfigDict(extra="forbid")

    thread_id: UUID
    direction: Literal["to_csr", "to_bot"]
    csr_id: str | None = Field(default=None, max_length=128)
    csr_name: str | None = Field(default=None, max_length=255)
    handover_note: str | None = Field(default=None, max_length=1000)
    correlation_id: str | None = Field(default=None, max_length=128)


class HandoverResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: UUID
    direction: str
    csr_handover_active: bool


class ChatFactsRequest(BaseModel):
    """Inject verified customer facts into thread state from an external source.

    Used when the CSR backend already holds name/email/phone (e.g. from a signup
    form or customer profile) so the bot never re-asks for data it can look up.
    Fields are applied only when the bot's current confidence for that field is
    lower than the incoming value — this ensures form-submitted data fills gaps
    without overwriting facts the bot already captured with equal reliability.
    """

    model_config = ConfigDict(extra="forbid")

    thread_id: UUID
    name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=64)
    source_label: str = Field(default="crm_sync", max_length=64)


class ChatFactsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: UUID
    fields_applied: list[str]


@router.post("/turn", response_model=ChatTurnResponse)
async def chat_turn(payload: ChatTurnRequest, request: Request) -> ChatTurnResponse:
    settings: Settings = request.app.state.settings
    principal = require_http_auth(request, settings)
    if payload.customer_id is None and principal.customer_id is not None:
        payload = payload.model_copy(update={"customer_id": principal.customer_id})

    limiter: RateLimiter = request.app.state.rate_limiter
    client_host = request.client.host if request.client else None
    decision = await limiter.check(
        f"http:chat_turn:{client_ip_from_scope(client_host)}",
        scope="http_chat_turn",
    )
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "rate_limited",
                "limit": decision.limit,
                "reset_after_seconds": decision.reset_after_seconds,
            },
            headers={"Retry-After": str(decision.reset_after_seconds)},
        )

    update: dict[str, object] = {
        "correlation_id": sanitize_correlation_id(payload.correlation_id)
    }
    # Landing-context fallback: if the widget did not pass landing_page on the turn, use
    # the browser's Referer (the embedding page URL, sent automatically). This lets the
    # service anchor the active service from the page even without any frontend change, so
    # an ambiguous first message on, e.g., the cover-design page is not mis-classified.
    if payload.landing_page is None:
        referer = request.headers.get("referer") or request.headers.get("referrer")
        if referer:
            update["landing_page"] = referer[:200]
    payload = payload.model_copy(update=update)
    service: ChatService = request.app.state.chat_service
    return await service.handle_turn(payload)


@router.post("/greet", response_model=ChatTurnResponse)
async def chat_greet(payload: ChatGreetRequest, request: Request) -> ChatTurnResponse:
    """Generate a proactive personalised first message when the chat widget opens."""
    settings: Settings = request.app.state.settings
    principal = require_http_auth(request, settings)
    if payload.customer_id is None and principal.customer_id is not None:
        payload = payload.model_copy(update={"customer_id": principal.customer_id})

    limiter: RateLimiter = request.app.state.rate_limiter
    client_host = request.client.host if request.client else None
    decision = await limiter.check(
        f"http:chat_greet:{client_ip_from_scope(client_host)}",
        scope="http_chat_turn",
    )
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "rate_limited",
                "limit": decision.limit,
                "reset_after_seconds": decision.reset_after_seconds,
            },
            headers={"Retry-After": str(decision.reset_after_seconds)},
        )

    payload = payload.model_copy(
        update={"correlation_id": sanitize_correlation_id(payload.correlation_id)}
    )
    service: ChatService = request.app.state.chat_service
    return await service.handle_greet(payload)


@router.post("/csr-turn", status_code=204)
async def chat_csr_turn(payload: CsrTurnRequest, request: Request) -> None:
    """Ingest a CSR message for context gathering — no bot response is generated."""
    settings = request.app.state.settings
    require_http_auth(request, settings)
    service: ChatService = request.app.state.chat_service
    await service.handle_csr_turn(payload)


@router.post("/handover", response_model=HandoverResponse)
async def chat_handover(payload: HandoverRequest, request: Request) -> HandoverResponse:
    """Signal a handover between bot and CSR."""
    settings = request.app.state.settings
    require_http_auth(request, settings)
    service: ChatService = request.app.state.chat_service
    return await service.handle_handover(payload)


@router.get("/debug/state/{thread_id}")
async def chat_debug_state(thread_id: UUID, request: Request) -> dict:
    """Return the thread's extracted state for debugging in the CSR dashboard.

    Shows: personal facts (name/email/phone/timezone), project facts
    (genre/word_count/manuscript_status), consultation state, and TRG active facts.
    Requires the same auth as other endpoints.
    """
    settings: Settings = request.app.state.settings
    require_http_auth(request, settings)
    service: ChatService = request.app.state.chat_service
    return await service.get_thread_debug_state(thread_id)


@router.post("/facts", response_model=ChatFactsResponse)
async def chat_inject_facts(payload: ChatFactsRequest, request: Request) -> ChatFactsResponse:
    """Inject verified customer facts (name/email/phone) from the CRM into thread state.

    Called by the Node.js backend when:
    - A customer fills out a signup form (so the bot never re-asks for known data).
    - A chat session starts and the customer's profile already has contact details.

    Only fills fields that are currently empty or have lower confidence in the
    bot's thread state — verified CRM data never overwrites a fact the bot
    collected with equal or higher confidence.
    """
    settings = request.app.state.settings
    require_http_auth(request, settings)
    service: ChatService = request.app.state.chat_service
    return await service.handle_inject_facts(payload)


@router.websocket("/ws/{thread_id}")
async def chat_ws(websocket: WebSocket, thread_id: UUID) -> None:
    settings: Settings = websocket.app.state.settings
    origin = websocket.headers.get("origin")

    if not is_origin_allowed(origin, settings):
        await websocket.close(code=1008, reason="Origin not allowed")
        return

    try:
        principal = authenticate_websocket(websocket, settings)
    except Exception:
        await websocket.close(code=1008, reason="Unauthorized")
        return

    await websocket.accept()
    service: ChatService = websocket.app.state.chat_service
    limiter: RateLimiter = websocket.app.state.rate_limiter
    client_host = websocket.client.host if websocket.client else None
    rate_key = f"ws:chat:{client_ip_from_scope(client_host)}"
    try:
        while True:
            data = await websocket.receive_json()
            decision = await limiter.check(rate_key, scope="ws_chat_message")
            if not decision.allowed:
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "rate_limited",
                        "message": "Too many messages. Please wait before sending another message.",
                        "retry_after_seconds": decision.reset_after_seconds,
                    }
                )
                continue
            message = data.get("message")
            raw_attachments = data.get("attachments")
            attachments = raw_attachments if isinstance(raw_attachments, list) else []
            message_str = message if isinstance(message, str) else ""
            # A turn is valid with either typed text OR an attachment. Previously an
            # attachment-only upload (no text) was rejected here, and attachments were
            # never forwarded to the service at all — so a bare file upload got no reply.
            if not message_str.strip() and not attachments:
                await websocket.send_json({"type": "error", "message": "message is required"})
                continue
            corr_id = data.get("correlation_id")
            raw_corr = corr_id if isinstance(corr_id, str) else None
            try:
                turn_request = ChatTurnRequest(
                    thread_id=thread_id,
                    customer_id=principal.customer_id,
                    message=message_str,
                    correlation_id=sanitize_correlation_id(raw_corr),
                    attachments=attachments,
                )
            except ValidationError:
                await websocket.send_json(
                    {"type": "error", "message": "a message or attachment is required"}
                )
                continue
            response = await service.handle_turn(turn_request)
            for bubble in response.bubbles:
                await websocket.send_json({"type": "typing_start"})
                await websocket.send_json({"type": "typing_stop"})
                await websocket.send_json(
                    {
                        "type": "message_bubble",
                        "payload": bubble.model_dump(mode="json"),
                    }
                )
            await websocket.send_json(
                {
                    "type": "turn_complete",
                    "thread_id": str(response.thread_id),
                    "language_status": response.language_status,
                }
            )
    except WebSocketDisconnect:
        return


def _chunk_text_for_stream(text: str, words_per_chunk: int = 4) -> list[str]:
    """Split already-validated text into small chunks for incremental delivery.

    Whitespace is preserved so that ``"".join(chunks) == text`` exactly — the client
    reassembles the byte-identical, quality-gated message.
    """
    if not text:
        return []
    tokens = re.findall(r"\S+\s*", text)
    return [
        "".join(tokens[i : i + words_per_chunk])
        for i in range(0, len(tokens), words_per_chunk)
    ]


@router.websocket("/ws/stream")
async def chat_websocket_stream(websocket: WebSocket) -> None:
    """WebSocket endpoint for streaming chat responses.

    When ``settings.response_streaming_enabled`` is True, response tokens are
    sent incrementally as they are generated.  When it is False (or the adapter
    does not support streaming), the full response is sent as a single payload.

    Message protocol (server → client):
      {"type": "stream_start"}                         — streaming turn begun
      {"type": "token",       "text": "<chunk>"}       — incremental token
      {"type": "stream_end",  "full_text": "<all>"}    — streaming turn done
      {"type": "response",    "data": <ChatTurnResponse as dict>}  — non-streaming
      {"type": "error",       "message": "<reason>"}   — request parsing error
      {"type": "error", "code": "rate_limited", ...}   — rate limit hit

    The streaming path runs the full handle_turn pipeline (safety checks, tool
    governance, quality gate, bubble formatting) and then streams the validated
    bubble text out in chunks, so only quality-gated output reaches the client.
    """
    settings: Settings = websocket.app.state.settings
    origin = websocket.headers.get("origin")

    if not is_origin_allowed(origin, settings):
        await websocket.close(code=1008, reason="Origin not allowed")
        return

    try:
        principal = authenticate_websocket(websocket, settings)
    except Exception:
        await websocket.close(code=1008, reason="Unauthorized")
        return

    await websocket.accept()
    service: ChatService = websocket.app.state.chat_service
    limiter: RateLimiter = websocket.app.state.rate_limiter
    client_host = websocket.client.host if websocket.client else None
    rate_key = f"ws:stream:{client_ip_from_scope(client_host)}"

    # Detect whether streaming is active.  Attribute access is tolerant of
    # Settings objects that do not yet carry response_streaming_enabled.
    streaming_enabled: bool = bool(getattr(settings, "response_streaming_enabled", False))

    try:
        while True:
            data = await websocket.receive_json()
            decision = await limiter.check(rate_key, scope="ws_chat_message")
            if not decision.allowed:
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "rate_limited",
                        "message": "Too many messages. Please wait before sending another message.",
                        "retry_after_seconds": decision.reset_after_seconds,
                    }
                )
                continue

            try:
                request = ChatTurnRequest.model_validate(data)
            except Exception as exc:
                await websocket.send_json({"type": "error", "message": str(exc)})
                continue

            # Inject principal customer_id if the caller did not supply one.
            if request.customer_id is None and principal.customer_id is not None:
                request = request.model_copy(update={"customer_id": principal.customer_id})

            if not streaming_enabled:
                # Non-streaming path: delegate to the standard handle_turn pipeline.
                response = await service.handle_turn(request)
                await websocket.send_json(
                    {"type": "response", "data": response.model_dump(mode="json")}
                )
            else:
                # Streaming delivery MUST NOT bypass the safety pipeline. Run the full
                # handle_turn (intent, tool governance, quality gate, bubble formatting),
                # then stream the ALREADY-VALIDATED bubble text out in chunks. The
                # customer only ever sees quality-gated output. (Chat 6211: the old
                # scaffold streamed raw, un-gated generator tokens straight to the
                # client, so a verbatim RAG/document bleed would reach the user before
                # any validation could run.)
                response = await service.handle_turn(request)
                await websocket.send_json({"type": "stream_start"})
                full_text = ""
                last_index = len(response.bubbles) - 1
                for bubble_index, bubble in enumerate(response.bubbles):
                    for chunk in _chunk_text_for_stream(bubble.text):
                        full_text += chunk
                        await websocket.send_json({"type": "token", "text": chunk})
                    if bubble_index != last_index:
                        # Preserve bubble boundaries as a paragraph break.
                        full_text += "\n\n"
                        await websocket.send_json({"type": "token", "text": "\n\n"})
                await websocket.send_json({"type": "stream_end", "full_text": full_text})
                # Structured payload for parity (thread_id, blocked, intent, etc.).
                await websocket.send_json(
                    {"type": "response", "data": response.model_dump(mode="json")}
                )
    except WebSocketDisconnect:
        return
