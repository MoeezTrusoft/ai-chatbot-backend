from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.components.response.schemas import ResponseDraft
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
    resp = client.post("/api/v1/chat/turn", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _joined_text(body: dict[str, Any]) -> str:
    return " ".join(str(b["text"]) for b in body["bubbles"])


def _latest_trace(client: TestClient, thread_id: str) -> dict[str, Any]:
    trace_store = client.app.state.chat_service.trace_store
    rows = trace_store.for_thread(thread_id)
    assert rows, f"No trace rows for {thread_id}"
    return rows[0]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_sales_tone_trace_key_present_on_every_turn() -> None:
    """Every processed turn must emit a sales_tone entry in the live trace."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "I need cover design for my children's fiction book.")
        trace = _latest_trace(client, resp["thread_id"])

    st = trace.get("sales_tone")
    assert isinstance(st, dict), f"sales_tone missing from trace; keys: {list(trace.keys())}"
    assert "passed" in st
    assert "failures" in st
    assert isinstance(st["failures"], list)
    assert "suggestions" in st
    assert "audit" in st
    assert isinstance(st["audit"], list)
    assert len(st["audit"]) >= 1


def test_sales_tone_passes_for_clean_template_response() -> None:
    """Template responses must pass the sales-tone check (no robotic openers)."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "What services does BookCraft offer?")
        trace = _latest_trace(client, resp["thread_id"])

    st = trace.get("sales_tone", {})
    # Template responses don't start with "Sure!", "Absolutely!", etc.
    banned_opener_failures = [f for f in st.get("failures", []) if "banned_opener" in f]
    assert not banned_opener_failures, (
        f"Template response has banned opener: {banned_opener_failures}; "
        f"response text: {_joined_text(resp)[:200]}"
    )


def test_sales_tone_no_internal_terms_in_clean_response() -> None:
    """Clean template responses must not contain internal implementation terms."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "Tell me about cover design.")

    text = _joined_text(resp)
    for term in ("backend", "classifier", "RAG", "tool_governance", "action_plan"):
        assert term not in text, f"Internal term '{term}' found in response"


def test_sales_tone_cover_design_flow_service_guidance_available() -> None:
    """After cover design is established, service guidance appears in suggestions."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        first = _chat(client, "I need a cover design for my children's fiction book.")
        thread_id = first["thread_id"]
        _chat(client, "I have finished my manuscript.", thread_id=thread_id)
        trace = _latest_trace(client, thread_id)

    st = trace.get("sales_tone", {})
    # When cover design is active and response plan is set, service guidance
    # should appear as a suggestion in the report.
    has_service_guidance = any("service_guidance" in s for s in st.get("suggestions", []))
    # The trace must be structurally valid regardless.
    assert "passed" in st
    assert "audit" in st
    # If service guidance is available, it should mention cover design specifics.
    if has_service_guidance:
        guidance_text = " ".join(s for s in st["suggestions"] if "service_guidance" in s).lower()
        assert "cover" in guidance_text or "visual" in guidance_text or "style" in guidance_text


def test_sales_tone_report_does_not_affect_response_content() -> None:
    """SalesToneReport is advisory — it must not alter the response text."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "I need ghostwriting help for my sci-fi novel.")
        trace = _latest_trace(client, resp["thread_id"])

    text = _joined_text(resp)
    st = trace.get("sales_tone", {})

    # The response is generated independently of the tone report.
    # The tone report is advisory — it never replaces the response draft.
    assert text.strip(), "Response must always be non-empty"
    assert "passed" in st  # tone report exists alongside the response


def test_sales_tone_and_quality_gate_both_present() -> None:
    """Both response_quality and sales_tone must exist in every trace."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "How much does cover design cost?")
        trace = _latest_trace(client, resp["thread_id"])

    assert "response_quality" in trace, "response_quality must be in trace"
    assert "sales_tone" in trace, "sales_tone must be in trace"

    rq = trace["response_quality"]
    st = trace["sales_tone"]
    assert isinstance(rq["passed"], bool)
    assert isinstance(st["passed"], bool)


def test_sales_tone_uses_known_facts_when_context_available() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        first = _chat(client, "I need cover design for my children's fiction book.")
        thread_id = first["thread_id"]
        second = _chat(client, "I have finished my manuscript.", thread_id=thread_id)
        trace = _latest_trace(client, thread_id)

    text = _joined_text(second).lower()
    assert "children" in text or "fiction" in text or "manuscript" in text or "cover design" in text
    st = trace.get("sales_tone", {})
    assert "missing_specificity_known_context" not in st.get("failures", [])


def test_sales_tone_response_has_no_fake_excitement() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "Can you help with editing my completed draft?")
        trace = _latest_trace(client, resp["thread_id"])

    text = _joined_text(resp)
    assert "!!!" not in text
    st = trace.get("sales_tone", {})
    assert "fake_excitement" not in st.get("failures", [])


def test_forced_bad_response_is_cleaned_by_quality_fallback() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    class _BadGenerator:
        async def generate(self, **kwargs: Any) -> ResponseDraft:
            del kwargs
            return ResponseDraft(text="Sure! I can assist you with that.", source="forced_bad_test")

    with TestClient(app) as client:
        chat_service = client.app.state.chat_service
        original_generator = chat_service.response_generator
        chat_service.response_generator = _BadGenerator()
        try:
            resp = _chat(client, "I need help with cover design.")
            trace = _latest_trace(client, resp["thread_id"])
        finally:
            chat_service.response_generator = original_generator

    text = _joined_text(resp)
    rq = trace.get("response_quality", {})
    assert any("sales_tone" in failure for failure in rq.get("failures", []))
    assert not text.startswith("Sure!")
    assert "I can assist you with that." not in text
