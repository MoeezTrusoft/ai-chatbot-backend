from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.infra.config import Settings


def test_context_pack_is_recorded_and_guides_response() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        first = _chat(
            client,
            "I am Kashif, I need a design on cover for my book can you help me with it?",
        )
        thread_id = first["thread_id"]
        _chat(client, "Its children book", thread_id=thread_id)
        _chat(client, "I have finished my manuscript.", thread_id=thread_id)
        fourth = _chat(
            client,
            "Its fiction children book as I told you.",
            thread_id=thread_id,
        )
        pack = _latest_context_pack(client, thread_id)

    text = _joined_text(fourth).casefold()
    intent = fourth["intent"]

    # ContextPack recorded in trace with correct derived fields.
    assert pack["active_service"] == "cover_design_illustration"
    assert pack["active_genre"] in {"children's book", "children's fiction"}
    assert pack["manuscript_status"] == "completed_draft"
    assert "genre" in pack["forbidden_reasks"]
    assert (
        "manuscript_stage" in pack["forbidden_reasks"] or "draft status" in pack["forbidden_reasks"]
    )
    assert "word_or_page_count" in pack["missing_facts"] or "cover_style" in pack["missing_facts"]

    # Response must not re-ask facts already in the ContextPack.
    assert "ghostwriting" not in text
    assert "what genre" not in text
    assert "have a draft" not in text
    assert "starting from scratch" not in text
    assert "manuscript stage" not in text

    # Intent stabilized to cover design; no spurious secondary services.
    assert intent["service_primary"] == "cover_design_illustration"
    assert intent["service_secondary"] == []


def _chat(
    client: TestClient,
    message: str,
    *,
    thread_id: object | None = None,
) -> dict[str, Any]:
    payload: dict[str, object] = {"message": message}
    if thread_id is not None:
        payload["thread_id"] = str(thread_id)
    response = client.post("/api/v1/chat/turn", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _joined_text(body: dict[str, Any]) -> str:
    return " ".join(str(bubble["text"]) for bubble in body["bubbles"])


def _latest_context_pack(client: TestClient, thread_id: str) -> dict[str, Any]:
    trace_store = client.app.state.chat_service.trace_store
    rows = trace_store.for_thread(thread_id)
    assert rows, f"No trace rows found for thread {thread_id}"
    pack = rows[0].get("context_pack")
    assert isinstance(pack, dict), f"Expected context_pack dict, got {type(pack)}"
    return pack
