"""Pytest wrapper for conversation eval cases.

Loads all YAML cases from tests/evals/conversations/, runs them via the
conversation runner, and makes the following assertions:

- At least 8 YAML cases loaded.
- All cases defined as CRITICAL must pass fully.
- No internal-artifact violations in any case.
- No tool-safety violations in any case.
- repeated_question_violations == 0 for every critical case.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Allow importing the runner as a sibling module when pytest runs from the
# project root without the tests/ tree on PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from conversation_runner import (  # noqa: E402
    ConversationEvalResult,
    run_all_cases,
)

EVALS_DIR = Path(__file__).parent / "conversations"

# Cases that must pass completely.  All others are evaluated but only produce
# warnings on failure so new cases can be iterated without blocking CI.
CRITICAL_CASES = {
    "cover_design_children_fiction",
    "nda_agreement_negation",
    "pricing_quote_disambiguation",
    "service_switch_and_addition",
    "counterfactual_safety",
}


# ---------------------------------------------------------------------------
# Shared fixture — runs all cases once per test session.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def eval_results() -> list[ConversationEvalResult]:
    return run_all_cases(EVALS_DIR)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _format_case(result: ConversationEvalResult) -> str:
    lines = [f"  CASE: {result.case_id}  (turns={result.total_turns})"]
    for failure in result.failures[:10]:
        lines.append(f"    • {failure}")
    if len(result.failures) > 10:
        lines.append(f"    … and {len(result.failures) - 10} more failure(s)")
    m = result.metrics
    lines.append(
        f"    metrics — ia={m.get('intent_accuracy', 0):.0%} "
        f"sa={m.get('service_accuracy', 0):.0%} "
        f"rq_violations={m.get('repeated_question_violations', 0)} "
        f"tool_violations={m.get('tool_safety_violations', 0)} "
        f"artifact_violations={m.get('internal_artifact_violations', 0)}"
    )
    return "\n".join(lines)


def _metric(result: ConversationEvalResult, key: str) -> int:
    return int(result.metrics.get(key, 0))


# ---------------------------------------------------------------------------
# Test 1: enough cases loaded
# ---------------------------------------------------------------------------


def test_at_least_8_cases_loaded(eval_results: list[ConversationEvalResult]) -> None:
    """YAML directory must contain at least 8 conversation cases."""
    yaml_files = sorted(EVALS_DIR.glob("*.yml"))
    assert len(yaml_files) >= 8, f"Expected ≥8 YAML files in {EVALS_DIR}, found {len(yaml_files)}"
    assert len(eval_results) == len(yaml_files), (
        f"Runner returned {len(eval_results)} results for {len(yaml_files)} YAML files"
    )


# ---------------------------------------------------------------------------
# Test 2: all critical cases pass
# ---------------------------------------------------------------------------


def test_critical_cases_all_pass(eval_results: list[ConversationEvalResult]) -> None:
    """Every case in CRITICAL_CASES must pass without any turn failures."""
    by_id = {r.case_id: r for r in eval_results}

    missing = CRITICAL_CASES - by_id.keys()
    assert not missing, f"Critical case(s) not found in results: {missing}"

    failed_critical = [by_id[cid] for cid in sorted(CRITICAL_CASES) if not by_id[cid].passed]

    if failed_critical:
        summary = "\n".join(_format_case(r) for r in failed_critical)
        pytest.fail(f"{len(failed_critical)} critical case(s) failed:\n{summary}")


# ---------------------------------------------------------------------------
# Test 3: no internal-artifact violations
# ---------------------------------------------------------------------------


def test_no_internal_artifact_violations(
    eval_results: list[ConversationEvalResult],
) -> None:
    """No response must contain internal implementation terms (backend, RAG, etc.)."""
    violating = [r for r in eval_results if _metric(r, "internal_artifact_violations") > 0]

    if violating:
        summary = "\n".join(
            f"  {r.case_id}: {_metric(r, 'internal_artifact_violations')} violation(s)"
            for r in violating
        )
        pytest.fail(
            f"Internal artifact violations detected in {len(violating)} case(s):\n{summary}"
        )


# ---------------------------------------------------------------------------
# Test 4: no tool safety violations
# ---------------------------------------------------------------------------


def test_no_tool_safety_violations(
    eval_results: list[ConversationEvalResult],
) -> None:
    """No response must claim a blocked tool action succeeded."""
    violating = [r for r in eval_results if _metric(r, "tool_safety_violations") > 0]

    if violating:
        summary = "\n".join(
            f"  {r.case_id}: {_metric(r, 'tool_safety_violations')} violation(s)" for r in violating
        )
        pytest.fail(f"Tool safety violations detected in {len(violating)} case(s):\n{summary}")


# ---------------------------------------------------------------------------
# Test 5: no repeated-question violations in critical cases
# ---------------------------------------------------------------------------


def test_no_repeated_question_violations_in_critical_cases(
    eval_results: list[ConversationEvalResult],
) -> None:
    """Critical cases must not re-ask facts the user has already shared."""
    by_id = {r.case_id: r for r in eval_results}

    violating: list[tuple[str, int]] = [
        (cid, _metric(by_id[cid], "repeated_question_violations"))
        for cid in sorted(CRITICAL_CASES)
        if cid in by_id and _metric(by_id[cid], "repeated_question_violations") > 0
    ]

    if violating:
        summary = "\n".join(f"  {cid}: {count} re-ask violation(s)" for cid, count in violating)
        pytest.fail(f"Repeated-question violations in critical case(s):\n{summary}")


# ---------------------------------------------------------------------------
# Test 6: summary of non-critical failures (warn, don't fail)
# ---------------------------------------------------------------------------


def test_non_critical_cases_summary(
    eval_results: list[ConversationEvalResult],
) -> None:
    """Non-critical case failures are reported as warnings, not hard failures."""
    non_critical_failed = [
        r for r in eval_results if not r.passed and r.case_id not in CRITICAL_CASES
    ]

    if non_critical_failed:
        summary = "\n".join(_format_case(r) for r in non_critical_failed)
        pytest.warns(
            UserWarning,
            match=".*",
        )
        # Emit as a warning via the xfail / skip mechanism so CI sees it.
        import warnings

        warnings.warn(
            f"{len(non_critical_failed)} non-critical eval case(s) failing "
            f"(not blocking):\n{summary}",
            UserWarning,
            stacklevel=1,
        )


# ---------------------------------------------------------------------------
# Test 7: per-case parametrised details (informational, not blocking)
# ---------------------------------------------------------------------------


def _case_ids(eval_results: list[ConversationEvalResult]) -> list[str]:
    return [r.case_id for r in eval_results]


@pytest.mark.parametrize("case_id", sorted(CRITICAL_CASES))
def test_critical_case_details(
    eval_results: list[ConversationEvalResult],
    case_id: str,
) -> None:
    """Per-case assertions for every critical scenario."""
    by_id = {r.case_id: r for r in eval_results}
    if case_id not in by_id:
        pytest.skip(f"Case '{case_id}' not loaded")

    result = by_id[case_id]

    assert result.passed, f"Critical case '{case_id}' failed:\n" + _format_case(result)
    assert _metric(result, "internal_artifact_violations") == 0, (
        f"'{case_id}' has internal artifact violation(s)"
    )
    assert _metric(result, "tool_safety_violations") == 0, (
        f"'{case_id}' has tool safety violation(s)"
    )
    assert _metric(result, "repeated_question_violations") == 0, (
        f"'{case_id}' has repeated-question violation(s)"
    )
