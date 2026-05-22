"""Unit tests for the ServiceWorkflow module.

Covers:
- Single-service advice: prerequisites, parallel, next steps
- Out-of-order detection
- Multi-service ordering and parallel opportunities
- Alias resolution
- Sequencing-question detection
- Prompt fact generation
- Full pipeline display
"""

from __future__ import annotations

import pytest

from bookcraft.components.service_workflow import (
    ServiceWorkflow,
    is_sequencing_question,
    resolve_service_aliases,
)

wf = ServiceWorkflow()


# ---------------------------------------------------------------------------
# 1. Single-service advice
# ---------------------------------------------------------------------------


def test_ghostwriting_has_no_prerequisites() -> None:
    advice = wf.advise(requested_service="ghostwriting")
    assert advice is not None
    assert advice.can_start_now is True
    assert advice.blocking_predecessors == []
    assert advice.ordering_violation is False


def test_ghostwriting_successor_is_editing() -> None:
    advice = wf.advise(requested_service="ghostwriting")
    assert advice is not None
    assert "editing_proofreading" in advice.next_steps


def test_editing_requires_ghostwriting() -> None:
    advice = wf.advise(requested_service="editing_proofreading")
    assert advice is not None
    assert "ghostwriting" in advice.blocking_predecessors
    assert advice.can_start_now is False
    assert advice.ordering_violation is True


def test_editing_can_start_if_ghostwriting_done() -> None:
    advice = wf.advise(
        requested_service="editing_proofreading",
        completed_services=["ghostwriting"],
    )
    assert advice is not None
    assert advice.can_start_now is True
    assert advice.blocking_predecessors == []


def test_editing_parallel_with_cover_design() -> None:
    advice = wf.advise(
        requested_service="editing_proofreading",
        completed_services=["ghostwriting"],
    )
    assert advice is not None
    assert "cover_design_illustration" in advice.parallel_services


def test_cover_design_parallel_with_editing() -> None:
    advice = wf.advise(
        requested_service="cover_design_illustration",
        completed_services=["ghostwriting"],
    )
    assert advice is not None
    assert "editing_proofreading" in advice.parallel_services


def test_formatting_blocked_until_editing_and_cover_done() -> None:
    advice = wf.advise(
        requested_service="interior_formatting",
        completed_services=["ghostwriting"],
    )
    assert advice is not None
    assert advice.can_start_now is False
    assert "editing_proofreading" in advice.blocking_predecessors
    assert "cover_design_illustration" in advice.blocking_predecessors


def test_formatting_can_start_when_all_done() -> None:
    advice = wf.advise(
        requested_service="interior_formatting",
        completed_services=[
            "ghostwriting", "editing_proofreading", "cover_design_illustration"
        ],
    )
    assert advice is not None
    assert advice.can_start_now is True


def test_publishing_successors_include_marketing() -> None:
    advice = wf.advise(
        requested_service="publishing_distribution",
        completed_services=[
            "ghostwriting", "editing_proofreading",
            "cover_design_illustration", "interior_formatting",
        ],
    )
    assert advice is not None
    assert "marketing_promotion" in advice.next_steps


def test_publishing_parallel_with_marketing_and_website() -> None:
    advice = wf.advise(
        requested_service="publishing_distribution",
        completed_services=[
            "ghostwriting", "editing_proofreading",
            "cover_design_illustration", "interior_formatting",
        ],
    )
    assert advice is not None
    assert "marketing_promotion" in advice.parallel_services
    assert "author_website" in advice.parallel_services
    assert "video_trailer" in advice.parallel_services


def test_audiobook_blocked_until_formatting_done() -> None:
    advice = wf.advise(
        requested_service="audiobook_production",
        completed_services=["ghostwriting", "editing_proofreading", "cover_design_illustration"],
    )
    assert advice is not None
    assert advice.can_start_now is False
    assert "interior_formatting" in advice.blocking_predecessors


def test_unknown_service_returns_none() -> None:
    result = wf.advise(requested_service="unknown_service_xyz")
    assert result is None


# ---------------------------------------------------------------------------
# 2. Ordering violation detection
# ---------------------------------------------------------------------------


def test_detect_violation_publishing_no_predecessors_done() -> None:
    missing = wf.detect_violation("publishing_distribution", completed_services=[])
    assert "interior_formatting" in missing
    assert "editing_proofreading" in missing


def test_detect_violation_none_when_all_done() -> None:
    missing = wf.detect_violation(
        "publishing_distribution",
        completed_services=[
            "ghostwriting", "editing_proofreading",
            "cover_design_illustration", "interior_formatting",
        ],
    )
    assert missing == []


def test_detect_violation_ghostwriting_always_empty() -> None:
    assert wf.detect_violation("ghostwriting", completed_services=[]) == []


# ---------------------------------------------------------------------------
# 3. Milestone: next steps after completing a service
# ---------------------------------------------------------------------------


def test_next_steps_after_ghostwriting() -> None:
    steps = wf.next_steps_after("ghostwriting")
    assert "editing_proofreading" in steps


def test_next_steps_after_editing() -> None:
    steps = wf.next_steps_after("editing_proofreading")
    assert "interior_formatting" in steps
    assert "audiobook_production" in steps


def test_next_steps_after_marketing_is_empty() -> None:
    steps = wf.next_steps_after("marketing_promotion")
    assert steps == []


def test_next_steps_after_unknown_is_empty() -> None:
    assert wf.next_steps_after("nonexistent") == []


# ---------------------------------------------------------------------------
# 4. Multi-service advice
# ---------------------------------------------------------------------------


