from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.infra.config import Settings


def test_chat_remembers_cover_design_finished_children_fiction_context() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        first = client.post(
            "/api/v1/chat/turn",
            json={
                "message": (
                    "I am Kashif, I need a design on cover for my book. Can you help me with it?"
                )
            },
        )
        assert first.status_code == 200
        thread_id = first.json()["thread_id"]

        second = client.post(
            "/api/v1/chat/turn",
            json={"thread_id": thread_id, "message": "Its children book"},
        )
        assert second.status_code == 200

        third = client.post(
            "/api/v1/chat/turn",
            json={"thread_id": thread_id, "message": "I have finished my manuscript."},
        )
        assert third.status_code == 200

        fourth = client.post(
            "/api/v1/chat/turn",
            json={
                "thread_id": thread_id,
                "message": "Its fiction children book as I told you.",
            },
        )
        assert fourth.status_code == 200

    body = fourth.json()
    text = " ".join(bubble["text"] for bubble in body["bubbles"]).casefold()
    intent = body["intent"]

    assert "starting from scratch" not in text
    assert "do you have a draft" not in text
    assert "have a draft" not in text
    assert "manuscript stage" not in text
    assert "what genre" not in text
    assert "ghostwriting" not in text

    assert intent["service_primary"] == "cover_design_illustration"
