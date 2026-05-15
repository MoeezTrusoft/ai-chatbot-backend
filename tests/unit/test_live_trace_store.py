from pathlib import Path

from bookcraft.components.analysis import LiveTraceStore


def test_live_trace_store_appends_latest_and_filters_thread(tmp_path: Path) -> None:
    path = tmp_path / "traces" / "chat_turns.jsonl"
    store = LiveTraceStore(path)

    store.append(
        {
            "thread_id": "thread-a",
            "message_preview": "email me at user@example.com",
            "elapsed_ms": 10,
            "recorded_at": "2026-05-15T21:11:22.123456+00:00",
        }
    )
    store.append(
        {
            "thread_id": "thread-b",
            "message_preview": "visit https://example.com",
            "elapsed_ms": 20,
        }
    )
    store.append(
        {
            "thread_id": "thread-a",
            "message_preview": "second message",
            "elapsed_ms": 30,
        }
    )

    latest = store.latest(limit=2)
    assert [row["thread_id"] for row in latest] == ["thread-a", "thread-b"]

    thread_rows = store.for_thread("thread-a")
    assert [row["elapsed_ms"] for row in thread_rows] == [30, 10]

    contents = path.read_text()
    assert "[REDACTED_EMAIL]" in contents
    assert "[REDACTED_URL]" in contents
    assert "2026-05-15T21:11:22.123456+00:00" in contents