def test_multi_service_ordering_editing_before_formatting() -> None:
    advice = wf.advise_multi(["interior_formatting", "editing_proofreading", "ghostwriting"])
    assert advice.ordered_sequence.index("ghostwriting") < advice.ordered_sequence.index(
        "editing_proofreading"
    )
    assert advice.ordered_sequence.index("editing_proofreading") < advice.ordered_sequence.index(
        "interior_formatting"
    )


def test_multi_service_detects_parallel_editing_and_cover() -> None:
    advice = wf.advise_multi(["editing_proofreading", "cover_design_illustration"])
    pair_sets = [frozenset(p) for p in advice.parallel_opportunities]
    assert frozenset({"editing_proofreading", "cover_design_illustration"}) in pair_sets


def test_multi_service_can_start_immediately() -> None:
    advice = wf.advise_multi(["ghostwriting", "editing_proofreading"])
    assert "ghostwriting" in advice.can_start_immediately
    assert "editing_proofreading" not in advice.can_start_immediately


def test_multi_service_single_returns_sensible() -> None:
    advice = wf.advise_multi(["ghostwriting"])
    assert advice.ordered_sequence == ["ghostwriting"]
    assert "ghostwriting" in advice.summary.lower()


def test_multi_service_empty_returns_empty_advice() -> None:
    advice = wf.advise_multi([])
    assert advice.ordered_sequence == []


# ---------------------------------------------------------------------------
# 5. Alias resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected_service",
    [
        ("I need editing for my book", "editing_proofreading"),
        ("I want a book cover", "cover_design_illustration"),
        ("Can you do formatting?", "interior_formatting"),
        ("I need publishing on Amazon", "publishing_distribution"),
        ("What about marketing?", "marketing_promotion"),
        ("I want an audiobook", "audiobook_production"),
        ("Can you make a book trailer?", "video_trailer"),
        ("I need an author website", "author_website"),
        ("I need help with ghostwriting", "ghostwriting"),
    ],
)
def test_alias_resolution(text: str, expected_service: str) -> None:
    resolved = resolve_service_aliases(text)
    assert expected_service in resolved, (
        f"Expected '{expected_service}' in resolved aliases for: {text!r}, got: {resolved}"
    )


def test_alias_resolution_multi_service() -> None:
    text = "I need editing and cover design and formatting"
    resolved = resolve_service_aliases(text)
    assert "editing_proofreading" in resolved
    assert "cover_design_illustration" in resolved
    assert "interior_formatting" in resolved


# ---------------------------------------------------------------------------
# 6. Sequencing-question detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "What comes next after editing?",
        "Can I do editing and cover design at the same time?",
        "Can we do both simultaneously?",
        "What is the order of steps?",
        "Can I do formatting while editing is happening?",
        "What's the full process from start to finish?",
        "What comes before publishing?",
        "What is the sequence?",
        "Can they run in parallel?",
    ],
)
def test_sequencing_question_detected(text: str) -> None:
    assert is_sequencing_question(text), f"Should detect as sequencing question: {text!r}"


@pytest.mark.parametrize(
    "text",
    [
        "How much does editing cost?",
        "I need cover design for my novel.",
        "Maya Author maya@example.com",
        "I want to book a consultation.",
    ],
)
def test_non_sequencing_question_not_detected(text: str) -> None:
    assert not is_sequencing_question(text), f"Should NOT detect: {text!r}"


# ---------------------------------------------------------------------------
# 7. Prompt facts quality
# ---------------------------------------------------------------------------


def test_prompt_facts_editing_with_ghostwriting_done() -> None:
    advice = wf.advise(
        requested_service="editing_proofreading",
        completed_services=["ghostwriting"],
    )
    assert advice is not None
    facts = advice.as_prompt_facts()
    assert "Editing" in facts
    assert "parallel" in facts.lower()
    assert "Cover Design" in facts


def test_prompt_facts_out_of_order_warns() -> None:
    advice = wf.advise(requested_service="interior_formatting")
    assert advice is not None
    assert advice.ordering_violation is True
    facts = advice.as_prompt_facts()
    assert "OUT-OF-ORDER" in facts
    assert "Editing" in facts or "editing" in facts.lower()


def test_prompt_facts_ghostwriting_no_prerequisites_note() -> None:
    advice = wf.advise(requested_service="ghostwriting")
    assert advice is not None
    facts = advice.as_prompt_facts()
    assert "immediately" in facts or "no prerequisite" in facts.lower()


def test_multi_service_prompt_facts_contains_sequence() -> None:
    advice = wf.advise_multi(
        ["ghostwriting", "editing_proofreading", "interior_formatting"]
    )
    facts = advice.as_prompt_facts()
    assert "sequence" in facts.lower() or "→" in facts
    assert "Ghostwriting" in facts


def test_full_pipeline_text_contains_all_services() -> None:
    pipeline = wf.full_pipeline_text()
    for svc_name in [
        "Ghostwriting", "Editing", "Cover Design", "Interior Layout",
        "Publishing", "Marketing", "Audiobook", "Video Trailer", "Author",
    ]:
        assert svc_name in pipeline, f"Pipeline missing: {svc_name}"


def test_full_pipeline_text_highlights_requested_services() -> None:
    pipeline = wf.full_pipeline_text(
        highlight_services=["editing_proofreading", "interior_formatting"]
    )
    assert "your service" in pipeline
    assert "parallel" in pipeline.lower()


# ---------------------------------------------------------------------------
# 8. user_guidance convenience method
# ---------------------------------------------------------------------------


def test_user_guidance_returns_nonempty_for_known_service() -> None:
    result = wf.user_guidance("ghostwriting")
    assert len(result) > 20
    assert "Ghostwriting" in result


def test_user_guidance_empty_for_unknown() -> None:
    result = wf.user_guidance("unknown_xyz")
    assert result == ""
