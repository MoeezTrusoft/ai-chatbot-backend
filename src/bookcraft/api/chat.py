from uuid import UUID

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field

from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.response.schemas import FormattedBubble
from bookcraft.services.chat import ChatService

from starlette.websockets import WebSocketState

from bookcraft.api.security import is_origin_allowed
from bookcraft.infra.config import Settings

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


class ChatTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: UUID | None = None
    customer_id: UUID | None = None
    message: str = Field(min_length=1, max_length=8000)
    correlation_id: str | None = None


class ChatTurnResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: UUID
    bubbles: list[FormattedBubble]
    intent: IntentVote | None
    language_status: str
    debug_event_ids: list[str] = Field(default_factory=list)


@router.post("/turn", response_model=ChatTurnResponse)
async def chat_turn(payload: ChatTurnRequest, request: Request) -> ChatTurnResponse:
    service: ChatService = request.app.state.chat_service
    return await service.handle_turn(payload)


@router.websocket("/ws/{thread_id}")
async def chat_ws(websocket: WebSocket, thread_id: UUID) -> None:
    settings: Settings = websocket.app.state.settings
    origin = websocket.headers.get("origin")

    if not is_origin_allowed(origin, settings):
        await websocket.close(code=1008, reason="Origin not allowed")
        return

    await websocket.accept()
    service: ChatService = websocket.app.state.chat_service
    try:
        while True:
            data = await websocket.receive_json()
            message = data.get("message")
            if not isinstance(message, str) or not message.strip():
                await websocket.send_json({"type": "error", "message": "message is required"})
                continue
            response = await service.handle_turn(
                ChatTurnRequest(
                    thread_id=thread_id,
                    message=message,
                    correlation_id=data.get("correlation_id")
                    if isinstance(data.get("correlation_id"), str)
                    else None,
                )
            )
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

