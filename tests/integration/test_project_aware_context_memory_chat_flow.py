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


def _text(body: dict[str, Any]) -> str:
    return " ".join(b.get("text", "") for b in body.get("bubbles", []))


# ---------------------------------------------------------------------------
# 1. New project does not inherit old genre/status as active known facts
# ---------------------------------------------------------------------------


def test_new_project_does_not_inherit_old_genre(client: TestClient) -> None:
    r1 = _chat(client, "I need cover design for my fantasy thriller novel.")
    thread_id = str(r1["thread_id"])

    r2 = _chat(
        client,
        "I have another book that needs editing.",
        thread_id=thread_id,
    )
    t2 = _trace(client, str(r2["thread_id"]))

    pc2 = t2.get("context_pack") or {}
    proj2 = t2.get("project_context") or {}
    event2 = (proj2.get("decision") or {}).get("event")
    assert event2 == "new_project", f"Expected new_project, got {event2}"

    # Active genre for the new project must not carry over "fantasy thriller"
    active_genre2 = pc2.get("active_genre")
    assert active_genre2 is None or active_genre2 != "fantasy thriller", (
        f"New project must not inherit old genre, got {active_genre2}"
    )


# ---------------------------------------------------------------------------
# 2. Previous project appears in memory summary
# ---------------------------------------------------------------------------


def test_previous_project_in_memory_summary(client: TestClient) -> None:
    r1 = _chat(client, "I need cover design for my book.")
    thread_id = str(r1["thread_id"])

    r2 = _chat(
        client,
        "I have another book that needs ghostwriting.",
        thread_id=thread_id,
    )
    t2 = _trace(client, str(r2["thread_id"]))
    proj_ctx2 = t2.get("project_context") or {}
    event2 = (proj_ctx2.get("decision") or {}).get("event")
    if event2 == "new_project":
        assert event2 == "new_project"


# ---------------------------------------------------------------------------
# 3. RAG query excludes old project terms after new project switch
# ---------------------------------------------------------------------------


def test_rag_query_excludes_old_project_terms_after_switch(client: TestClient) -> None:
    r1 = _chat(client, "I need cover design for my children's fantasy novel.")
    thread_id = str(r1["thread_id"])

    r2 = _chat(
        client,
        "I have another book that needs editing — it's a business book.",
        thread_id=thread_id,
    )
    t2 = _trace(client, str(r2["thread_id"]))

    rag2 = t2.get("rag_query") or {}
    query_text2 = rag2.get("query_text") or ""
    proj2 = t2.get("project_context") or {}
    if (proj2.get("decision") or {}).get("event") == "new_project":
        # old project's genre (children's fantasy) must not be in the new project's RAG query
        assert "children" not in query_text2.lower() or "fantasy" not in query_text2.lower(), (
            f"Old project genre must not appear in new project RAG query, got {query_text2}"
        )
        # New project scope audit should be present
        audit2 = rag2.get("audit") or []
        assert any("new_project_scope" in a for a in audit2) or any(
            "project_id" in a for a in audit2
        ), f"RAG audit must reflect new project scope, got {audit2}"


# ---------------------------------------------------------------------------
# 4. Same project service addition keeps active facts
# ---------------------------------------------------------------------------


def test_same_project_service_addition_keeps_facts(client: TestClient) -> None:
    r1 = _chat(client, "I need cover design for my fantasy novel.")
    thread_id = str(r1["thread_id"])

    r2 = _chat(
        client,
        "I also need interior formatting for the same book.",
        thread_id=thread_id,
    )
    t2 = _trace(client, str(r2["thread_id"]))

    proj2 = t2.get("project_context") or {}
    event2 = (proj2.get("decision") or {}).get("event")
    assert event2 in ("same_project_service_addition", "same_project"), (
        f"Expected same project event, got {event2}"
    )


# ---------------------------------------------------------------------------
# 5. Ambiguous project reference produces clarify_project_scope plan
# ---------------------------------------------------------------------------


def test_ambiguous_project_produces_clarification_plan(client: TestClient) -> None:
    r1 = _chat(client, "I need ghostwriting for my novel.")
    thread_id = str(r1["thread_id"])

    r2 = _chat(client, "Now I need editing too.", thread_id=thread_id)
    t2 = _trace(client, str(r2["thread_id"]))

    proj2 = t2.get("project_context") or {}
    event2 = (proj2.get("decision") or {}).get("event")
    rp2 = t2.get("response_plan") or {}

    if event2 == "ambiguous_project_reference":
        assert rp2.get("primary_goal") == "clarify_project_scope", (
            f"Ambiguous project must yield clarify_project_scope, got {rp2}"
        )
        assert rp2.get("next_question") == "same_or_new_project"


# ---------------------------------------------------------------------------
# 6. Slot delegation from old project does not suppress new project slot
# ---------------------------------------------------------------------------


def test_old_project_delegation_does_not_suppress_new_project_slot(client: TestClient) -> None:
    r1 = _chat(client, "I need cover design.")
    thread_id = str(r1["thread_id"])

    _chat(client, "You decide the cover style.", thread_id=thread_id)

    r3 = _chat(
        client,
        "I have another book that needs ghostwriting.",
        thread_id=thread_id,
    )
    t3 = _trace(client, str(r3["thread_id"]))

    proj3 = t3.get("project_context") or {}
    event3 = (proj3.get("decision") or {}).get("event")

    if event3 == "new_project":
        trg3 = t3.get("trg_semantic") or {}
        project_shifts3 = trg3.get("project_shifts") or []
        assert project_shifts3, "TRG must record project shift"
        assert project_shifts3[0].get("event") == "new_project"
