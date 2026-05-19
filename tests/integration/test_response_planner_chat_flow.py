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


def _response_plan(trace: dict[str, Any]) -> dict[str, Any]:
    rp = trace.get("response_plan")
    assert isinstance(rp, dict), f"response_plan missing from trace; keys: {list(trace.keys())}"
    return rp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_response_plan_present_in_live_trace() -> None:
    """Every processed turn must emit a response_plan entry in the live trace."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "I need a cover design for my children's fiction book.")
        trace = _latest_trace(client, resp["thread_id"])

    rp = _response_plan(trace)
    assert "primary_goal" in rp
    assert "acknowledge_facts" in rp
    assert "must_not_mention" in rp
    assert "next_question" in rp
    assert "audit" in rp
    assert isinstance(rp["audit"], list)
    assert rp["max_questions"] == 1
    assert rp["tone"] == "warm_consultative"


def test_response_plan_cover_design_goal_and_next_question() -> None:
    """
    After cover design is established, the plan's primary_goal must be
    cover_design_scoping and next_question should guide toward cover_style.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        first = _chat(client, "I need a cover design for my children's fiction book.")
        thread_id = first["thread_id"]
        _chat(client, "I have finished the manuscript.", thread_id=thread_id)
        _chat(
            client,
            "Its fiction children book as I told you.",
            thread_id=thread_id,
        )
        trace = _latest_trace(client, thread_id)

    rp = _response_plan(trace)
    assert rp["primary_goal"] == "cover_design_scoping"
    # Next question should point toward cover_style since genre/stage are known.
    assert rp["next_question"] in {"cover_style", "word_or_page_count", None}


def test_response_plan_known_genre_not_re_asked() -> None:
    """
    Once genre is established, it must appear in forbidden_reasks (and
    therefore in must_not_mention) and must NOT appear as next_question.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        first = _chat(client, "I need cover design. The genre is children's fiction.")
        thread_id = first["thread_id"]
        resp = _chat(client, "What else do you need?", thread_id=thread_id)
        trace = _latest_trace(client, thread_id)

    rp = _response_plan(trace)
    # Genre must appear in must_not_mention.
    assert "genre" in rp["must_not_mention"]
    # next_question must not be genre.
    assert rp["next_question"] != "genre"
    # Response text must not ask for genre.
    text = _joined_text(resp).casefold()
    assert "what genre" not in text


def test_response_plan_internal_terms_in_must_not_mention() -> None:
    """The plan must always suppress internal implementation terms."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "Tell me about your editing services.")
        trace = _latest_trace(client, resp["thread_id"])

    rp = _response_plan(trace)
    assert "backend" in rp["must_not_mention"]
    assert "RAG" in rp["must_not_mention"]


def test_response_plan_governance_blocked_shows_safe_blocked_action() -> None:
    """
    When governance blocks an action, the plan's primary_goal is safe_blocked_action
    and customer_safe_tool_summary carries the safe blocked message.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "I don't need an NDA.")
        trace = _latest_trace(client, resp["thread_id"])

    rp = _response_plan(trace)
    gov = trace.get("tool_governance", {})

    if not gov.get("allowed", True):
        # Governance blocked the action — plan should reflect it.
        assert rp["primary_goal"] in {"safe_blocked_action", "clarify_intent"}
        assert rp["customer_safe_tool_summary"] is not None
    else:
        # No action planned / allowed — plan should be sensible.
        assert rp["primary_goal"] in {
            "continue_discovery",
            "document_scoping",
            "safe_blocked_action",
        }


def test_response_plan_pricing_scoping_goal() -> None:
    """Pricing intent must produce pricing_scoping primary_goal."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "How much does ghostwriting cost?")
        trace = _latest_trace(client, resp["thread_id"])

    rp = _response_plan(trace)
    assert rp["primary_goal"] == "pricing_scoping"
    # Should ask for a missing pricing slot, not a random question.
    if rp["next_question"] is not None:
        assert rp["next_question"] in {
            "genre",
            "manuscript_stage",
            "word_or_page_count",
            "deadline",
            "services",
        }


