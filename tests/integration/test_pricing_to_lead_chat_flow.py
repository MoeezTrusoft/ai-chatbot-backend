from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.infra.config import Settings


def _chat(
    client: TestClient,
    message: str,
    *,
    thread_id: object | None = None,
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"message": message}
    if thread_id is not None:
        payload["thread_id"] = str(thread_id)
    if attachments:
        payload["attachments"] = attachments
    r = client.post("/api/v1/chat/turn", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _trace(client: TestClient, thread_id: str) -> dict[str, Any]:
    rows = client.app.state.chat_service.trace_store.for_thread(thread_id)
    assert rows
    return rows[0]


def _text(body: dict[str, Any]) -> str:
    return " ".join(b.get("text", "") for b in body.get("bubbles", [])).lower()


def test_pricing_request_does_not_invent_price() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))
    with TestClient(app) as client:
        body = _chat(client, "How much does ghostwriting cost?")

    txt = _text(body)
    assert "$" not in txt
    assert "usd" not in txt


def test_pricing_request_asks_contact_or_consultation_instead_of_many_quote_slots() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))
    with TestClient(app) as client:
        body = _chat(client, "How much does ghostwriting cost?")
        t = _trace(client, body["thread_id"])

    ro = t.get("lead_objective") or {}
    assert ro.get("stop_discovery") is True
    assert ro.get("objective_move") in {"ask_contact", "offer_consultation"}


def test_timeline_request_does_not_invent_timeline_and_moves_toward_contact() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))
    with TestClient(app) as client:
        body = _chat(client, "How long will ghostwriting take?")
        t = _trace(client, body["thread_id"])

    txt = _text(body)
    assert "within 2" not in txt
    assert (t.get("lead_objective") or {}).get("stop_discovery") is True


def test_samples_request_can_move_toward_lead_capture_after_portfolio_fallback() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))
    with TestClient(app) as client:
        body = _chat(client, "Show me ghostwriting samples")
        t = _trace(client, body["thread_id"])

    assert (t.get("lead_objective") or {}).get("objective_move") in {
        "ask_contact",
        "continue_light_discovery",
    }


def test_attachment_assessment_moves_toward_specialist_handoff_contact() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))
    with TestClient(app) as client:
        body = _chat(
            client,
            "I need editing for this manuscript",
            attachments=[{"filename": "my_novel_draft.docx"}],
        )
        t = _trace(client, body["thread_id"])

    assert (t.get("attachment_intake") or {}).get("assessment_type") is not None
    assert (t.get("lead_objective") or {}).get("objective_move") in {
        "ask_contact",
        "handoff_to_specialist",
    }
