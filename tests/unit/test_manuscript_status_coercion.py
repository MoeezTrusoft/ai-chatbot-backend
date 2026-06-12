"""Regression tests for manuscript_status enum coercion.

Production incident (2026-06-12): the LLM metadata extractor emitted its coarse
prompt vocabulary (``not_started`` / ``notes_only`` / ``early_draft`` /
``full_draft`` / ``editing_complete``) directly into ThreadState. None of these
are valid ``ManuscriptStatus`` members, so the value round-tripped in memory but
crashed ``ThreadState.model_validate`` on the next load — 500-ing every
subsequent turn of that thread (e.g. the "story is still in my head" / idea-only
leads, whose extractor output is ``not_started``).
"""

from __future__ import annotations

from bookcraft.components.extraction.llm_extractor import _facts_to_deltas
from bookcraft.components.extraction.llm_schemas import ExtractedValue, LLMExtractedFacts
from bookcraft.domain.enums import ManuscriptStatus, coerce_manuscript_status
from bookcraft.domain.state import ThreadState


def test_coerce_maps_llm_vocabulary_to_canonical_enum() -> None:
    assert coerce_manuscript_status("not_started") is ManuscriptStatus.IDEA
    assert coerce_manuscript_status("notes_only") is ManuscriptStatus.ROUGH_NOTES
    assert coerce_manuscript_status("early_draft") is ManuscriptStatus.PARTIAL_DRAFT
    assert coerce_manuscript_status("full_draft") is ManuscriptStatus.DRAFT
    assert coerce_manuscript_status("editing_complete") is ManuscriptStatus.EDITED


def test_coerce_accepts_canonical_members_and_is_case_insensitive() -> None:
    assert coerce_manuscript_status("partial_draft") is ManuscriptStatus.PARTIAL_DRAFT
    assert coerce_manuscript_status("NOT_STARTED") is ManuscriptStatus.IDEA
    assert coerce_manuscript_status(ManuscriptStatus.DRAFT) is ManuscriptStatus.DRAFT


def test_coerce_returns_none_for_unmappable_or_empty() -> None:
    assert coerce_manuscript_status("garbage_status") is None
    assert coerce_manuscript_status("") is None
    assert coerce_manuscript_status(None) is None


def test_llm_extractor_normalizes_manuscript_status_delta() -> None:
    facts = LLMExtractedFacts(
        manuscript_status=ExtractedValue(
            value="not_started", confidence=0.92, source_quote="still in my head"
        )
    )
    deltas = [d for d in _facts_to_deltas(facts) if d.path == "project.manuscript_status"]
    assert len(deltas) == 1
    # Must be a valid enum value, not the raw prompt vocabulary.
    assert deltas[0].value == ManuscriptStatus.IDEA.value
    assert ManuscriptStatus(deltas[0].value) is ManuscriptStatus.IDEA


def test_llm_extractor_drops_unmappable_manuscript_status() -> None:
    facts = LLMExtractedFacts(
        manuscript_status=ExtractedValue(value="??", confidence=0.92, source_quote="??")
    )
    deltas = [d for d in _facts_to_deltas(facts) if d.path == "project.manuscript_status"]
    assert deltas == []


def _poisoned_state(raw_value: str) -> dict:
    state = ThreadState().model_dump(mode="json")
    state["project"]["manuscript_status"] = {
        "value": raw_value,
        "confidence": 0.9,
        "source": "ai_extracted",
        "extracted_at": "2026-06-12T20:48:00Z",
        "extracted_by": "llm_metadata_extractor.v1",
        "raw_excerpt": None,
    }
    return state


def test_poisoned_thread_loads_with_coerced_status() -> None:
    # Pre-fix persisted value must not crash on load — it is the production 500.
    loaded = ThreadState.model_validate(_poisoned_state("not_started"))
    assert loaded.project.manuscript_status.value is ManuscriptStatus.IDEA


def test_poisoned_thread_with_unmappable_status_clears_instead_of_crashing() -> None:
    loaded = ThreadState.model_validate(_poisoned_state("totally_unknown"))
    assert loaded.project.manuscript_status.value is None


def test_canonical_status_loads_untouched() -> None:
    loaded = ThreadState.model_validate(_poisoned_state("partial_draft"))
    assert loaded.project.manuscript_status.value is ManuscriptStatus.PARTIAL_DRAFT
