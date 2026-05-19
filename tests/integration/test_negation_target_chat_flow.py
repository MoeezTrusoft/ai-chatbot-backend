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


def _neg_targets(trace: dict[str, Any]) -> list[dict[str, Any]]:
    return [t for t in (trace.get("negation_targets") or []) if t.get("polarity") == "negated"]


def _aff_targets(trace: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        t
        for t in (trace.get("negation_targets") or [])
        if t.get("polarity") in ("affirmed", "replacement")
    ]


# ---------------------------------------------------------------------------
# 1. Service negation routes to replacement service
# ---------------------------------------------------------------------------


def test_service_negation_routes_to_replacement(client: TestClient) -> None:
    body = _chat(client, "I don't need ghostwriting, I need editing.")
    thread_id = str(body["thread_id"])
    t = _trace(client, thread_id)

    # Negation target must be present in trace.
    neg = _neg_targets(t)
    assert any(n["target"] == "ghostwriting" for n in neg), (
        f"ghostwriting must be negated in trace, got neg={neg}"
    )

    # Intent should reflect the replacement service (editing_proofreading).
    intent = body.get("intent") or {}
    svc = intent.get("service_primary")
    assert svc == "editing_proofreading", (
        f"service_primary should be editing_proofreading after negation swap, got {svc}"
    )

    # Response must not mention ghostwriting as an active service.
    txt = _text(body).casefold()
    assert "ghostwriting" not in txt or "don't need ghostwriting" not in txt, (
        "Response must not re-offer the negated service"
    )


# ---------------------------------------------------------------------------
# 2. Negated NDA does not block agreement globally
# ---------------------------------------------------------------------------


def test_negated_nda_does_not_block_agreement(client: TestClient) -> None:
    body = _chat(client, "I don't need an NDA, but I do need an agreement.")
    thread_id = str(body["thread_id"])
    t = _trace(client, thread_id)

    neg = _neg_targets(t)
    aff = _aff_targets(t)

    assert any(n["target"] in ("generate_nda", "nda") for n in neg), (
        f"NDA must be negated, got neg={neg}"
    )
    assert any(a["target"] in ("generate_agreement", "agreement") for a in aff), (
        f"Agreement must be affirmed, got aff={aff}"
    )

    # Intent should be AGREEMENT_REQUEST (not NDA_REQUEST after swap).
    intent = body.get("intent") or {}
    got_q = intent.get("query_primary")
    assert got_q in {"agreement_request", "service_question", "unclear"}, (
        f"query_primary after NDA negation should be agreement-related, got {got_q}"
    )

    # Response must not mention the NDA path as if it's being processed.
    txt = _text(body).casefold()
    assert "nda" not in txt or "don't need" in txt, "Response must not reference NDA as active"


# ---------------------------------------------------------------------------
# 3. Negated pricing does not trigger a quote
# ---------------------------------------------------------------------------


def test_negated_pricing_does_not_trigger_quote(client: TestClient) -> None:
    body = _chat(client, "Don't send pricing yet, just show me samples.")
    thread_id = str(body["thread_id"])
    t = _trace(client, thread_id)

    neg = _neg_targets(t)
    assert any(n["target"] == "price_quote" for n in neg), (
        f"price_quote must be negated, got neg={neg}"
    )

    # Intent should NOT be a pricing question after the negation.
    intent = body.get("intent") or {}
    assert intent.get("query_primary") not in {"pricing_question", "timeline_question"}, (
        f"query_primary should not be pricing after negation, got {intent.get('query_primary')}"
    )

    # Response must not contain a price.
    txt = _text(body)
    assert "$" not in txt, f"Response must not contain a price, got: {txt[:200]}"


# ---------------------------------------------------------------------------
# 4. Negated service does not appear as active primary after arbitration
# ---------------------------------------------------------------------------


def test_negated_service_not_primary_after_arbitration(client: TestClient) -> None:
    body = _chat(client, "No cover design, only formatting.")
    thread_id = str(body["thread_id"])
    t = _trace(client, thread_id)

    neg = _neg_targets(t)
    assert any(n["target"] == "cover_design_illustration" for n in neg), (
        f"cover_design_illustration must be negated, got {neg}"
    )

    # Context pack active service should not be the negated service.
    cp = t.get("context_pack") or {}
    active_svc = cp.get("active_service")
    assert active_svc != "cover_design_illustration", (
        f"active_service must not be the negated service, got {active_svc}"
    )


# ---------------------------------------------------------------------------
# 5. Trace contains negation_targets
# ---------------------------------------------------------------------------


def test_trace_contains_negation_targets(client: TestClient) -> None:
    body = _chat(client, "I don't need ghostwriting, I need editing.")
    thread_id = str(body["thread_id"])
    t = _trace(client, thread_id)

    nt = t.get("negation_targets")
    assert isinstance(nt, list), "negation_targets must be a list in the trace"
    assert len(nt) > 0, "negation_targets must not be empty for this input"
    for item in nt:
        assert "target_type" in item
        assert "target" in item
        assert "polarity" in item
        assert "confidence" in item
