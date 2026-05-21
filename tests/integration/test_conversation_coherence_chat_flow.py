"""Integration tests for conversation coherence and assumption guard (PR 1)."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.components.response.schemas import ResponseDraft
from bookcraft.infra.config import Settings


def _chat(client: TestClient, message: str, *, thread_id: object | None = None) -> dict[str, Any]:
    payload: dict[str, object] = {"message": message}
    if thread_id is not None:
        payload["thread_id"] = str(thread_id)
    response = client.post("/api/v1/chat/turn", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _latest_trace(client: TestClient, thread_id: str) -> dict[str, Any]:
    trace_store = client.app.state.chat_service.trace_store
    rows = trace_store.for_thread(thread_id)
    assert rows, f"No trace rows found for thread {thread_id}"
    return rows[0]


class _FakeResponseGenerator:
    """Fake generator that returns pre-set text; minimal interface for testing."""

    def __init__(self, text: str, source: str = "claude_sonnet") -> None:
        self._draft = ResponseDraft(text=text, source=source)

    async def generate(self, **_kwargs: Any) -> ResponseDraft:
        return self._draft

    async def repair(self, *, bad_text: str, **_kwargs: Any) -> ResponseDraft:
        return ResponseDraft(text=bad_text, source="template_no_adapter_repair_unavailable")


# ---------------------------------------------------------------------------
# Test 1 — Greeting must not ask word count or scoping questions
# ---------------------------------------------------------------------------


def test_hello_does_not_ask_word_count() -> None:
    """A greeting-only message must not trigger scoping questions."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = _FakeResponseGenerator(
            text="Welcome to BookCraft! What can I help you with today?",
        )
        resp = _chat(client, "hello")
        trace = _latest_trace(client, resp["thread_id"])

    # Runtime atoms must flag this as a greeting-only turn.
    atoms = trace.get("runtime_atoms", {})
    assert atoms.get("is_greeting_only") is True

    # The response must not contain word count / scoping language.
    text = " ".join(str(b["text"]) for b in resp["bubbles"])
    assert "word count" not in text.lower()
    assert "page count" not in text.lower()
    assert "manuscript stage" not in text.lower()


def test_hello_mate_greeting_sets_greeting_turn() -> None:
    """'hello mate' is a greeting-only turn."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = _FakeResponseGenerator(
            text="Hey there! Happy to help with your book project. What are you working on?"
        )
        resp = _chat(client, "hello mate")
        trace = _latest_trace(client, resp["thread_id"])

    atoms = trace.get("runtime_atoms", {})
    assert atoms.get("is_greeting_only") is True


# ---------------------------------------------------------------------------
# Test 2 — Uncertain genre must not cause assumption of memoir or fiction
# ---------------------------------------------------------------------------


def test_story_written_then_uncertain_genre_does_not_assume_memoir() -> None:
    """When user says 'not sure if memoir or fiction', no genre should be confirmed."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = _FakeResponseGenerator(
            text=(
                "That makes sense — many authors start with a story idea and figure out "
                "the category later. Which direction feels closer: a personal story based "
                "on your own life, or a fictional narrative?"
            )
        )
        resp = _chat(client, "I have a story I'd like written, not sure if memoir or fiction")
        trace = _latest_trace(client, resp["thread_id"])

    atoms = trace.get("runtime_atoms", {})
    # Genre must be uncertain, not confirmed.
    assert atoms.get("genre_status") == "uncertain"
    # No confirmed genre atom.
    assert "genre" not in atoms or atoms.get("genre_status") == "uncertain"


