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


def _slot_res(trace: dict[str, Any]) -> list[dict[str, Any]]:
    return trace.get("slot_resolution") or []


# ---------------------------------------------------------------------------
# 1. Cover style delegated — not re-asked in same turn response
# ---------------------------------------------------------------------------


def test_cover_style_delegated_not_reasked(client: TestClient) -> None:
    r1 = _chat(client, "I need cover design for my fantasy novel.")
    thread_id = str(r1["thread_id"])

    r2 = _chat(
        client,
        "I don't know the cover style, you guys decide.",
        thread_id=thread_id,
    )
    t2 = _trace(client, str(r2["thread_id"]))

    sr = _slot_res(t2)
    delegated = [s for s in sr if s.get("slot") == "cover_style"]
    assert delegated, f"cover_style must appear in slot_resolution, got {sr}"
    assert delegated[0].get("status") in ("delegated", "unknown_by_user"), (
        f"cover_style must be delegated/unknown, got {delegated[0].get('status')}"
    )

    txt = _text(r2).casefold()
    assert "cover style" not in txt and "visual direction" not in txt, (
        f"Response must not re-ask the delegated slot, got: {txt[:200]}"
    )


# ---------------------------------------------------------------------------
# 2. Word/page count unknown — not immediately repeated
# ---------------------------------------------------------------------------


def test_word_count_unknown_not_repeated(client: TestClient) -> None:
    r1 = _chat(client, "I need ghostwriting for my novel.")
    thread_id = str(r1["thread_id"])

    r2 = _chat(
        client,
        "I have no idea about the word count.",
        thread_id=thread_id,
    )
    t2 = _trace(client, str(r2["thread_id"]))
    sr = _slot_res(t2)
    unknown_wc = [s for s in sr if s.get("slot") == "word_or_page_count"]
    assert unknown_wc, f"word_or_page_count must appear in slot_resolution, got {sr}"
    assert unknown_wc[0].get("forbidden_reask") is True


# ---------------------------------------------------------------------------
# 3. Genre declined by "just show me" — becomes forbidden reask
# ---------------------------------------------------------------------------


def test_genre_declined_by_sample_insistence(client: TestClient) -> None:
    r1 = _chat(client, "I need cover design samples.")
    thread_id = str(r1["thread_id"])

    r2 = _chat(
        client,
        "Just show me samples, I don't know the genre.",
        thread_id=thread_id,
    )
    t2 = _trace(client, str(r2["thread_id"]))
    sr = _slot_res(t2)
    # A slot is resolved (could be genre or the active service slot).
    assert sr, f"At least one slot must appear in slot_resolution, got {sr}"
    # The resolved slot must have forbidden_reask=True.
    assert any(s.get("forbidden_reask") for s in sr), (
        f"At least one resolved slot must be forbidden_reask, got {sr}"
    )


# ---------------------------------------------------------------------------
# 4. Trace includes slot_resolution and delegated_decision
# ---------------------------------------------------------------------------


def test_trace_includes_slot_resolution_keys(client: TestClient) -> None:
    r1 = _chat(client, "I need cover design.")
    thread_id = str(r1["thread_id"])

    r2 = _chat(client, "You decide the cover style.", thread_id=thread_id)
    t2 = _trace(client, str(r2["thread_id"]))

    assert "slot_resolution" in t2, "trace must have slot_resolution key"
    assert "delegated_decision" in t2, "trace must have delegated_decision key"
    dd = t2.get("delegated_decision")
    if dd is not None:
        assert "detected" in dd
        assert "status" in dd


# ---------------------------------------------------------------------------
# 5. Claude-only contract still holds after slot delegation
# ---------------------------------------------------------------------------


def test_claude_only_contract_holds_after_delegation(client: TestClient) -> None:
    r1 = _chat(client, "I need cover design for my sci-fi novel.")
    thread_id = str(r1["thread_id"])

    r2 = _chat(
        client,
        "I have no idea about any of the details, you decide everything.",
        thread_id=thread_id,
    )
    t2 = _trace(client, str(r2["thread_id"]))

    contract = t2.get("customer_response_contract") or {}
    assert contract.get("contract_passed") is not False, (
        f"Claude-only contract must pass even after delegation, got {contract}"
    )
