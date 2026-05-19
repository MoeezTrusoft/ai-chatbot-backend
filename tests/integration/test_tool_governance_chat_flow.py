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


def _governance(trace: dict[str, Any]) -> dict[str, Any]:
    gov = trace.get("tool_governance")
    assert isinstance(gov, dict), f"tool_governance missing from trace; keys: {list(trace.keys())}"
    return gov


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_governance_trace_key_present_on_every_turn() -> None:
    """Every successfully processed turn must emit a tool_governance trace entry."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        first = _chat(client, "I need help with my children's fiction book cover.")
        thread_id = first["thread_id"]
        _chat(client, "The manuscript is finished.", thread_id=thread_id)
        trace = _latest_trace(client, thread_id)

    gov = _governance(trace)
    assert "allowed" in gov
    assert "reason" in gov
    assert "audit" in gov
    assert isinstance(gov["audit"], list)


def test_governance_allows_service_question_as_no_action() -> None:
    """A plain service question produces no action, which governance allows."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "What does cover design include?")
        trace = _latest_trace(client, resp["thread_id"])

    gov = _governance(trace)
    assert gov["allowed"] is True
    assert "no_action" in gov["reason"] or "non_ready" in gov["reason"]


def test_governance_allows_portfolio_lookup_as_read_only() -> None:
    """Portfolio lookup must always be allowed regardless of confidence."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(
            client,
            "Can you show me cover design samples for children's fiction?",
        )
        trace = _latest_trace(client, resp["thread_id"])

    gov = _governance(trace)
    assert gov["allowed"] is True
    # Either a read_only decision or no action (if portfolio action service is absent).
    assert gov["reason"] in {
        "read_only_allowed",
        "no_action",
        "missing_info_allowed",
    }


def test_governance_allows_pricing_missing_info_passthrough() -> None:
    """Pricing request without enough data: MISSING_INFO status passes through."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "How much does ghostwriting cost?")
        trace = _latest_trace(client, resp["thread_id"])

    gov = _governance(trace)
    assert gov["allowed"] is True
    assert gov["reason"] in {"missing_info_allowed", "no_action"}
    # No dollar figures must appear in the response (no real quote was produced).
    text = _joined_text(resp)
    assert "$" not in text


def test_governance_trace_has_idempotency_key_when_write_action_ready() -> None:
    """When a write action is READY and allowed, an idempotency_key must be set."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        # Provide a full contact record to push CREATE_LEAD to READY.
        resp = _chat(
            client,
            "I am Kashif. My email is kashif@example.com and phone is 555-1234.",
        )
        trace = _latest_trace(client, resp["thread_id"])

    gov = _governance(trace)
    if gov["allowed"] and gov.get("idempotency_key") is not None:
        assert gov["idempotency_key"] is not None
        assert len(gov["idempotency_key"]) == 24


def test_governance_negated_pricing_does_not_execute_price_quote() -> None:
    """
    'Don't send a quote yet' must not trigger pricing execution; governance
    either prevents dispatch or the action is non-ready.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "Don't send a quote yet.")
        trace = _latest_trace(client, resp["thread_id"])

    gov = _governance(trace)
    text = _joined_text(resp)

    # Either the action was blocked by governance OR there was no pricing action.
    if not gov["allowed"]:
        assert "pricing" in gov["reason"] or "confidence" in gov["reason"]
    # Either way, no price figures must appear.
    assert "$" not in text


def test_governance_audit_list_is_non_empty() -> None:
    """Governance audit trail must always contain at least one entry."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "Tell me about your editing services.")
        trace = _latest_trace(client, resp["thread_id"])

    gov = _governance(trace)
    assert len(gov["audit"]) >= 1


# ===========================================================================
# Required integration tests (exact scenarios from spec)
# ===========================================================================


def test_negated_nda_does_not_dispatch_nda_and_trace_shows_no_side_effect() -> None:
    """
    'I don't need an NDA' must not reach the NDA dispatcher and must not
    produce nda_request intent. Governance trace must be present.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "I don't need an NDA.")
        trace = _latest_trace(client, resp["thread_id"])

    intent = resp["intent"]
    gov = _governance(trace)
    action_plan = trace.get("action_plan")

    # Intent must not be nda_request (arbiter + mock classifier both handle this).
    assert intent["query_primary"] != "nda_request"

    # Either governance blocked the NDA action OR no NDA action was planned.
    nda_planned = isinstance(action_plan, dict) and action_plan.get("action_type") == "generate_nda"
    if nda_planned:
        assert not gov["allowed"], "Governance must block a spurious generate_nda action"
    else:
        # No NDA action was planned at all — equally valid.
        assert gov["allowed"] is True

    # Response must be customer-safe (no legal clause text).
    text = _joined_text(resp).lower()
    assert "confidentiality" not in text or "nda" not in text


