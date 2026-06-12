"""Tests for the SonnetResponseGenerator.stream() async generator."""
from __future__ import annotations

import inspect

import pytest

from bookcraft.components.response.generator import SonnetResponseGenerator


class TestStreamMethod:
    def test_stream_method_exists(self):
        gen = SonnetResponseGenerator()
        assert hasattr(gen, "stream")

    def test_stream_is_async_generator_function(self):
        gen = SonnetResponseGenerator()
        assert inspect.isasyncgenfunction(gen.stream)

    @pytest.mark.asyncio
    async def test_stream_fallback_yields_text(self):
        """Without adapter, stream() falls back to generate() and yields a single chunk."""
        from bookcraft.components.extraction.schemas import CombinedExtraction
        from bookcraft.components.intent.schemas import IntentVote
        from bookcraft.components.preprocessor.schemas import ProcessedMessage, TokenInfo
        from bookcraft.domain.enums import QueryIntentType, SalesStage
        from bookcraft.domain.state import ThreadState

        gen = SonnetResponseGenerator(adapter=None)

        text = "hi"
        tokens = [TokenInfo(text=text, lemma=text, start=0, end=2)]
        msg = ProcessedMessage(
            raw=text,
            normalized=text,
            tokens=tokens,
            negation_spans=[],
            hedge_spans=[],
            counterfactual_spans=[],
            deterministic_atoms={},
            embedding=[],
            language="en",
            char_count=2,
        )

        chunks = []
        async for chunk in gen.stream(
            message=msg,
            state=ThreadState(),
            intent=IntentVote(
                query_primary=QueryIntentType.GREETING,
                service_primary=None,
                funnel_stage=SalesStage.NEW,
                needs_clarification=False,
                confidence=0.95,
                rationale="test",
                evidence=[],
            ),
            extraction=CombinedExtraction(),
        ):
            chunks.append(chunk)

        assert len(chunks) >= 1
        assert all(isinstance(c, str) for c in chunks)

    @pytest.mark.asyncio
    async def test_stream_yields_nonempty_text(self):
        """stream() must yield at least one non-empty string."""
        from bookcraft.components.extraction.schemas import CombinedExtraction
        from bookcraft.components.intent.schemas import IntentVote
        from bookcraft.components.preprocessor.schemas import ProcessedMessage, TokenInfo
        from bookcraft.domain.enums import QueryIntentType, SalesStage
        from bookcraft.domain.state import ThreadState

        gen = SonnetResponseGenerator(adapter=None)

        text = "hi"
        tokens = [TokenInfo(text=text, lemma=text, start=0, end=2)]
        msg = ProcessedMessage(
            raw=text,
            normalized=text,
            tokens=tokens,
            negation_spans=[],
            hedge_spans=[],
            counterfactual_spans=[],
            deterministic_atoms={},
            embedding=[],
            language="en",
            char_count=2,
        )

        chunks = []
        async for chunk in gen.stream(
            message=msg,
            state=ThreadState(),
            intent=IntentVote(
                query_primary=QueryIntentType.GREETING,
                service_primary=None,
                funnel_stage=SalesStage.NEW,
                needs_clarification=False,
                confidence=0.95,
                rationale="test",
                evidence=[],
            ),
            extraction=CombinedExtraction(),
        ):
            chunks.append(chunk)

        combined = "".join(chunks)
        assert len(combined) > 0

    def test_stream_signature_has_expected_params(self):
        sig = inspect.signature(SonnetResponseGenerator.stream)
        params = sig.parameters
        assert "message" in params
        assert "state" in params
        assert "intent" in params
        assert "extraction" in params
