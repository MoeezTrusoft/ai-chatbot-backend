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


def _quality(trace: dict[str, Any]) -> dict[str, Any]:
    rq = trace.get("response_quality")
    assert isinstance(rq, dict), f"response_quality missing from trace; keys: {list(trace.keys())}"
    return rq


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_quality_gate_trace_key_present_on_every_turn() -> None:
    """Every processed turn must emit a response_quality trace entry."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "I need cover design for my children's fiction book.")
        trace = _latest_trace(client, resp["thread_id"])

    rq = _quality(trace)
    assert "passed" in rq
    assert "failures" in rq
    assert isinstance(rq["failures"], list)
    assert "audit" in rq
    assert isinstance(rq["audit"], list)
    assert len(rq["audit"]) >= 1


def test_quality_gate_passes_on_clean_service_question() -> None:
    """A normal service-question turn should pass all quality checks."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "What services does BookCraft offer for fiction authors?")
        trace = _latest_trace(client, resp["thread_id"])

    rq = _quality(trace)
    # Core quality checks should stay clean. If tone-only issues appear, they
    # are captured separately under the unified sales_tone gate result.
    non_tone_failures = [f for f in rq["failures"] if f != "sales_tone"]
    assert len(non_tone_failures) == 0


def test_quality_gate_no_dollar_on_service_question() -> None:
    """Service-question responses must never contain price figures."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "What does cover design cost roughly?")
        trace = _latest_trace(client, resp["thread_id"])

    rq = _quality(trace)
    text = _joined_text(resp)

    assert "$" not in text
    assert not any("unapproved_price" in f for f in rq["failures"]), (
        "Price figure detected in a service-question response"
    )


def test_quality_gate_known_genre_not_re_asked_after_established() -> None:
    """
    After genre is established, the quality gate must not allow the response
    to ask for genre again.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        first = _chat(client, "I need cover design. The genre is children's fiction.")
        thread_id = first["thread_id"]
        resp = _chat(client, "What else do you need?", thread_id=thread_id)
        trace = _latest_trace(client, thread_id)

    rq = _quality(trace)
    text = _joined_text(resp).casefold()

    assert "what genre" not in text
    assert not any("known_fact_reask" in f for f in rq["failures"]), (
        "Quality gate should not flag a genre re-ask when genre was not re-asked"
    )


def test_quality_gate_no_ghostwriting_when_cover_design_active() -> None:
    """
    After cover design is established, ghostwriting must not appear in
    the response and the quality gate must catch it if it does.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        first = _chat(client, "I need a cover design for my fiction novel.")
        thread_id = first["thread_id"]
        resp = _chat(
            client,
            "Its fiction children book as I told you.",
            thread_id=thread_id,
        )
        trace = _latest_trace(client, thread_id)

    rq = _quality(trace)
    text = _joined_text(resp).casefold()

    # Ghostwriting must not appear in the cover-design flow response.
    assert "ghostwriting" not in text
    # Quality gate should pass (no ghostwriting mention to flag).
    assert not any("wrong_service" in f for f in rq["failures"])


def test_quality_gate_response_plan_reflects_gate_state() -> None:
    """
    Both response_plan and response_quality appear in the trace,
    and their fields are consistent.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "How much does ghostwriting cost?")
        trace = _latest_trace(client, resp["thread_id"])

    rp = trace.get("response_plan", {})
    rq = _quality(trace)

    # Both trace keys present.
    assert isinstance(rp, dict)
    assert isinstance(rq, dict)

    # Quality gate ran.
    assert "passed" in rq
    assert "audit" in rq

    # When response_plan has max_questions=1, gate should not see too-many-questions.
    if rp.get("max_questions") == 1:
        assert not any("too_many_questions" in f for f in rq["failures"])


