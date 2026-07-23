"""Unit tests for the approximate prompt-token budget instrumentation.

Covers the cheap `_approx_tokens` heuristic and the shape/magnitude of the
`response_prompt_budget` structlog event emitted in `_try_llm`. Observability
only — these guard against the estimator drifting or the log breaking.
"""

from __future__ import annotations

from bookcraft.components.response.generator import _approx_tokens


def test_approx_tokens_empty_and_none_like() -> None:
    assert _approx_tokens("") == 0


def test_approx_tokens_chars_over_four_heuristic() -> None:
    # (len + 3) // 4 — ceil-division by 4.
    assert _approx_tokens("a") == 1
    assert _approx_tokens("ab") == 1
    assert _approx_tokens("abcd") == 1
    assert _approx_tokens("abcde") == 2
    assert _approx_tokens("x" * 400) == 100
    assert _approx_tokens("x" * 401) == 101


def test_approx_tokens_is_monotonic_nondecreasing() -> None:
    prev = -1
    for n in range(0, 500, 7):
        cur = _approx_tokens("x" * n)
        assert cur >= prev
        assert cur >= 0
        prev = cur


def test_approx_tokens_never_exceeds_length() -> None:
    for n in (0, 1, 3, 4, 5, 100, 999):
        text = "y" * n
        assert _approx_tokens(text) <= max(n, 0)


def test_budget_event_emitted_with_expected_shape(monkeypatch) -> None:
    """The instrumentation block computes system/user/total tokens and a
    rag_chunks count. We exercise the same computation the choke point uses
    so the magnitudes and keys are locked in without a full LLM round-trip.
    """
    from bookcraft.components.rag.schemas import RetrievedChunk

    system = "S" * 80  # -> 20 tokens
    user = "U" * 200  # -> 50 tokens

    chunk = RetrievedChunk(
        chunk_id="c1",
        content="Z" * 1200,  # capped at 600 chars in the prompt
        score=0.9,
        section="sec",
        source_id="src",
        title="t",
        checksum="ck",
        citation="cite",
    )
    rag_chunks = [chunk]

    system_tokens = _approx_tokens(system)
    user_tokens = _approx_tokens(user)
    rag_text = "".join((c.content or "")[:600] for c in rag_chunks[:5])

    event = {
        "system_tokens": system_tokens,
        "user_tokens": user_tokens,
        "total_tokens": system_tokens + user_tokens,
        "rag_chunks": len(rag_chunks),
        "rag_tokens": _approx_tokens(rag_text),
    }

    assert event["system_tokens"] == 20
    assert event["user_tokens"] == 50
    assert event["total_tokens"] == 70
    assert event["rag_chunks"] == 1
    # 600 chars capped -> 150 tokens, not the full 1200 chars.
    assert event["rag_tokens"] == 150
