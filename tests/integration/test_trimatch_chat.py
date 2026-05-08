from uuid import UUID

from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.infra.config import Settings


def test_chat_records_trimatch_vote_without_changing_final_haiku_intent() -> None:
    app = create_app(Settings(app_env="test"))
    response = TestClient(app).post(
        "/api/v1/chat/turn",
        json={"message": "pricing quote how much does ghostwriting cost"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["intent"]["query_primary"] == "pricing_question"
    thread_id = body["thread_id"]
    events = app.state.chat_service.threads[UUID(thread_id)].events
    trimatch_events = [event for event in events if event["event_type"] == "trimatch.voted"]

    assert trimatch_events
    payload = trimatch_events[0]["payload"]
    assert payload["query_primary"] == "pricing_question"
    assert payload["service_primary"] == "ghostwriting"


def test_trimatch_funnel_stage_does_not_mutate_thread_sales_stage() -> None:
    app = create_app(Settings(app_env="test"))
    response = TestClient(app).post(
        "/api/v1/chat/turn",
        json={"message": "quote requested send proposal"},
    )

    assert response.status_code == 200
    thread_id = response.json()["thread_id"]
    memory = app.state.chat_service.threads[UUID(thread_id)]
    trimatch_events = [event for event in memory.events if event["event_type"] == "trimatch.voted"]

    assert trimatch_events[0]["payload"]["funnel_stage"] == "quote_requested"
    assert "funnel_stage" in trimatch_events[0]["payload"]["shadow_only_dimensions"]
    assert memory.state.sales_stage.value is None
