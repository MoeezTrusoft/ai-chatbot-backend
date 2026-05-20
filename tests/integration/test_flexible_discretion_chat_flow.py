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


def _fi(trace: dict[str, Any]) -> dict[str, Any]:
    return trace.get("flexible_intent") or {}


# ---------------------------------------------------------------------------
# 1. Unsure service → flexible_service_guidance
# ---------------------------------------------------------------------------


def test_unsure_service_routes_to_flexible_guidance(client: TestClient) -> None:
    r1 = _chat(client, "I don't know what I need, can you guide me?")
    thread_id = str(r1["thread_id"])
    t1 = _trace(client, thread_id)

    fi = _fi(t1)
    assert fi.get("detected") is True, f"flexible_intent must be detected, got {fi}"
    assert fi.get("mode") in ("service_guidance", "consultation_handoff"), (
        f"Expected guidance mode, got {fi.get('mode')}"
    )


# ---------------------------------------------------------------------------
# 2. BookCraft discretion → consultation_handoff or process_explanation
# ---------------------------------------------------------------------------


def test_bookcraft_discretion_routes_to_handoff(client: TestClient) -> None:
    r1 = _chat(client, "I trust your team — whatever you think is best.")
    thread_id = str(r1["thread_id"])
    t1 = _trace(client, thread_id)

    fi = _fi(t1)
    assert fi.get("detected") is True, f"flexible_intent must be detected, got {fi}"
    assert fi.get("mode") in (
        "bookcraft_discretion",
        "consultation_handoff",
        "process_explanation",
    ), f"Expected discretion/handoff mode, got {fi.get('mode')}"


# ---------------------------------------------------------------------------
# 3. Delegated cover style is not re-asked
# ---------------------------------------------------------------------------


def test_delegated_cover_style_not_reasked(client: TestClient) -> None:
    r1 = _chat(client, "I need cover design for my thriller novel.")
    thread_id = str(r1["thread_id"])

    r2 = _chat(
        client,
        "You decide the cover style — use your own creativity.",
        thread_id=thread_id,
    )
    txt2 = _text(r2).casefold()
    assert "cover style" not in txt2 or "you decide" in txt2.casefold(), (
        f"Response must not re-ask cover style after delegation, got: {txt2[:200]}"
    )


# ---------------------------------------------------------------------------
# 4. Process question gets process_explanation goal
# ---------------------------------------------------------------------------


def test_process_question_gets_process_explanation(client: TestClient) -> None:
    r1 = _chat(client, "How does it work? What are the steps?")
    thread_id = str(r1["thread_id"])
    t1 = _trace(client, thread_id)

    fi = _fi(t1)
    assert fi.get("detected") is True, f"flexible_intent must be detected, got {fi}"
    assert fi.get("mode") in ("process_explanation", "consultation_handoff"), (
        f"Expected process mode, got {fi.get('mode')}"
    )


# ---------------------------------------------------------------------------
# 5. Consultation handoff does not auto-book without confirmation
# ---------------------------------------------------------------------------


def test_consultation_handoff_does_not_autobook(client: TestClient) -> None:
    r1 = _chat(client, "Can I talk to someone on your team?")
    thread_id = str(r1["thread_id"])
    t1 = _trace(client, thread_id)

    fi = _fi(t1)
    assert fi.get("detected") is True
    assert fi.get("mode") == "consultation_handoff"

    txt = _text(r1).casefold()
    # Must not claim consultation was booked.
    claim_markers = ("has been scheduled", "booked for", "confirmed your")
    for marker in claim_markers:
        assert marker not in txt, (
            f"Response must not auto-book consultation: '{marker}' in {txt[:200]}"
        )


# ---------------------------------------------------------------------------
# 6. Trace includes flexible_intent
# ---------------------------------------------------------------------------


def test_trace_includes_flexible_intent(client: TestClient) -> None:
    r1 = _chat(client, "Where should I start? I need help with my book.")
    thread_id = str(r1["thread_id"])
    t1 = _trace(client, thread_id)

    assert "flexible_intent" in t1, "trace must have flexible_intent key"
    fi = t1["flexible_intent"]
    assert "detected" in fi
    assert "mode" in fi
    assert "recommended_primary_goal" in fi


# ---------------------------------------------------------------------------
# 7. Final source remains Claude-compliant for flexible intent
# ---------------------------------------------------------------------------


def test_final_source_claude_compliant_for_flexible_intent(client: TestClient) -> None:
    r1 = _chat(client, "I don't know which service I need, help me choose.")
    thread_id = str(r1["thread_id"])
    t1 = _trace(client, thread_id)

    final_source = (t1.get("assistant") or {}).get("source", "")
    # Must not be a raw deterministic source.
    assert "portfolio_engine" not in final_source, (
        f"Final source must not be portfolio_engine, got {final_source}"
    )
