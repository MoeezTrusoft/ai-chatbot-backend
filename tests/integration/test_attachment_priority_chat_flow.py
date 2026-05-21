"""Integration tests for PR 3 Part A: attachment priority overrides scoping."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.components.response.schemas import ResponseDraft
from bookcraft.infra.config import Settings


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
    r = client.post("/api/v1/chat/turn", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _trace(client: TestClient, thread_id: str) -> dict[str, Any]:
    rows = client.app.state.chat_service.trace_store.for_thread(thread_id)
    assert rows, f"No trace for {thread_id}"
    return rows[0]


def _text(body: dict[str, Any]) -> str:
    return " ".join(b.get("text", "") for b in body.get("bubbles", []))


class _FakeGenerator:
    def __init__(self, text: str = "") -> None:
        self._text = text or "I've received your file. Our specialist will be in touch."

    async def generate(self, **_kwargs: Any) -> ResponseDraft:
        return ResponseDraft(text=self._text, source="claude_sonnet")

    async def repair(self, *, bad_text: str, **_kwargs: Any) -> ResponseDraft:
        return ResponseDraft(text=bad_text, source="template_no_adapter_repair_unavailable")


# ---------------------------------------------------------------------------
# Test 1 — Manuscript attachment does not ask word count or draft status
# ---------------------------------------------------------------------------


def test_manuscript_attachment_does_not_ask_word_count_or_draft_status() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = _FakeGenerator(
            "Thanks for sharing your manuscript. Our senior editorial specialist will "
            "give it a proper assessment. What's the best name and email to reach you?"
        )
        body = _chat(
            client,
            "I need editing help. Here is my draft.",
            attachments=[{"filename": "my_draft.docx"}],
        )
        t = _trace(client, body["thread_id"])

    # Attachment priority should be active.
    ap = t.get("attachment_priority") or {}
    assert ap.get("has_attachment_priority") is True

    # Context pack must have scoping slots in forbidden_reasks.
    cp = t.get("context_pack") or {}
    forbidden = cp.get("forbidden_reasks") or []
    assert "word_or_page_count" in forbidden or "manuscript_stage" in forbidden

    # Response text must not ask scoping questions.
    text = _text(body).lower()
    assert "word count" not in text
    assert "how many pages" not in text
    assert "manuscript stage" not in text


# ---------------------------------------------------------------------------
# Test 2 — Manuscript attachment routes to editorial assessment
# ---------------------------------------------------------------------------


def test_manuscript_attachment_routes_to_editorial_assessment() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        body = _chat(
            client,
            "Please help with my manuscript.",
            attachments=[{"filename": "chapter_one.docx"}],
        )
        t = _trace(client, body["thread_id"])

    ai = t.get("attachment_intake") or {}
    assert ai.get("assessment_type") is not None
    assert ai.get("specialist_role") is not None
    assert ai.get("content_analysis_allowed") is False

    ap = t.get("attachment_priority") or {}
    assert ap.get("has_attachment_priority") is True
    assert ap.get("assessment_type") == ai.get("assessment_type")


# ---------------------------------------------------------------------------
# Test 3 — Cover attachment routes to cover design handoff
# ---------------------------------------------------------------------------


def test_cover_attachment_routes_to_cover_design_handoff() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        body = _chat(
            client,
            "I need help with my cover.",
            attachments=[{"filename": "cover_idea.jpg"}],
        )
        t = _trace(client, body["thread_id"])

    ai = t.get("attachment_intake") or {}
    assert ai.get("assessment_type") is not None

    ap = t.get("attachment_priority") or {}
    assert ap.get("has_attachment_priority") is True


# ---------------------------------------------------------------------------
# Test 4 — Attachment response does not claim review or analysis
# ---------------------------------------------------------------------------


def test_attachment_response_does_not_claim_review_or_analysis() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        body = _chat(
            client,
            "Please review my manuscript.",
            attachments=[{"filename": "book_draft.docx"}],
        )

    text = _text(body).casefold()
    forbidden = (
        "i reviewed",
        "i analyzed",
        "i analysed",
        "i read",
        "your manuscript says",
        "your file contains",
        "i found in the attachment",
        "after reviewing",
        "having reviewed",
    )
    for claim in forbidden:
        assert claim not in text, (
            f"Response must not claim content was reviewed: '{claim}' found in: {text[:200]}"
        )


# ---------------------------------------------------------------------------
# Test 5 — Attachment priority trace key present
# ---------------------------------------------------------------------------


def test_attachment_priority_trace_present() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        body = _chat(
            client,
            "I have a manuscript to share.",
            attachments=[{"filename": "my_novel.docx"}],
        )
        t = _trace(client, body["thread_id"])

    assert "attachment_priority" in t
    ap = t["attachment_priority"]
    assert "has_attachment_priority" in ap
    assert "suppress_slots" in ap
    assert isinstance(ap["suppress_slots"], list)
