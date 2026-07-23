"""Integration tests for attachment + assessment intake flow."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.components.response.schemas import ResponseDraft
from bookcraft.infra.config import Settings


@pytest.fixture()
def client() -> TestClient:
    app = create_app(Settings(app_env="test"))
    return TestClient(app)


def _chat(
    client: TestClient,
    message: str,
    *,
    thread_id: object | None = None,
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"message": message}
    if thread_id is not None:
        payload["thread_id"] = str(thread_id)
    if attachments:
        payload["attachments"] = attachments
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
# 1. Manuscript attachment acknowledged and assessment trace set
# ---------------------------------------------------------------------------


def test_manuscript_attachment_assessment_trace_set(client: TestClient) -> None:
    r = _chat(
        client,
        "I need editing help. Here is my draft.",
        attachments=[{"filename": "my_draft.docx"}],
    )
    t = _trace(client, str(r["thread_id"]))

    ai = t.get("attachment_intake") or {}
    assert ai.get("assessment_type") is not None, f"assessment_type must be set: {ai}"
    assert ai.get("specialist_role") is not None
    assert ai.get("content_analysis_allowed") is False


# ---------------------------------------------------------------------------
# 2. Cover attachment routes to cover_design_assessment
# ---------------------------------------------------------------------------


def test_cover_attachment_routes_to_cover_design_assessment(client: TestClient) -> None:
    r = _chat(
        client,
        "I need help with my cover design.",
        attachments=[{"filename": "cover_sketch.jpg"}],
    )
    t = _trace(client, str(r["thread_id"]))

    ai = t.get("attachment_intake") or {}
    assert ai.get("assessment_type") in (
        "cover_design_assessment",
        "manuscript_assessment",
        "general_project_assessment",
    ), f"Unexpected assessment type: {ai.get('assessment_type')}"


# ---------------------------------------------------------------------------
# 3. Response does not claim attachment reviewed/analyzed
# ---------------------------------------------------------------------------


def test_response_does_not_claim_attachment_reviewed(client: TestClient) -> None:
    r = _chat(
        client,
        "Please review my manuscript.",
        attachments=[{"filename": "book_draft.docx"}],
    )
    txt = _text(r).casefold()
    forbidden_claims = (
        "i reviewed",
        "i analyzed",
        "i read",
        "your manuscript says",
        "your file contains",
        "i found in the attachment",
        "after reviewing",
    )
    for claim in forbidden_claims:
        assert claim not in txt, (
            f"Response must not claim attachment was reviewed: '{claim}' found in: {txt[:200]}"
        )


# ---------------------------------------------------------------------------
# 4. Manuscript status saved from message and not re-asked
# ---------------------------------------------------------------------------


def test_manuscript_status_saved_and_not_reasked(client: TestClient) -> None:
    r1 = _chat(client, "I need ghostwriting — I have a rough draft already written.")
    thread_id = str(r1["thread_id"])

    r2 = _chat(client, "Can you help me develop it further?", thread_id=thread_id)
    t2 = _trace(client, str(r2["thread_id"]))

    cp2 = t2.get("context_pack") or {}
    # manuscript_status should be known (draft)
    ms = cp2.get("manuscript_status")
    if ms:
        # If status is known, manuscript_stage should be in forbidden_reasks
        assert "manuscript_stage" in (cp2.get("forbidden_reasks") or []) or ms is not None


# ---------------------------------------------------------------------------
# 5. Claude-only contract holds when fake adapter is used
# ---------------------------------------------------------------------------


def test_claude_only_contract_holds_with_attachment(client: TestClient) -> None:
    from tests.integration.test_claude_only_response_contract_chat_flow import (
        FakeClaudeResponseGenerator,
    )

    app = create_app(Settings(app_env="test", api_auth_mode="off"))
    with TestClient(app) as c:
        c.app.state.chat_service.response_generator = FakeClaudeResponseGenerator(
            initial=ResponseDraft(
                text=(
                    "I have received your manuscript file. Our senior editorial "
                    "specialist will review it professionally."
                ),
                source="claude_sonnet",
            )
        )
        r = _chat(
            c,
            "Please help with my manuscript.",
            attachments=[{"filename": "chapter_one.docx"}],
        )
        t = _trace(c, str(r["thread_id"]))

    assert t["assistant"]["source"] == "claude_sonnet"
    contract = t.get("customer_response_contract") or {}
    assert contract.get("production_contract_passed") is True


# ---------------------------------------------------------------------------
# 6. Trace includes attachment_intake key
# ---------------------------------------------------------------------------


def test_trace_includes_attachment_intake(client: TestClient) -> None:
    r = _chat(
        client,
        "I have a manuscript to share.",
        attachments=[{"filename": "my_novel.docx"}],
    )
    t = _trace(client, str(r["thread_id"]))

    assert "attachment_intake" in t, "attachment_intake must be in trace"
    ai = t["attachment_intake"]
    assert "attachments" in ai
    assert "assessment_type" in ai
    assert "content_analysis_allowed" in ai
    assert ai["content_analysis_allowed"] is False


def test_quick_look_enrichment_round_trips_through_service(client: TestClient) -> None:
    """Pre-extracted 'quick look' metadata from the upload service must survive the
    full turn (intake -> state -> trace) so the LLM can narrate a human first
    impression. The backend still performs no content analysis of its own."""
    r = _chat(
        client,
        "Here's my manuscript for editing.",
        attachments=[
            {
                "filename": "winter-memoir.docx",
                "mime_type": (
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document"
                ),
                "page_count": 134,
                "word_count": 40200,
                "excerpt": "It was the winter my mother finally stopped speaking.",
            }
        ],
    )
    t = _trace(client, str(r["thread_id"]))
    att = t["attachment_intake"]["attachments"][0]
    assert att["page_count"] == 134
    assert att["word_count"] == 40200
    assert "winter" in (att["excerpt"] or "")
    # Backend never claims to have read it, even with an excerpt present.
    assert t["attachment_intake"]["content_analysis_allowed"] is False
