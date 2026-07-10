"""Real incremental streaming tests for SonnetResponseGenerator.stream().

Covers both paths of P4-T1:

* the offline fallback (no streaming-capable adapter) — must yield multiple
  word-group chunks whose concatenation equals what ``generate()`` produces; and
* the real streaming path — a fake adapter whose ``stream_text`` yields deltas
  is forwarded chunk-for-chunk, and a fake adapter whose ``stream_text`` raises
  mid-stream falls back to ``generate()`` without leaking the exception.

These run fully offline (no live API).
"""
from __future__ import annotations

import inspect
from collections.abc import AsyncIterator

import pytest

from bookcraft.components.extraction.schemas import CombinedExtraction
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.preprocessor.schemas import ProcessedMessage, TokenInfo
from bookcraft.components.response.generator import SonnetResponseGenerator
from bookcraft.domain.enums import QueryIntentType, SalesStage
from bookcraft.domain.state import ThreadState


def _message(text: str = "hi there friend") -> ProcessedMessage:
    tokens = [TokenInfo(text=text, lemma=text, start=0, end=len(text))]
    return ProcessedMessage(
        raw=text,
        normalized=text,
        tokens=tokens,
        negation_spans=[],
        hedge_spans=[],
        counterfactual_spans=[],
        deterministic_atoms={},
        embedding=[],
        language="en",
        char_count=len(text),
    )


def _intent() -> IntentVote:
    return IntentVote(
        query_primary=QueryIntentType.GREETING,
        service_primary=None,
        funnel_stage=SalesStage.NEW,
        needs_clarification=False,
        confidence=0.95,
        rationale="test",
        evidence=[],
    )


async def _collect(gen, msg) -> list[str]:
    chunks: list[str] = []
    async for chunk in gen.stream(
        message=msg,
        state=ThreadState(),
        intent=_intent(),
        extraction=CombinedExtraction(),
    ):
        chunks.append(chunk)
    return chunks


# ---------------------------------------------------------------------------
# Fake streaming adapters (no live API).
# ---------------------------------------------------------------------------


class _FakeStreamingAdapter:
    """Adapter whose stream_text yields a fixed list of deltas.

    Carries an ``api_key`` so the generator treats streaming as viable.
    """

    name = "fake"
    api_key = "sk-fake"

    def __init__(self, deltas: list[str]) -> None:
        self._deltas = deltas

    async def stream_text(
        self,
        *,
        system: str,
        messages: list[dict[str, object]],
        max_tokens: int = 1024,
        purpose: str = "response_stream",
        system_cache_suffix: str | None = None,
    ) -> AsyncIterator[str]:
        del system, messages, max_tokens, purpose, system_cache_suffix
        for delta in self._deltas:
            yield delta

    async def structured(self, *, system, user, output_model, purpose, system_cache_suffix=None):  # pragma: no cover
        del system, user, purpose, system_cache_suffix
        return output_model.model_validate({})


class _FakeRaisingAdapter:
    """Adapter whose stream_text yields one chunk then raises mid-stream."""

    name = "fake_raising"
    api_key = "sk-fake"

    async def stream_text(
        self,
        *,
        system: str,
        messages: list[dict[str, object]],
        max_tokens: int = 1024,
        purpose: str = "response_stream",
        system_cache_suffix: str | None = None,
    ) -> AsyncIterator[str]:
        del system, messages, max_tokens, purpose, system_cache_suffix
        yield "partial "
        raise RuntimeError("stream blew up mid-flight")

    async def structured(self, *, system, user, output_model, purpose, system_cache_suffix=None):  # pragma: no cover
        del system, user, purpose, system_cache_suffix
        return output_model.model_validate({})


# ---------------------------------------------------------------------------
# Shape.
# ---------------------------------------------------------------------------


def test_stream_is_async_generator_function():
    assert inspect.isasyncgenfunction(SonnetResponseGenerator.stream)


# ---------------------------------------------------------------------------
# Fallback path (no streaming-capable adapter).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fallback_yields_chunks_concatenating_to_generate_output():
    gen = SonnetResponseGenerator(adapter=None)
    msg = _message()

    chunks = await _collect(gen, msg)

    assert len(chunks) >= 1
    assert all(isinstance(c, str) for c in chunks)

    combined = "".join(chunks)
    assert combined  # non-empty

    draft = await gen.generate(
        message=msg,
        state=ThreadState(),
        intent=_intent(),
        extraction=CombinedExtraction(),
    )
    assert combined == draft.text


@pytest.mark.asyncio
async def test_fallback_is_multi_chunk_for_multiword_response():
    """The word-group fallback must split a multi-word reply into >1 chunk."""
    gen = SonnetResponseGenerator(adapter=None)
    msg = _message()

    chunks = await _collect(gen, msg)

    # The greeting fallback is well over five words, so chunking must produce
    # more than one chunk — demonstrating genuinely incremental delivery.
    assert len(chunks) > 1


def test_chunk_text_helper_is_lossless_and_multichunk():
    from bookcraft.components.response.generator import _chunk_text

    text = "one two three four five six seven eight nine ten eleven"
    chunks = _chunk_text(text)
    assert len(chunks) > 1
    assert "".join(chunks) == text

    # Edge cases: single word / empty still yield exactly one chunk.
    assert _chunk_text("solo") == ["solo"]
    assert _chunk_text("") == [""]


# ---------------------------------------------------------------------------
# Real streaming path.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_adapter_chunks_are_forwarded_and_accumulated():
    """A streaming adapter's deltas are yielded as-is and accumulate to the full text."""
    deltas = ["Hel", "lo ", "wor", "ld"]
    gen = SonnetResponseGenerator(adapter=_FakeStreamingAdapter(deltas))
    msg = _message()

    chunks = await _collect(gen, msg)

    assert chunks == deltas
    assert "".join(chunks) == "Hello world"


@pytest.mark.asyncio
async def test_streaming_failure_falls_back_to_generate_without_raising():
    """A mid-stream exception must be swallowed and generate() used instead."""
    gen = SonnetResponseGenerator(adapter=_FakeRaisingAdapter())
    msg = _message()

    # Must NOT raise — the streaming failure is contained.
    chunks = await _collect(gen, msg)

    combined = "".join(chunks)
    assert combined  # a complete, non-empty fallback response was produced
    assert all(isinstance(c, str) for c in chunks)

    # The fallback yields generate()'s text (possibly after a leaked partial).
    draft = await gen.generate(
        message=msg,
        state=ThreadState(),
        intent=_intent(),
        extraction=CombinedExtraction(),
    )
    assert draft.text in combined
