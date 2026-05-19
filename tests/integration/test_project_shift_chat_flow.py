from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.infra.config import Settings


@pytest.fixture()
def client() -> TestClient:
    app = create_app(Settings(app_env="test"))
    return TestClient(app)


def _chat(
    client: TestClient,
    message: str,
    *,
    thread_id: UUID | str | None = None,
) -> dict[str, Any]:
    payload: dict[str, object] = {"message": message}
    if thread_id is not None:
        payload["thread_id"] = str(thread_id)
    resp = client.post("/api/v1/chat/turn", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _trace(client: TestClient, thread_id: str) -> dict[str, Any]:
    ts = client.app.state.chat_service.trace_store
    rows = ts.for_thread(thread_id)
    assert rows, f"No trace for thread {thread_id}"
    return rows[0]


def _project_ctx(trace: dict[str, Any]) -> dict[str, Any]:
    return trace.get("project_context") or {}


# ---------------------------------------------------------------------------
# 1. New-project shift: cover design → different book for editing
# ---------------------------------------------------------------------------


def test_new_project_shift_cover_to_editing(client: TestClient) -> None:
    r1 = _chat(client, "I need help with cover design for my fantasy novel.")
    thread_id = str(r1["thread_id"])

    t1 = _trace(client, thread_id)
    pc1 = _project_ctx(t1)
    first_project_id = pc1.get("active_project_id")
    assert first_project_id, "First turn must create a project ID"
    assert pc1.get("decision", {}).get("event") == "same_project"

    r2 = _chat(
        client,
        "I have another book that needs editing and proofreading.",
        thread_id=thread_id,
    )
    t2 = _trace(client, str(r2["thread_id"]))
    pc2 = _project_ctx(t2)

    assert pc2.get("decision", {}).get("event") == "new_project", (
        f"Expected new_project, got {pc2.get('decision', {}).get('event')}"
    )
    second_project_id = pc2.get("active_project_id")
    assert second_project_id != first_project_id, "active_project_id must change on new project"
    assert pc2.get("previous_project_id") == first_project_id

    # Response must not reference cover-design details from project 1.
    text2 = " ".join(b.get("text", "") for b in r2.get("bubbles", []))
    assert "cover style" not in text2.lower(), (
        "Response for new project must not reference old cover-style scoping"
    )

    # Context pack for turn 2 should reflect the new project's intent-derived service.
    cp2 = t2.get("context_pack") or {}
    service = cp2.get("active_service")
    assert service in {
        "editing_proofreading",
        None,  # may be None if service wasn't yet confirmed
    }, f"Unexpected active_service: {service}"


# ---------------------------------------------------------------------------
# 2. Same-project service addition keeps project ID and is additive
# ---------------------------------------------------------------------------


def test_same_project_service_bundle_does_not_reset_project(client: TestClient) -> None:
    r1 = _chat(client, "I need interior formatting for my romance novel.")
    thread_id = str(r1["thread_id"])
    first_id = _project_ctx(_trace(client, thread_id)).get("active_project_id")

    r2 = _chat(
        client,
        "I also need KDP publishing for the same book.",
        thread_id=thread_id,
    )
    pc2 = _project_ctx(_trace(client, str(r2["thread_id"])))

    assert pc2.get("decision", {}).get("event") == "same_project_service_addition", (
        f"Expected same_project_service_addition, got {pc2.get('decision', {}).get('event')}"
    )
    assert pc2.get("active_project_id") == first_id, (
        "active_project_id must remain unchanged for service addition"
    )


# ---------------------------------------------------------------------------
# 3. Ambiguous "now I need editing too" triggers clarification plan
# ---------------------------------------------------------------------------


def test_ambiguous_project_reference_triggers_clarification_plan(client: TestClient) -> None:
    r1 = _chat(client, "I need ghostwriting for my fantasy novel.")
    thread_id = str(r1["thread_id"])

    r2 = _chat(client, "Now I need editing too.", thread_id=thread_id)
    t2 = _trace(client, str(r2["thread_id"]))
    pc2 = _project_ctx(t2)
    event = pc2.get("decision", {}).get("event")

    # Either ambiguous or same_project_service_addition (when strong evidence exists).
    assert event in {
        "ambiguous_project_reference",
        "same_project_service_addition",
    }, f"Unexpected event: {event}"

    if event == "ambiguous_project_reference":
        rp = t2.get("response_plan") or {}
        assert rp.get("primary_goal") == "clarify_project_scope", (
            "Ambiguous project must yield clarify_project_scope plan"
        )
        assert rp.get("next_question") == "same_or_new_project"


# ---------------------------------------------------------------------------
# 4. Previous-book switch restores older project
# ---------------------------------------------------------------------------


def test_previous_book_switch_records_correct_ids(client: TestClient) -> None:
    r1 = _chat(client, "I need cover design for my thriller novel.")
    thread_id = str(r1["thread_id"])
    p1_id = _project_ctx(_trace(client, thread_id)).get("active_project_id")

    r2 = _chat(
        client,
        "I also have another book — a sci-fi novel that needs editing.",
        thread_id=thread_id,
    )
    p2_id = _project_ctx(_trace(client, str(r2["thread_id"]))).get("active_project_id")
    assert p2_id != p1_id

    r3 = _chat(
        client,
        "Actually, let me go back to the previous book, the thriller.",
        thread_id=thread_id,
    )
    t3 = _trace(client, str(r3["thread_id"]))
    pc3 = _project_ctx(t3)
    event3 = pc3.get("decision", {}).get("event")
    assert event3 in {"project_switch", "same_project"}, f"Unexpected event: {event3}"

    # The restored project ID must be from the first project.
    if event3 == "project_switch":
        assert pc3.get("active_project_id") == p1_id
        assert pc3.get("previous_project_id") == p2_id
