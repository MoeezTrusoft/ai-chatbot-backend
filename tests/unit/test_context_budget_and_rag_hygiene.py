"""Advisory items 5 (bound structured-state growth) and 4 (RAG hygiene).

Item 5: ``ContextPack.known_facts`` grows unbounded and is injected every turn.
``_select_rendered_facts`` / ``_context_pack_prompt_section`` cap what is RENDERED
per turn to the top-K, always keeping contact + active-service facts. Persisted state
is never pruned — only the per-turn rendering is bounded.

Item 4: RAG chunks are injected every turn; ``_dedupe_rag_snippets`` collapses
identical / near-duplicate chunk text WITHIN a single prompt to cut tokens and
verbatim-bleed surface without touching retrieval.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from bookcraft.components.context.schemas import ContextPack, KnownFact
from bookcraft.components.extraction.schemas import CombinedExtraction
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.components.response.generator import (
    _context_pack_prompt_section,
    _dedupe_rag_snippets,
    _response_user_prompt,
    _select_rendered_facts,
)
from bookcraft.domain.enums import QueryIntentType, SalesStage
from bookcraft.domain.state import ThreadState


def _intent() -> IntentVote:
    return IntentVote(
        query_primary=QueryIntentType.SERVICE_QUESTION,
        query_secondary=[],
        funnel_stage=SalesStage.SERVICE_DISCOVERY,
        needs_clarification=False,
        confidence=0.9,
        rationale="test",
        evidence=[],
    )


def _processed(text: str = "tell me about editing") -> ProcessedMessage:
    return ProcessedMessage(
        raw=text,
        normalized=text,
        language="en",
        tokens=[],
        negation_spans=[],
        hedge_spans=[],
        counterfactual_spans=[],
        deterministic_atoms={},
        embedding=[],
        char_count=len(text),
        negation_targets=[],
    )


def _rag_chunk(content: str) -> MagicMock:
    c = MagicMock()
    c.content = content
    return c


def _fact(path: str, value: str, confidence: float) -> KnownFact:
    return KnownFact(
        path=path,
        label=path.split(".")[-1],
        value=value,
        confidence=confidence,
        source="chat",
    )


# ---------------------------------------------------------------------------
# Item 5: bound structured-state growth (known-facts render cap)
# ---------------------------------------------------------------------------


class TestSelectRenderedFacts:
    def test_cap_zero_is_noop(self) -> None:
        facts = [_fact(f"project.p{i}", f"v{i}", 0.5) for i in range(10)]
        assert _select_rendered_facts(facts, 0) == facts

    def test_list_within_cap_unchanged(self) -> None:
        facts = [_fact(f"project.p{i}", f"v{i}", 0.5) for i in range(3)]
        assert _select_rendered_facts(facts, 5) == facts

    def test_caps_to_k_facts(self) -> None:
        facts = [_fact(f"project.p{i}", f"v{i}", 0.5) for i in range(10)]
        out = _select_rendered_facts(facts, 4)
        assert len(out) == 4

    def test_always_keeps_contact_and_service_facts(self) -> None:
        contact = _fact("personal.email", "a@b.com", 0.4)
        service = _fact("service.editing.level", "developmental", 0.4)
        # Many high-confidence project facts that would otherwise crowd them out.
        project = [_fact(f"project.p{i}", f"v{i}", 0.99) for i in range(10)]
        facts = project[:5] + [contact, service] + project[5:]
        out = _select_rendered_facts(facts, 3)
        # Contact + active-service facts survive even a tight cap...
        assert contact in out
        assert service in out
        # ...and are never dropped even though that pushes past the nominal cap.
        assert len(out) >= 2

    def test_fills_remaining_slots_by_confidence(self) -> None:
        low = _fact("project.low", "lo", 0.10)
        mid = _fact("project.mid", "mi", 0.50)
        high = _fact("project.high", "hi", 0.95)
        contact = _fact("personal.phone", "555", 0.30)
        facts = [low, mid, high, contact]
        # cap 2: contact always kept (1 slot used), 1 slot left → highest-confidence.
        out = _select_rendered_facts(facts, 2)
        assert contact in out
        assert high in out
        assert low not in out and mid not in out

    def test_preserves_original_order(self) -> None:
        a = _fact("project.a", "a", 0.9)
        b = _fact("project.b", "b", 0.8)
        c = _fact("project.c", "c", 0.7)
        out = _select_rendered_facts([a, b, c], 2)
        # a and b kept (highest confidence), and in original order.
        assert out == [a, b]


class TestContextPackPromptSectionCap:
    def _pack(self, facts: list[KnownFact]) -> ContextPack:
        return ContextPack(known_facts=facts, active_service="editing")

    def test_default_no_cap_renders_all(self) -> None:
        facts = [_fact(f"project.p{i}", f"val{i}", 0.5) for i in range(8)]
        section = _context_pack_prompt_section(self._pack(facts))
        for i in range(8):
            assert f"val{i}" in section

    def test_cap_limits_rendered_facts(self) -> None:
        facts = [_fact(f"project.p{i}", f"val{i}", 0.5) for i in range(8)]
        section = _context_pack_prompt_section(self._pack(facts), fact_render_cap=3)
        rendered = [i for i in range(8) if f"val{i}" in section]
        assert len(rendered) == 3

    def test_cap_keeps_contact_and_service(self) -> None:
        contact = _fact("personal.email", "author@example.com", 0.4)
        service = _fact("service.editing.tier", "line-edit", 0.4)
        project = [_fact(f"project.p{i}", f"val{i}", 0.99) for i in range(8)]
        facts = project + [contact, service]
        section = _context_pack_prompt_section(self._pack(facts), fact_render_cap=2)
        assert "author@example.com" in section
        assert "line-edit" in section


def test_response_user_prompt_applies_fact_cap() -> None:
    contact = _fact("personal.phone", "5551234567", 0.4)
    service = _fact("service.editing.tier", "developmental", 0.4)
    project = [_fact(f"project.p{i}", f"projval{i}", 0.99) for i in range(8)]
    pack = ContextPack(
        known_facts=project + [contact, service],
        active_service="editing",
    )
    prompt = _response_user_prompt(
        message=_processed(),
        state=ThreadState(),
        intent=_intent(),
        extraction=CombinedExtraction(state_deltas=[]),
        rag_chunks=[],
        route_name="direct_answer",
        runtime_atoms={},
        context_pack=pack,
        known_facts_render_cap=3,
    )
    # Always-keep facts survive the cap.
    assert "5551234567" in prompt
    assert "developmental" in prompt
    # The full set of project facts is NOT all rendered under the cap.
    rendered_project = sum(1 for i in range(8) if f"projval{i}" in prompt)
    assert rendered_project < 8


def test_response_user_prompt_uncapped_by_default() -> None:
    project = [_fact(f"project.p{i}", f"projval{i}", 0.5) for i in range(8)]
    pack = ContextPack(known_facts=project, active_service="editing")
    prompt = _response_user_prompt(
        message=_processed(),
        state=ThreadState(),
        intent=_intent(),
        extraction=CombinedExtraction(state_deltas=[]),
        rag_chunks=[],
        route_name="direct_answer",
        runtime_atoms={},
        context_pack=pack,
    )
    for i in range(8):
        assert f"projval{i}" in prompt


# ---------------------------------------------------------------------------
# Item 4: RAG hygiene (within-prompt de-duplication)
# ---------------------------------------------------------------------------


class TestDedupeRagSnippets:
    def test_exact_duplicates_collapsed(self) -> None:
        out = _dedupe_rag_snippets(["alpha beta", "alpha beta", "gamma"])
        assert out == ["alpha beta", "gamma"]

    def test_near_duplicate_whitespace_case_collapsed(self) -> None:
        out = _dedupe_rag_snippets(["Alpha  Beta", "alpha beta"])
        assert out == ["Alpha  Beta"]

    def test_subset_snippet_collapsed(self) -> None:
        longer = "editing includes developmental and line editing services"
        shorter = "line editing services"
        out = _dedupe_rag_snippets([longer, shorter])
        assert out == [longer]

    def test_distinct_snippets_preserved_in_order(self) -> None:
        out = _dedupe_rag_snippets(["one thing", "another thing", "third thing"])
        assert out == ["one thing", "another thing", "third thing"]

    def test_empty_snippets_skipped(self) -> None:
        assert _dedupe_rag_snippets(["", "   ", "real"]) == ["real"]


def test_response_user_prompt_collapses_duplicate_rag_chunks() -> None:
    dup = "BookCraft offers developmental editing for fiction manuscripts."
    other = "Cover design turnaround is typically two weeks."
    chunks = [_rag_chunk(dup), _rag_chunk(dup), _rag_chunk(other)]
    prompt = _response_user_prompt(
        message=_processed(),
        state=ThreadState(),
        intent=_intent(),
        extraction=CombinedExtraction(state_deltas=[]),
        rag_chunks=chunks,
        route_name="direct_answer",
        runtime_atoms={},
        rag_within_prompt_dedup=True,
    )
    # The duplicated passage appears exactly once; the distinct one still appears.
    assert prompt.count(dup) == 1
    assert other in prompt


def test_response_user_prompt_dedup_can_be_disabled() -> None:
    dup = "BookCraft offers developmental editing for fiction manuscripts."
    chunks = [_rag_chunk(dup), _rag_chunk(dup)]
    prompt = _response_user_prompt(
        message=_processed(),
        state=ThreadState(),
        intent=_intent(),
        extraction=CombinedExtraction(state_deltas=[]),
        rag_chunks=chunks,
        route_name="direct_answer",
        runtime_atoms={},
        rag_within_prompt_dedup=False,
    )
    # With dedup off, the duplicated passage is rendered twice (current behavior).
    assert prompt.count(dup) == 2