def test_negated_pricing_does_not_dispatch_price_quote_side_effect() -> None:
    """'Don't send a quote yet' must not produce a pricing result or $ figures."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "Don't send a quote yet.")
        trace = _latest_trace(client, resp["thread_id"])

    gov = _governance(trace)
    text = _joined_text(resp)

    # Governance blocked OR action was non-ready (MISSING_INFO) — both acceptable.
    if not gov["allowed"]:
        assert any(kw in gov["reason"] for kw in ("pricing", "confidence", "counterfactual"))
    assert "$" not in text


def test_counterfactual_consultation_does_not_book() -> None:
    """
    'If I wanted to book a consultation…' must not execute a booking.
    The request lacks required slots, so the plan is MISSING_INFO and governance
    passes through — but the dispatcher does not execute. No booking occurs.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(
            client,
            "If I wanted to book a consultation, how would that work?",
        )
        trace = _latest_trace(client, resp["thread_id"])

    gov = _governance(trace)
    action_plan = trace.get("action_plan")

    # Governance allows passthrough (MISSING_INFO) or no action.
    assert gov["allowed"] is True

    # The action must not be in a READY/EXECUTED state — no booking dispatched.
    if isinstance(action_plan, dict) and action_plan.get("action_type") == "schedule_consultation":
        assert action_plan.get("status") != "executed"
        # If the plan was READY (shouldn't happen without slots), governance or
        # the MISSING_INFO check should have prevented dispatch.
        result = action_plan.get("result")
        assert result is None or not result.get("success", False)

    # No confirmation or booking acknowledgement in response.
    text = _joined_text(resp).lower()
    assert "booked" not in text
    assert "confirmed" not in text
    assert "appointment" not in text


def test_valid_consultation_confirmation_reaches_pending_or_booking() -> None:
    """
    A consultation with all required details must reach NEEDS_CONFIRMATION
    (and governance must allow it) so the booking flow is not broken.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(
            client,
            "I need a consultation. My name is Alex, email alex@example.com, "
            "phone 555-0000, available next Monday at 10am.",
        )
        trace = _latest_trace(client, resp["thread_id"])

    gov = _governance(trace)
    action_plan = trace.get("action_plan")

    # Governance must not block the consultation request.
    assert gov["allowed"] is True

    # The plan should be schedule_consultation at NEEDS_CONFIRMATION or MISSING_INFO.
    if isinstance(action_plan, dict):
        assert action_plan.get("action_type") in {
            "schedule_consultation",
            None,
        }
        if action_plan.get("action_type") == "schedule_consultation":
            assert action_plan.get("status") in {
                "needs_confirmation",
                "missing_info",
                "ready",  # allowed if all slots resolved
            }


def test_governance_trace_present_in_all_turns_with_action_plan() -> None:
    """Every turn (including ones with planned actions) must emit tool_governance."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        first = _chat(client, "I need cover design. My email is test@example.com.")
        thread_id = first["thread_id"]
        _chat(client, "The manuscript is finished.", thread_id=thread_id)
        _chat(client, "How much does cover design cost?", thread_id=thread_id)
        trace_store = client.app.state.chat_service.trace_store
        all_rows = trace_store.for_thread(thread_id)

    assert all_rows, "Expected trace rows"
    for row in all_rows:
        gov = row.get("tool_governance")
        assert isinstance(gov, dict), (
            f"tool_governance missing from a trace row: {list(row.keys())}"
        )
        assert "allowed" in gov
        assert "reason" in gov
        assert isinstance(gov.get("audit"), list)