def test_uncertain_genre_does_not_set_confirmed_genre_atom() -> None:
    """'maybe memoir or business' must set genre_status=uncertain, not genre=memoir."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = _FakeResponseGenerator(
            text="Happy to help you figure that out. What's the core topic of the book?"
        )
        resp = _chat(client, "maybe memoir or business, I haven't decided")
        trace = _latest_trace(client, resp["thread_id"])

    atoms = trace.get("runtime_atoms", {})
    assert atoms.get("genre_status") == "uncertain"
    assert "genre" not in atoms  # must not have a confirmed genre atom


# ---------------------------------------------------------------------------
# Test 3 — Word count extraction from message with explicit "words"
# ---------------------------------------------------------------------------


def test_number_reply_after_word_count_question_saved_as_word_count() -> None:
    """A message containing explicit word count should extract word_count atom."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = _FakeResponseGenerator(
            text="Got it — 60,000 words. That's a solid full-length novel. What genre is it?"
        )
        resp = _chat(client, "I need ghostwriting, about 60000 words")
        trace = _latest_trace(client, resp["thread_id"])

    atoms = trace.get("runtime_atoms", {})
    word_counts = atoms.get("word_counts", [])
    assert 60000 in word_counts


# ---------------------------------------------------------------------------
# Test 4 — Picture book is a format, not automatically a children's genre
# ---------------------------------------------------------------------------


def test_picture_book_not_children_until_audience_confirmed() -> None:
    """'picture book' alone must set book_formats=['picture_book'] not genre=children's book."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = _FakeResponseGenerator(
            text=(
                "A picture book is a great format to work with. "
                "Could you tell me more about the story and who it's for?"
            )
        )
        resp = _chat(client, "I want to create a picture book")
        trace = _latest_trace(client, resp["thread_id"])

    atoms = trace.get("runtime_atoms", {})
    # picture_book must appear as a book_format, not as genre.
    assert "picture_book" in (atoms.get("book_formats") or [])
    # genre must NOT be set to children's book from picture book alone.
    assert atoms.get("genre") != "children's book"
    # audience must not be inferred.
    assert atoms.get("audience") is None


def test_picture_book_for_kids_sets_audience() -> None:
    """'picture book for kids' must set audience=children."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = _FakeResponseGenerator(
            text="A picture book for kids — great choice. What age range is the target audience?"
        )
        resp = _chat(client, "I want to create a picture book for kids")
        trace = _latest_trace(client, resp["thread_id"])

    atoms = trace.get("runtime_atoms", {})
    assert "picture_book" in (atoms.get("book_formats") or [])
    assert atoms.get("audience") == "children"


# ---------------------------------------------------------------------------
# Test 5 — Name/email must not be language-rejected
# ---------------------------------------------------------------------------


def test_name_email_not_language_rejected() -> None:
    """Messages that are predominantly PII must not be treated as non-English."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "Maham Qureshi, maham@example.com")

    # Must not return a language redirect.
    assert resp["language_status"] == "en"
    bubbles_text = " ".join(str(b["text"]) for b in resp["bubbles"])
    assert "english" not in bubbles_text.lower() or "please send" not in bubbles_text.lower()


def test_name_and_phone_not_language_rejected() -> None:
    """Phone number messages must not be language-rejected."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "Sarah, +92 300 1234567")

    assert resp["language_status"] == "en"


# ---------------------------------------------------------------------------
# Test 6 — Mixed-language: English portion answered, non-English safely ignored
# ---------------------------------------------------------------------------


def test_mixed_language_answers_english_part_only() -> None:
    """Mixed-language messages with clear English intent must not be fully rejected."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = _FakeResponseGenerator(
            text=(
                "Of course — I can help with editing for your book. "
                "Could you share more details about the manuscript?"
            )
        )
        # English part: "I need editing for my book"
        # Non-English part: "meri file ready hai" (Urdu: "my file is ready")
        resp = _chat(client, "I need editing for my book, meri file ready hai")

    # The response must address the English intent (editing), not reject entirely.
    assert resp["language_status"] == "en"
    text = " ".join(str(b["text"]) for b in resp["bubbles"])
    # Should not be a pure language-redirect message
    assert "please send your message in english" not in text.lower()
