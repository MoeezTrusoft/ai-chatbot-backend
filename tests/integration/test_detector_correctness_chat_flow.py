from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.infra.config import Settings


def test_not_published_does_not_become_published() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        body = _chat(
            client,
            "I have not published it yet, but I need help with cover design.",
        )
        memory = _thread_memory(client, body)

    text = _joined_text(body).casefold()
    intent = body["intent"]

    assert "already published" not in text
    assert "book is published" not in text
    assert intent["service_primary"] == "cover_design_illustration"
    assert intent["funnel_stage"] != "published"
    assert memory.state.project.manuscript_status.value != "published"


def test_nda_negation_does_not_trigger_nda_action() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        body = _chat(
            client,
            "I don't need an NDA right now. I just want to discuss editing.",
        )
        memory = _thread_memory(client, body)

    text = _joined_text(body).casefold()
    action = _sales_action(memory)

    assert action["action_type"] != "generate_nda"
    assert memory.state.sales_actions.documents.nda.requested is False
    assert "approved template" not in text
    assert re.search(r"\bnda\b", text) is None
    assert body["intent"]["service_primary"] == "editing_proofreading"


def test_agreement_negation_does_not_trigger_agreement_action() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        body = _chat(
            client,
            "I am not ready for agreement. I just want pricing information.",
        )
        memory = _thread_memory(client, body)

    action = _sales_action(memory)

    assert action["action_type"] != "generate_agreement"
    assert memory.state.sales_actions.documents.agreement.requested is False
    assert body["intent"]["query_primary"] == "pricing_question"


def test_quote_negation_does_not_trigger_pricing() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        body = _chat(
            client,
            "Don't send a quote yet. I only want to know how cover design works.",
        )
        memory = _thread_memory(client, body)

    action = _sales_action(memory)

    assert action["action_type"] != "price_quote"
    assert memory.state.sales_actions.pricing.requested is False
    assert body["intent"]["service_primary"] == "cover_design_illustration"


def test_real_pricing_still_works() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        body = _chat(
            client,
            "Can you give me a quote for ghostwriting a 50000 word fantasy novel?",
        )
        memory = _thread_memory(client, body)

    action = _sales_action(memory)

    assert body["intent"]["query_primary"] == "pricing_question"
    assert body["intent"]["service_primary"] == "ghostwriting"
    assert action["action_type"] == "price_quote"
    assert action["status"] in {"missing_info", "needs_confirmation", "ready"}


def test_children_fiction_context_still_works() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        first = _chat(
            client,
            "I am Kashif, I need a design on cover for my book. Can you help me with it?",
        )
        thread_id = first["thread_id"]

        _chat(client, "Its children book", thread_id=thread_id)
        _chat(client, "I have finished my manuscript.", thread_id=thread_id)
        body = _chat(
            client,
            "Its fiction children book as I told you.",
            thread_id=thread_id,
        )

    text = _joined_text(body).casefold()
    intent = body["intent"]

    assert intent["service_primary"] == "cover_design_illustration"
    assert "ghostwriting" not in text
    assert "starting from scratch" not in text
    assert "do you have a draft" not in text
    assert "have a draft" not in text
    assert "manuscript stage" not in text
    assert "what genre" not in text


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


def _thread_memory(client: TestClient, body: dict[str, Any]) -> Any:
    return client.app.state.chat_service.threads[UUID(body["thread_id"])]


def _sales_action(memory: Any) -> dict[str, Any]:
    for event in memory.events:
        if event["event_type"] == "sales_action.planned":
            payload = event["payload"]
            assert isinstance(payload, dict)
            return payload
    raise AssertionError("sales_action.planned event was not recorded")