def test_response_plan_acknowledge_facts_filled_after_genre_established() -> None:
    """After genre is established, acknowledge_facts must include it."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        first = _chat(client, "I need cover design for my children's fiction novel.")
        thread_id = first["thread_id"]
        resp = _chat(client, "What do you need next?", thread_id=thread_id)
        trace = _latest_trace(client, thread_id)

    rp = _response_plan(trace)
    del resp
    # At least the active_service should be acknowledged.
    assert rp["acknowledge_facts"]
    assert any("cover_design_illustration" in f for f in rp["acknowledge_facts"])


# ===========================================================================
# Required integration tests (four scenarios from spec)
# ===========================================================================


def test_kashif_cover_design_flow() -> None:
    """
    Kashif cover-design session:
    - response_plan appears in every trace row.
    - primary_goal == cover_design_scoping after service is established.
    - next_question points toward cover_style or word_or_page_count.
    - Final response never re-asks for genre or manuscript stage.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        first = _chat(
            client,
            "I am Kashif, I need a design on cover for my book. Can you help me?",
        )
        thread_id = first["thread_id"]
        _chat(client, "Its children book", thread_id=thread_id)
        _chat(client, "I have finished my manuscript.", thread_id=thread_id)
        fourth = _chat(
            client,
            "Its fiction children book as I told you.",
            thread_id=thread_id,
        )
        trace = _latest_trace(client, thread_id)

    rp = _response_plan(trace)
    text = _joined_text(fourth).casefold()

    # response_plan present and structurally valid.
    assert isinstance(rp, dict)
    assert rp["max_questions"] == 1

    # Goal and next-question match cover-design scoping.
    assert rp["primary_goal"] == "cover_design_scoping"
    assert rp["next_question"] in {"cover_style", "word_or_page_count", None}

    # Final response must not re-ask known facts.
    assert "what genre" not in text
    assert "manuscript stage" not in text
    assert "starting from scratch" not in text
    assert "have a draft" not in text


def test_negated_nda_does_not_start_nda_flow() -> None:
    """
    'I don't need an NDA' must not trigger NDA intent or actions.
    response_plan primary_goal must be safe_blocked_action or a benign goal.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "I don't need an NDA.")
        trace = _latest_trace(client, resp["thread_id"])

    rp = _response_plan(trace)
    intent = resp["intent"]
    gov = trace.get("tool_governance", {})

    # Intent must not be nda_request.
    assert intent["query_primary"] != "nda_request"

    # Plan must not push toward NDA scoping.
    assert rp["primary_goal"] in {
        "safe_blocked_action",
        "clarify_intent",
        "continue_discovery",
        "document_scoping",  # allowed only when governance allowed=True (no action planned)
    }

    # When governance blocked, plan reflects it.
    if not gov.get("allowed", True):
        assert rp["primary_goal"] in {"safe_blocked_action", "clarify_intent"}

    # Response must not propose drafting an NDA document.
    # Check for the acronym as a standalone token (not as part of "recommendation", etc.).
    import re as _re

    text = _joined_text(resp).lower()
    nda_as_word = bool(_re.search(r"\bnda\b", text))
    assert not nda_as_word, f"Response should not mention NDA: {text[:200]}"


def test_counterfactual_consultation_does_not_book() -> None:
    """
    'If I wanted to book a consultation...' must not execute a booking.
    Because the message lacks required slots, the plan is MISSING_INFO and
    governance passes through — but the dispatcher does not execute.
    The response_plan goal is consultation_scoping or continue_discovery.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(
            client,
            "If I wanted to book a consultation, how would that work?",
        )
        trace = _latest_trace(client, resp["thread_id"])

    rp = _response_plan(trace)
    action_plan = trace.get("action_plan")
    gov = trace.get("tool_governance", {})
    text = _joined_text(resp).lower()

    # response_plan is present and structurally valid.
    assert rp["max_questions"] == 1

    # Goal should be consultation-related or discovery — not a confirmed booking.
    assert rp["primary_goal"] in {
        "consultation_scoping",
        "clarify_intent",
        "continue_discovery",
    }

    # Governance must not have dispatched a booking (no side-effect executed).
    if action_plan is not None:
        result = action_plan.get("result")
        if result is not None:
            assert not result.get("success", False), "No consultation must be booked"

    # Response must not say a booking was confirmed.
    assert "booked" not in text
    assert "appointment confirmed" not in text
    assert gov.get("allowed") is True or "counterfactual" in gov.get("reason", "")


def test_pricing_missing_info_plan() -> None:
    """
    For a pricing request without project details:
    - response_plan.primary_goal == pricing_scoping
    - next_question is the highest-priority missing quote slot
    - No dollar figures in the response (no real quote issued)
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "How much does ghostwriting cost?")
        trace = _latest_trace(client, resp["thread_id"])

    rp = _response_plan(trace)
    text = _joined_text(resp)

    assert rp["primary_goal"] == "pricing_scoping"

    # next_question must be a known pricing slot key (or None if all known).
    if rp["next_question"] is not None:
        assert rp["next_question"] in {
            "word_or_page_count",
            "genre",
            "manuscript_stage",
            "deadline",
            "services",
        }

    # No real quote should have been issued.
    assert "$" not in text
