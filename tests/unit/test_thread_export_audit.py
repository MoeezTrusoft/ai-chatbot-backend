"""Unit tests for the audit_thread_exports script.

Tests the _audit() and _is_bad_source() functions with fixture data:
- one good claude_sonnet trace
- one bad template_no_adapter trace
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# Load the audit module directly (it's a script, not a package).
_AUDIT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "data" / "audit_thread_exports.py"
_AUDIT_SPEC = importlib.util.spec_from_file_location("audit_thread_exports", _AUDIT_PATH)
assert _AUDIT_SPEC is not None and _AUDIT_SPEC.loader is not None
_AUDIT_MODULE = importlib.util.module_from_spec(_AUDIT_SPEC)
_AUDIT_SPEC.loader.exec_module(_AUDIT_MODULE)

_audit = _AUDIT_MODULE._audit
_is_bad_source = _AUDIT_MODULE._is_bad_source


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _good_trace(thread_id: str = "thread-good") -> dict:
    return {
        "thread_id": thread_id,
        "assistant": {"source": "claude_sonnet_4_6", "bubble_count": 1},
        "customer_response_contract": {"contract_passed": True},
        "response_quality": {"passed": True},
        "sales_tone": {"passed": True},
        "context_pack": {},
        "response_plan": {},
    }


def _bad_trace(thread_id: str = "thread-bad", source: str = "template_no_adapter") -> dict:
    return {
        "thread_id": thread_id,
        "assistant": {"source": source, "bubble_count": 1},
        "customer_response_contract": {"contract_passed": False},
        "response_quality": {"passed": False},
        "sales_tone": {"passed": True},
        "context_pack": {},
        "response_plan": {},
    }


def _make_threads(
    *thread_traces: tuple[str, list[dict]],
) -> list[dict]:
    return [{"thread_id": tid, "traces": traces} for tid, traces in thread_traces]


# ---------------------------------------------------------------------------
# _is_bad_source tests
# ---------------------------------------------------------------------------


def test_claude_source_is_not_bad() -> None:
    assert _is_bad_source("claude_sonnet_4_6") is False
    assert _is_bad_source("claude_sonnet_repair") is False


def test_template_source_is_bad() -> None:
    assert _is_bad_source("template_no_adapter") is True
    assert _is_bad_source("template_fallback") is True


def test_deterministic_source_is_bad() -> None:
    assert _is_bad_source("deterministic_mixed_request_guard") is True


def test_portfolio_engine_source_is_bad() -> None:
    assert _is_bad_source("portfolio_engine_quality_fallback") is True


def test_quality_fallback_allowed_for_claude_repair() -> None:
    # claude_sonnet_repair_quality_fallback should NOT be flagged
    assert _is_bad_source("claude_sonnet_repair_quality_fallback") is False


def test_quality_fallback_flagged_for_other_sources() -> None:
    assert _is_bad_source("some_other_source_quality_fallback") is True


def test_empty_source_is_not_bad() -> None:
    assert _is_bad_source("") is False


# ---------------------------------------------------------------------------
# _audit tests
# ---------------------------------------------------------------------------


def test_audit_counts_good_and_bad_threads() -> None:
    threads = _make_threads(
        ("thread-good", [_good_trace("thread-good")]),
        ("thread-bad", [_bad_trace("thread-bad", "template_no_adapter")]),
    )
    result = _audit(threads)
    assert result["threads_checked"] == 2
    assert result["turns_checked"] == 2
    assert result["deterministic_source_hits"] == 1
    assert result["deterministic_sources"][0]["thread_id"] == "thread-bad"
    assert result["deterministic_sources"][0]["final_source"] == "template_no_adapter"


def test_audit_compliance_rate_all_good() -> None:
    threads = _make_threads(
        ("t1", [_good_trace(), _good_trace()]),
    )
    result = _audit(threads)
    assert result["source_compliance_rate"] == 1.0
    assert result["deterministic_source_hits"] == 0


def test_audit_compliance_rate_one_bad() -> None:
    threads = _make_threads(
        ("t1", [_good_trace(), _bad_trace(source="template_x")]),
    )
    result = _audit(threads)
    assert result["deterministic_source_hits"] == 1
    assert result["source_compliance_rate"] == pytest.approx(0.5, abs=0.01)


def test_audit_missing_contract_counted() -> None:
    trace_no_contract = {
        "thread_id": "t1",
        "assistant": {"source": "claude_sonnet"},
        "response_quality": {"passed": True},
        "sales_tone": {"passed": True},
        "context_pack": {},
        "response_plan": {},
        # customer_response_contract is absent
    }
    threads = _make_threads(("t1", [trace_no_contract]))
    result = _audit(threads)
    assert result["missing_contract_count"] == 1


def test_audit_empty_threads() -> None:
    result = _audit([])
    assert result["threads_checked"] == 0
    assert result["turns_checked"] == 0
    assert result["deterministic_source_hits"] == 0
    assert result["source_compliance_rate"] == 1.0
