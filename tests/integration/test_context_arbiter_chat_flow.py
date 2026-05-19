from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.infra.config import Settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _latest_trace(client: TestClient, thread_id: str) -> dict[str, Any]:
    trace_store = client.app.state.chat_service.trace_store
    rows = trace_store.for_thread(thread_id)
    assert rows, f"No trace rows found for thread {thread_id}"
    return rows[0]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_context_arbiter_trace_entry_is_present() -> None:
    """Every turn must record a context_arbiter key in the live trace."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        first = _chat(client, "I need help with my book cover design.")
        thread_id = first["thread_id"]
        _chat(client, "It is a children's fiction book.", thread_id=thread_id)
        trace = _latest_trace(client, thread_id)

    assert "context_arbiter" in trace, "context_arbiter key missing from live trace"
    arbiter = trace["context_arbiter"]
    assert "corrections" in arbiter
    assert "audit" in arbiter
    assert "intent_before" in arbiter
    assert "intent_after" in arbiter


def test_service_inertia_blocks_drift_across_turns() -> None:
    """
    After cover design is established, a follow-up turn with no explicit
    service cue must remain on cover_design_illustration.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        first = _chat(client, "I need a cover design for my children's fiction book.")
        thread_id = first["thread_id"]
        # No service keyword — should stay on cover design.
        second = _chat(
            client,
            "I have finished the manuscript, what should I do next?",
            thread_id=thread_id,
        )
        trace = _latest_trace(client, thread_id)

    assert second["intent"]["service_primary"] == "cover_design_illustration"
    arbiter = trace["context_arbiter"]
    # Inertia fired: a correction starting with "service_inertia" must be present.
    assert any("inertia" in c for c in arbiter["corrections"])


def test_explicit_service_switch_updates_active_service() -> None:
    """
    After cover design is established, an explicit "instead" switch must
    update the active service to the new one.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        first = _chat(client, "I need a cover design for my book.")
        thread_id = first["thread_id"]
        # Explicit switch — arbiter should allow it.
        switched = _chat(
            client,
            "Actually I need editing and proofreading instead.",
            thread_id=thread_id,
        )
        trace = _latest_trace(client, thread_id)

    intent = switched["intent"]
    assert intent["service_primary"] == "editing_proofreading"
    arbiter = trace["context_arbiter"]
    assert any("explicit_service_switch" in a for a in arbiter["audit"])


def test_arbiter_audit_notes_known_facts_after_genre_established() -> None:
    """
    Once genre is known, the arbiter audit trail must include a known_facts
    entry so downstream components can see it was logged.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        first = _chat(client, "I need cover design for my children's fiction novel.")
        thread_id = first["thread_id"]
        _chat(client, "The genre is children's fiction.", thread_id=thread_id)
        third = _chat(client, "What else do you need from me?", thread_id=thread_id)
        trace = _latest_trace(client, thread_id)

    del third
    arbiter = trace["context_arbiter"]
    assert any("known_facts" in a for a in arbiter["audit"])


def test_negated_pricing_does_not_trigger_price_quote() -> None:
    """
    'Don't send a quote yet' must not produce a pricing_question intent or
    any price figures in the response.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "Don't send a quote yet.")
        thread_id = resp["thread_id"]
        trace = _latest_trace(client, thread_id)

    intent = resp["intent"]
    text = _joined_text(resp)

    assert intent["query_primary"] != "pricing_question", (
        f"Expected non-pricing intent, got {intent['query_primary']}"
    )
    assert "$" not in text, "Response must not contain a price figure"
    # Arbiter trace must be present and structurally valid.
    assert "context_arbiter" in trace
    arbiter = trace["context_arbiter"]
    assert "corrections" in arbiter
    assert "audit" in arbiter


def test_negated_nda_does_not_trigger_nda_action() -> None:
    """
    'I don't need an NDA' must not produce an nda_request intent or a
    generate_nda action in the trace.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "I don't need an NDA.")
        thread_id = resp["thread_id"]
        trace = _latest_trace(client, thread_id)

    intent = resp["intent"]

    assert intent["query_primary"] != "nda_request", (
        f"Expected non-NDA intent, got {intent['query_primary']}"
    )
    action_plan = trace.get("action_plan")
    if action_plan is not None:
        assert action_plan.get("action_type") != "generate_nda", (
            "generate_nda action must not be planned when NDA is negated"
        )
    assert "context_arbiter" in trace