def test_quality_gate_fallback_used_when_text_contains_internal_terms() -> None:
    """
    If the quality gate detects internal terms, the safe_fallback should
    replace the draft text and the customer-facing response must be clean.
    (End-to-end: the generated text from the no-adapter mock is already clean,
    so this test exercises the gate's pass path rather than the fallback path.
    We verify the gate ran without blocking clean output.)
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "Tell me about your editing and proofreading services.")
        trace = _latest_trace(client, resp["thread_id"])

    rq = _quality(trace)
    text = _joined_text(resp)

    # The no-adapter template never leaks internal terms, so gate should pass.
    assert "backend" not in text.lower()
    assert "RAG" not in text
    # Quality gate audit must be present.
    assert any("quality_gate" in a for a in rq["audit"])


# ===========================================================================
# Required integration tests (spec scenarios)
# ===========================================================================


def test_kashif_cover_design_flow_passes_or_falls_back_safely() -> None:
    """
    Kashif cover-design session: response_quality present in trace,
    final response never asks for genre or draft status.
    If the gate fails it must use the safe_fallback, which is also clean.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        first = _chat(
            client,
            "I am Kashif, I need a design on cover for my book. Can you help?",
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

    rq = _quality(trace)
    text = _joined_text(fourth).casefold()

    # Quality gate ran.
    assert "passed" in rq
    assert isinstance(rq["audit"], list)

    # Whether gate passed or fell back, the response must be clean.
    assert "what genre" not in text
    assert "starting from scratch" not in text
    assert "have a draft" not in text
    assert "manuscript stage" not in text
    assert "ghostwriting" not in text

    # If gate failed, source must reflect fallback.
    assistant = trace.get("assistant", {})
    if not rq["passed"]:
        assert "quality_fallback" in assistant.get("source", ""), (
            "When gate fails, source must include quality_fallback"
        )


def test_negated_nda_flow_does_not_claim_nda_generated() -> None:
    """
    'I don't need an NDA' must not produce a response that claims an NDA
    was generated, sent, or created.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "I don't need an NDA.")
        trace = _latest_trace(client, resp["thread_id"])

    rq = _quality(trace)
    text = _joined_text(resp).lower()

    # Response must not claim NDA was generated.
    import re as _re

    assert not _re.search(r"\b(generated|sent|created|produced)\b.*\bnda\b", text)
    assert not _re.search(r"\bnda\b.*\b(generated|sent|created|produced)\b", text)

    # Quality gate must not have flagged blocked_action (meaning no false success claim).
    assert not any("blocked_action" in f for f in rq["failures"]), (
        f"Gate flagged blocked_action claim: {rq['failures']}"
    )


def test_counterfactual_consultation_does_not_claim_booking() -> None:
    """
    'If I wanted to book a consultation...' must not produce a response
    that claims the consultation was booked or scheduled.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(
            client,
            "If I wanted to book a consultation, how would that work?",
        )
        trace = _latest_trace(client, resp["thread_id"])

    rq = _quality(trace)
    text = _joined_text(resp).lower()

    # Response must not claim a booking happened.
    assert "booked" not in text
    assert "appointment confirmed" not in text
    assert "consultation scheduled" not in text

    # Quality gate must not flag a blocked_action success claim.
    assert not any("blocked_action" in f for f in rq["failures"]), (
        f"Gate detected spurious success claim: {rq['failures']}"
    )


def test_pricing_missing_info_flow_does_not_invent_dollar_amount() -> None:
    """
    When pricing data is missing the response must never contain a $ figure
    and the quality gate must not flag unapproved_price.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "How much does cover design cost?")
        trace = _latest_trace(client, resp["thread_id"])

    rq = _quality(trace)
    text = _joined_text(resp)

    assert "$" not in text
    assert not any("unapproved_price" in f for f in rq["failures"]), (
        f"Price figure leaked into a missing-info response: {rq['failures']}"
    )


def test_quality_fallback_replaces_bad_response_and_updates_source() -> None:
    """
    When the quality gate detects an internal artifact in the draft it
    replaces the text with safe_fallback AND updates the source label.

    SonnetResponseGenerator uses slots=True so its methods cannot be patched
    directly. Instead, we swap the entire response_generator on the service
    with a lightweight stub that returns a response containing 'runtime atoms'.
    """
    from bookcraft.components.response.schemas import ResponseDraft

    bad_draft = ResponseDraft(
        text=(
            "The runtime atoms in our classifier detected your request. "
            "What cover style would you like?"
        ),
        source="mock_sonnet",
    )

    class _BadGenerator:
        """Minimal stub that always returns the bad draft."""

        async def generate(self, **_kwargs: Any) -> ResponseDraft:
            return bad_draft

    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        # Swap the generator on the live service instance before sending the request.
        client.app.state.chat_service.response_generator = _BadGenerator()  # type: ignore[assignment]
        resp = _chat(client, "I need a cover design.")
        trace = _latest_trace(client, resp["thread_id"])

    rq = _quality(trace)
    text = _joined_text(resp)
    assistant = trace.get("assistant", {})

    # Gate must have caught the artifact.
    assert not rq["passed"]
    assert any("internal_artifact" in f for f in rq["failures"])

    # Fallback text must be clean.
    assert "runtime atoms" not in text.lower()
    assert "classifier" not in text.lower()

    # Source must be updated to reflect the fallback.
    assert "quality_fallback" in assistant.get("source", ""), (
        f"Expected quality_fallback in source; got: {assistant.get('source')}"
    )
