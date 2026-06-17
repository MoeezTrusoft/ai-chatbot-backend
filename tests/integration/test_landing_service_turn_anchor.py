"""Turn-level landing-service anchoring.

The proactive /greet flow seeds the active service from the landing page, but the
widget may skip /greet or call it without landing data. ChatTurnRequest therefore also
accepts landing_page / landing_keyword, and handle_turn anchors the service before intent
classification so an ambiguous first message — a bare genre/premise description on the
cover-design page ("cozy mystery with magic and food") — stays on cover design instead of
being mis-inferred as ghostwriting.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.components.response.schemas import ResponseDraft
from bookcraft.infra.config import Settings


class _FakeResponseGenerator:
    def __init__(self, text: str) -> None:
        self._draft = ResponseDraft(text=text, source="claude_sonnet")

    async def generate(self, **_kwargs: Any) -> ResponseDraft:
        return self._draft

    async def repair(self, *, bad_text: str, **_kwargs: Any) -> ResponseDraft:
        return ResponseDraft(text=bad_text, source="template_no_adapter_repair_unavailable")


def _turn(client: TestClient, message: str, **extra: object) -> dict[str, Any]:
    payload: dict[str, object] = {"message": message, **extra}
    response = client.post("/api/v1/chat/turn", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_genre_description_on_cover_page_stays_cover_not_ghostwriting() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))
    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = _FakeResponseGenerator(
            text="A cozy mystery with magic and food sounds wonderful — tell me more.",
        )
        resp = _turn(
            client,
            "cozy mystery with magic and food",
            landing_page="/book-cover-design/",
            landing_keyword="hire a book cover designer",
        )

    # The landing anchor + ContextArbiter inertia keep the turn on cover design even though
    # the bare genre description carries no explicit service and would otherwise be inferred
    # as ghostwriting.
    intent = resp["intent"]
    assert intent is not None
    assert intent["service_primary"] == "cover_design_illustration"


def test_referer_header_anchors_landing_service_without_explicit_field() -> None:
    """Backend-only fallback: when the widget sends no landing_page, the embedding page's
    Referer header is used to anchor the service — no frontend change required."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))
    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = _FakeResponseGenerator(
            text="A cozy mystery with magic and food sounds wonderful — tell me more.",
        )
        resp = client.post(
            "/api/v1/chat/turn",
            json={"message": "cozy mystery with magic and food"},
            headers={"Referer": "https://bookcraft.example/book-cover-design/"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

    assert body["intent"] is not None
    assert body["intent"]["service_primary"] == "cover_design_illustration"


def test_genre_description_without_landing_does_not_anchor_ghostwriting() -> None:
    """Companion to the above: with no landing context and no explicit service, the bare
    genre description must NOT durably anchor an inferred service (ghostwriting). The
    ContextArbiter clears the unsupported inference, leaving service_primary unset."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))
    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = _FakeResponseGenerator(
            text="A cozy mystery with magic and food sounds wonderful — tell me more.",
        )
        resp = _turn(client, "cozy mystery with magic and food")

    intent = resp["intent"]
    assert intent is not None
    assert intent["service_primary"] != "ghostwriting"
