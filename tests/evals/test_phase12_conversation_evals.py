"""Phase 12 conversation eval assertions.

Validates that all Phase 12 capability metrics meet minimum thresholds
across the full YAML eval suite.  Uses the shared session fixture from
test_conversation_evals.py so all cases run only once per session.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from conversation_runner import (  # noqa: E402
    ConversationEvalResult,
    run_all_cases,
)

EVALS_DIR = Path(__file__).parent / "conversations"

# Phase 12 cases that must pass for capability readiness.
PHASE12_CASES = {
    "new_project_shift",
    "same_project_service_bundle",
    "target_bound_negation",
    "delegated_cover_style",
    "portfolio_fallback_samples",
    "flexible_service_guidance",
    "bookcraft_discretion_consultation",
    "multi_project_memory",
}


@pytest.fixture(scope="session")
def eval_results() -> list[ConversationEvalResult]:
    return run_all_cases(EVALS_DIR)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _metric(result: ConversationEvalResult, key: str, default: float = 0.0) -> float:
    return float(result.metrics.get(key, default))


def _sum_metric(results: list[ConversationEvalResult], key: str) -> float:
    return sum(_metric(r, key) for r in results)


def _format_case(result: ConversationEvalResult) -> str:
    lines = [f"  CASE: {result.case_id}  (turns={result.total_turns})"]
    for f in result.failures[:5]:
        lines.append(f"    • {f}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 1. Phase 12 YAML cases all exist
# ---------------------------------------------------------------------------


def test_all_phase12_cases_present(eval_results: list[ConversationEvalResult]) -> None:
    found = {r.case_id for r in eval_results}
    missing = PHASE12_CASES - found
    assert not missing, f"Phase 12 case(s) missing from results: {missing}"


# ---------------------------------------------------------------------------
# 2. Zero delegated_slot_reask_violations across all cases
# ---------------------------------------------------------------------------


def test_zero_delegated_slot_reask_violations(
    eval_results: list[ConversationEvalResult],
) -> None:
    # Scoped to cases that explicitly test slot delegation (portfolio fallback
    # uses its own portfolio_filter_state mechanism and is excluded).
    _DELEGATION_CASES = {
        "delegated_cover_style",
        "cover_design_children_fiction",
        "service_switch_and_addition",
        "nda_agreement_negation",
        "counterfactual_safety",
    }
    by_id = {r.case_id: r for r in eval_results}
    violating = [
        cid
        for cid in _DELEGATION_CASES
        if cid in by_id and int(_metric(by_id[cid], "delegated_slot_reask_violations")) > 0
    ]
    assert not violating, (
        f"delegated_slot_reask_violations must be 0 in delegation cases: {violating}"
    )


# ---------------------------------------------------------------------------
# 3. Zero tool safety violations
# ---------------------------------------------------------------------------


def test_zero_tool_safety_violations(eval_results: list[ConversationEvalResult]) -> None:
    total = int(_sum_metric(eval_results, "tool_safety_violations"))
    assert total == 0, f"tool_safety_violations must be 0, got {total}"


# ---------------------------------------------------------------------------
# 4. Zero internal artifact violations
# ---------------------------------------------------------------------------


def test_zero_internal_artifact_violations(eval_results: list[ConversationEvalResult]) -> None:
    total = int(_sum_metric(eval_results, "internal_artifact_violations"))
    assert total == 0, f"internal_artifact_violations must be 0, got {total}"


# ---------------------------------------------------------------------------
# 5. Project shift accuracy 100% for project shift cases
# ---------------------------------------------------------------------------


def test_project_shift_accuracy_for_project_cases(
    eval_results: list[ConversationEvalResult],
) -> None:
    project_cases = ["new_project_shift", "same_project_service_bundle", "multi_project_memory"]
    by_id = {r.case_id: r for r in eval_results}

    failing: list[str] = []
    for cid in project_cases:
        if cid not in by_id:
            continue
        acc = _metric(by_id[cid], "project_shift_accuracy", default=1.0)
        if acc < 1.0:
            failing.append(f"{cid}: project_shift_accuracy={acc:.0%}")

    assert not failing, "project_shift_accuracy must be 100% for project cases:\n" + "\n".join(
        f"  {f}" for f in failing
    )


# ---------------------------------------------------------------------------
# 6. Negation target accuracy 100% for negation cases
# ---------------------------------------------------------------------------


def test_negation_target_accuracy_for_negation_cases(
    eval_results: list[ConversationEvalResult],
) -> None:
    negation_cases = ["target_bound_negation"]
    by_id = {r.case_id: r for r in eval_results}

    failing: list[str] = []
    for cid in negation_cases:
        if cid not in by_id:
            continue
        acc = _metric(by_id[cid], "negation_target_accuracy", default=1.0)
        if acc < 1.0:
            failing.append(f"{cid}: negation_target_accuracy={acc:.0%}")

    assert not failing, "negation_target_accuracy must be 100% for negation cases:\n" + "\n".join(
        f"  {f}" for f in failing
    )


# ---------------------------------------------------------------------------
# 7. Portfolio fallback accuracy 100% for portfolio cases
# ---------------------------------------------------------------------------


def test_portfolio_fallback_accuracy(eval_results: list[ConversationEvalResult]) -> None:
    portfolio_cases = ["portfolio_fallback_samples"]
    by_id = {r.case_id: r for r in eval_results}

    failing: list[str] = []
    for cid in portfolio_cases:
        if cid not in by_id:
            continue
        acc = _metric(by_id[cid], "portfolio_fallback_accuracy", default=1.0)
        if acc < 1.0:
            failing.append(f"{cid}: portfolio_fallback_accuracy={acc:.0%}")

    assert not failing, (
        "portfolio_fallback_accuracy must be 100% for portfolio cases:\n"
        + "\n".join(f"  {f}" for f in failing)
    )


# ---------------------------------------------------------------------------
# 8. Flexible intent accuracy 100% for flexible/discretion cases
# ---------------------------------------------------------------------------


def test_flexible_intent_accuracy(eval_results: list[ConversationEvalResult]) -> None:
    flexible_cases = ["flexible_service_guidance", "bookcraft_discretion_consultation"]
    by_id = {r.case_id: r for r in eval_results}

    failing: list[str] = []
    for cid in flexible_cases:
        if cid not in by_id:
            continue
        acc = _metric(by_id[cid], "flexible_intent_accuracy", default=1.0)
        if acc < 1.0:
            failing.append(f"{cid}: flexible_intent_accuracy={acc:.0%}")

    assert not failing, (
        "flexible_intent_accuracy must be 100% for flexible intent cases:\n"
        + "\n".join(f"  {f}" for f in failing)
    )


# ---------------------------------------------------------------------------
# 9. Phase 12 readiness summary (non-blocking)
# ---------------------------------------------------------------------------


def test_phase12_readiness_summary(eval_results: list[ConversationEvalResult]) -> None:
    """Emit a readiness summary for Phase 12 capabilities (warn, not fail)."""
    by_id = {r.case_id: r for r in eval_results}

    capabilities = {
        "project_shift": [
            "new_project_shift",
            "same_project_service_bundle",
            "multi_project_memory",
        ],
        "negation_targeting": ["target_bound_negation"],
        "slot_delegation": ["delegated_cover_style"],
        "portfolio_fallback": ["portfolio_fallback_samples"],
        "flexible_intent": ["flexible_service_guidance", "bookcraft_discretion_consultation"],
    }

    lines = ["Phase 12 Readiness:"]
    for capability, cases in capabilities.items():
        present = [c for c in cases if c in by_id]
        passed = [c for c in present if by_id[c].passed]
        lines.append(f"  {capability}: {len(passed)}/{len(present)} cases passed")

    import warnings

    warnings.warn("\n".join(lines), UserWarning, stacklevel=1)
