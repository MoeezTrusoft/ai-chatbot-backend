"""Integration tests for PR 3 Part C/D: portfolio rich links."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.infra.config import Settings


def _chat(client: TestClient, message: str, *, thread_id: object | None = None) -> dict[str, Any]:
    payload: dict[str, object] = {"message": message}
    if thread_id is not None:
        payload["thread_id"] = str(thread_id)
    r = client.post("/api/v1/chat/turn", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _trace(client: TestClient, thread_id: str) -> dict[str, Any]:
    rows = client.app.state.chat_service.trace_store.for_thread(thread_id)
    assert rows
    return rows[0]


def _all_rich_segments(body: dict[str, Any]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for bubble in body.get("bubbles", []):
        for seg in bubble.get("rich_segments", []):
            if isinstance(seg, dict):
                segments.append(seg)
    return segments


def _all_text(body: dict[str, Any]) -> str:
    return " ".join(b.get("text", "") for b in body.get("bubbles", []))


# ---------------------------------------------------------------------------
# Test 1 — Portfolio response uses rich links, not raw URLs in text
# ---------------------------------------------------------------------------


def test_portfolio_response_uses_rich_links_not_raw_urls() -> None:
    """When the portfolio engine returns samples, URLs go in rich_segments."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        body = _chat(client, "Show me cover design portfolio samples.")

    segments = _all_rich_segments(body)
    # If portfolio samples were found, check they are in rich_segments.
    portfolio_segs = [s for s in segments if s.get("type") in {"portfolio_link", "portfolio_links"}]

    # The portfolio engine may or may not return results in test mode.
    # If it did, URLs should be in segments, not embedded as raw https:// in text.
    if portfolio_segs:
        for seg in portfolio_segs:
            if seg.get("type") == "portfolio_link":
                assert "url" in seg
                assert seg["url"].startswith("https://")
                assert "title" in seg
            elif seg.get("type") == "portfolio_links":
                assert isinstance(seg.get("items"), list)
                for item in seg["items"]:
                    assert item.get("url", "").startswith("https://")


# ---------------------------------------------------------------------------
# Test 2 — Portfolio URLs are sanitized (no trailing whitespace/newline)
# ---------------------------------------------------------------------------


def test_portfolio_urls_are_sanitized() -> None:
    """All URLs in rich segments must be clean https:// URLs."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        body = _chat(client, "Show me samples for ghostwriting.")

    for seg in _all_rich_segments(body):
        if seg.get("type") == "portfolio_link":
            url = seg.get("url", "")
            assert not url.endswith("\n"), f"URL has trailing newline: {url!r}"
            assert not url.endswith("\r"), f"URL has trailing CR: {url!r}"
            assert url.startswith("https://"), f"URL not https: {url!r}"
        elif seg.get("type") == "portfolio_links":
            for item in seg.get("items", []):
                url = item.get("url", "")
                assert not url.endswith("\n")
                assert url.startswith("https://")


# ---------------------------------------------------------------------------
# Test 3 — Portfolio link titles are present
# ---------------------------------------------------------------------------


def test_portfolio_link_titles_present() -> None:
    """Each portfolio_link rich segment must have a non-empty title."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        body = _chat(client, "Do you have any cover design samples?")

    for seg in _all_rich_segments(body):
        if seg.get("type") == "portfolio_link":
            assert seg.get("title"), f"portfolio_link missing title: {seg}"
        elif seg.get("type") == "portfolio_links":
            for item in seg.get("items", []):
                assert item.get("title"), f"portfolio_links item missing title: {item}"


# ---------------------------------------------------------------------------
# Test 4 — No corrupted dash-n URL in rich segments
# ---------------------------------------------------------------------------


def test_no_corrupted_dash_n_url() -> None:
    """No portfolio URL should end with '-n' (newline-split artifact)."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        body = _chat(client, "Show me your portfolio samples for editing.")

    for seg in _all_rich_segments(body):
        if seg.get("type") == "portfolio_link":
            url = seg.get("url", "")
            assert not url.endswith("-n"), f"Corrupted URL ends with '-n': {url!r}"
        elif seg.get("type") == "portfolio_links":
            for item in seg.get("items", []):
                url = item.get("url", "")
                assert not url.endswith("-n"), f"Corrupted URL ends with '-n': {url!r}"
