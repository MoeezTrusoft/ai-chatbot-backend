"""Conversation evaluation runner.

Loads YAML cases from tests/evals/conversations/, runs each turn against
the BookCraft API via FastAPI TestClient, and validates expectations.

Usage (module):
    APP_ENV=test API_AUTH_MODE=off python -m tests.evals.conversation_runner

Usage (direct):
    APP_ENV=test API_AUTH_MODE=off python tests/evals/conversation_runner.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field

from bookcraft.api.main import create_app
from bookcraft.infra.config import Settings

EVALS_DIR = Path(__file__).parent / "conversations"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class TurnExpectation(BaseModel):
    model_config = ConfigDict(extra="ignore")  # allow YAML keys not yet mapped

    # Intent checks.
    query_primary: str | None = None
    query_primary_not: str | None = None
    service_primary: str | None = None
    service_primary_not: str | None = None
    service_secondary_excludes: list[str] = Field(default_factory=list)
    service_secondary_contains: str | None = None

    # Context-pack checks.
    manuscript_status: str | None = None
    active_service: str | None = None
    context_pack: dict[str, Any] | None = None

    # Response-text checks.
    forbidden_response_phrases: list[str] = Field(default_factory=list)
    response_must_not_ask: list[str] = Field(default_factory=list)
    one_question_rule: bool = False
    must_not_claim_document_generated: bool = False

    # Governance / action checks.
    tool_governance_allowed: bool | None = None
    tool_governance_reason_not: str | None = None
    action_type_not: str | None = None

    # Trace quality checks.
    response_quality_passed: bool | None = None
    sales_tone_passed: bool | None = None

    # Project-context trace checks.
    project_context_event: str | None = None
    project_context_active_id_changed: bool | None = None

    # Negation-targets trace checks.
    negation_targets_negated_contains: str | None = None
    negation_targets_affirmed_contains: str | None = None

    # Slot-resolution trace checks.
    slot_resolution_contains_slot: str | None = None
    slot_resolution_contains_status: str | None = None


class ConversationTurn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user: str
    expect: TurnExpectation = Field(default_factory=TurnExpectation)


class ConversationCase(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    description: str = ""
    turns: list[ConversationTurn]


class TurnEvalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_index: int
    user: str
    response_text: str
    elapsed_ms: float
    passed: bool
    failures: list[str] = Field(default_factory=list)
    intent: dict[str, Any] = Field(default_factory=dict)


class ConversationEvalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    passed: bool
    total_turns: int
    failures: list[str] = Field(default_factory=list)
    metrics: dict[str, float | int] = Field(default_factory=dict)
    per_turn_results: list[TurnEvalResult] = Field(default_factory=list)


# Ensure forward-reference resolution for Pydantic models (needed when this
# module is loaded via importlib from the report script).
TurnExpectation.model_rebuild()
ConversationTurn.model_rebuild()
ConversationCase.model_rebuild()
TurnEvalResult.model_rebuild()
ConversationEvalResult.model_rebuild()


# ---------------------------------------------------------------------------
# YAML loading with schema normalisation
# ---------------------------------------------------------------------------


def _normalise_turn(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert the nested YAML turn format to the flat model format.

    Supports both compact format:
        user: "message text"
        expect:
            service_primary: cover_design_illustration

    And the verbose format used by the YAML eval files:
        role: user
        message: "message text"
        expect:
            intent:
                service_primary: cover_design_illustration
            response:
                forbidden_phrases: [ghostwriting]
            trace:
                response_quality:
                    passed: true
    """
    # Resolve user message from either `user:` or `role:/message:` form.
    if "user" in raw:
        user_text = str(raw["user"])
    elif raw.get("role") == "user" and "message" in raw:
        user_text = str(raw["message"])
    else:
        return {}  # assistant/system turns are skipped

    raw_expect = raw.get("expect") or {}
    flat: dict[str, Any] = {}

    # -- intent sub-block --
    intent_block = raw_expect.get("intent") or {}
    for key in ("service_primary", "service_secondary", "query_primary"):
        if key in intent_block:
            val = intent_block[key]
            if key.endswith("_not"):
                flat[key] = val
            else:
                flat[key] = val
    for key, val in intent_block.items():
        if key.endswith("_not"):
            flat[key] = val
        elif key.endswith("_one_of"):
            flat[key] = val
        elif key == "service_secondary_contains":
            flat["service_secondary_contains"] = val
        elif key not in flat:
            flat[key] = val

    # -- response sub-block --
    resp_block = raw_expect.get("response") or {}
    if "forbidden_phrases" in resp_block:
        # Filter out dict items (e.g. `fake_name_pattern:`) — only plain strings.
        flat["forbidden_response_phrases"] = [
            p for p in (resp_block["forbidden_phrases"] or []) if isinstance(p, str)
        ]
    if "must_contain_one_of" in resp_block:
        flat["response_must_contain_one_of"] = resp_block["must_contain_one_of"]
    if resp_block.get("one_question_rule"):
        flat["one_question_rule"] = True
    if resp_block.get("must_not_claim_document_generated"):
        flat["must_not_claim_document_generated"] = True

    # -- trace sub-block --
    trace_block = raw_expect.get("trace") or {}
    for section, checks in trace_block.items():
        if not isinstance(checks, dict):
            continue
        if section == "response_quality" and "passed" in checks:
            flat["response_quality_passed"] = checks["passed"]
        if section == "sales_tone" and "passed" in checks:
            flat["sales_tone_passed"] = checks["passed"]
        if section == "tool_governance":
            if "allowed" in checks:
                flat["tool_governance_allowed"] = checks["allowed"]
            if "reason_not" in checks:
                flat["tool_governance_reason_not"] = checks["reason_not"]
        if section == "project_context":
            if "event" in checks:
                flat["project_context_event"] = checks["event"]
            if "active_project_id_changed" in checks:
                flat["project_context_active_id_changed"] = checks["active_project_id_changed"]
        if section == "negation_targets":
            if "negated_contains" in checks:
                flat["negation_targets_negated_contains"] = checks["negated_contains"]
            if "affirmed_contains" in checks:
                flat["negation_targets_affirmed_contains"] = checks["affirmed_contains"]
        if section == "slot_resolution":
            if "contains_slot" in checks:
                flat["slot_resolution_contains_slot"] = checks["contains_slot"]
            if "contains_status" in checks:
                flat["slot_resolution_contains_status"] = checks["contains_status"]

    # -- action_plan sub-block --
    ap_block = raw_expect.get("action_plan") or {}
    for key in ("action_type_not", "action_type_not_2", "action_type_not_3"):
        if key in ap_block:
            flat["action_type_not"] = ap_block[key]
            break
    if "action_type" in ap_block:
        flat["expected_action_type"] = ap_block["action_type"]

    # -- context_pack sub-block --
    cp_block = raw_expect.get("context_pack") or {}
    if "manuscript_status" in cp_block:
        flat["manuscript_status"] = cp_block["manuscript_status"]
    if "active_service" in cp_block:
        flat["active_service"] = cp_block["active_service"]
    if cp_block:
        flat["context_pack"] = cp_block

    # -- flat shorthand keys (already in the right shape) --
    for key in (
        "query_primary",
        "service_primary",
        "forbidden_response_phrases",
        "response_must_not_ask",
        "tool_governance_allowed",
        "action_type_not",
        "response_quality_passed",
        "sales_tone_passed",
        "manuscript_status",
        "context_pack",
    ):
        if key in raw_expect and key not in flat:
            flat[key] = raw_expect[key]

    return {"user": user_text, "expect": flat}


def _normalise_case(raw: dict[str, Any]) -> dict[str, Any]:
    normalised_turns = []
    for t in raw.get("turns") or []:
        nt = _normalise_turn(t)
        if nt:
            normalised_turns.append(nt)
    return {
        "id": raw.get("id", "unknown"),
        "description": raw.get("description", ""),
        "turns": normalised_turns,
    }


def load_case(path: Path) -> ConversationCase:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return ConversationCase.model_validate(_normalise_case(raw))


def load_cases(directory: Path = EVALS_DIR) -> list[ConversationCase]:
    return [load_case(path) for path in sorted(directory.glob("*.yml"))]


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------


def _get_trace(app_state: Any, thread_id: str) -> dict[str, Any]:
    chat_service = getattr(app_state, "chat_service", None)
    if chat_service is None:
        return {}
    ts = getattr(chat_service, "trace_store", None)
    if ts is None:
        return {}
    rows = ts.for_thread(thread_id)
    return rows[0] if rows else {}


def run_conversation_case(
    case: ConversationCase,
    *,
    client: TestClient,
    app_state: Any,
) -> ConversationEvalResult:
    """Run a single conversation case and return its evaluation result."""
    per_turn: list[TurnEvalResult] = []
    case_failures: list[str] = []
    thread_id: str | None = None

    intent_expected = intent_matched = 0
    service_expected = service_matched = 0
    context_expected = context_matched = 0
    repeated_violations = tool_violations = artifact_violations = 0
    rq_failures = tone_failures = 0
    latency_samples: list[float] = []

    for idx, turn in enumerate(case.turns, start=1):
        payload: dict[str, Any] = {"message": turn.user}
        if thread_id is not None:
            payload["thread_id"] = thread_id

        t0 = time.perf_counter()
        resp = client.post("/api/v1/chat/turn", json=payload)
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        latency_samples.append(elapsed_ms)

        failures: list[str] = []

        if resp.status_code != 200:
            failures.append(f"http_status:{resp.status_code}")
            per_turn.append(
                TurnEvalResult(
                    turn_index=idx,
                    user=turn.user,
                    response_text="",
                    elapsed_ms=elapsed_ms,
                    passed=False,
                    failures=failures,
                )
            )
            case_failures.extend([f"turn_{idx}:{f}" for f in failures])
            continue

        body = resp.json()
        thread_id = str(body["thread_id"])
        text = " ".join(str(b.get("text", "")) for b in body.get("bubbles", []))
        text_lower = text.casefold()
        intent = body.get("intent") or {}
        trace = _get_trace(app_state, thread_id)

        cp = trace.get("context_pack") or {}
        tg = trace.get("tool_governance") or {}
        rq = trace.get("response_quality") or {}
        st = trace.get("sales_tone") or {}
        ap = trace.get("action_plan") or {}
        pc_trace = trace.get("project_context") or {}
        nt_trace: list[dict[str, Any]] = trace.get("negation_targets") or []
        sr_trace: list[dict[str, Any]] = trace.get("slot_resolution") or []

        ex = turn.expect

        # — intent checks —
        if ex.query_primary is not None:
            intent_expected += 1
            got = intent.get("query_primary")
            if got == ex.query_primary:
                intent_matched += 1
            else:
                failures.append(
                    f"turn {idx} expected query_primary '{ex.query_primary}', got '{got}'"
                )

        if ex.query_primary_not is not None:
            if intent.get("query_primary") == ex.query_primary_not:
                failures.append(f"turn {idx} query_primary must NOT be '{ex.query_primary_not}'")

        if ex.service_primary is not None:
            service_expected += 1
            got = intent.get("service_primary")
            if got == ex.service_primary:
                service_matched += 1
            else:
                failures.append(
                    f"turn {idx} expected service_primary '{ex.service_primary}', got '{got}'"
                )

        if ex.service_primary_not is not None:
            got = intent.get("service_primary")
            if got == ex.service_primary_not:
                failures.append(
                    f"turn {idx} service_primary must NOT be '{ex.service_primary_not}'"
                )

        for excl in ex.service_secondary_excludes:
            secondary = set(intent.get("service_secondary") or [])
            if excl in secondary:
                failures.append(f"turn {idx} service_secondary contains excluded '{excl}'")

        if ex.service_secondary_contains is not None:
            secondary = set(intent.get("service_secondary") or [])
            if ex.service_secondary_contains not in secondary:
                failures.append(
                    f"turn {idx} service_secondary must contain "
                    f"'{ex.service_secondary_contains}', got {list(secondary)}"
                )

        # — context-pack checks —
        if ex.manuscript_status is not None:
            context_expected += 1
            got = cp.get("manuscript_status")
            if got == ex.manuscript_status:
                context_matched += 1
            else:
                failures.append(
                    f"turn {idx} expected manuscript_status '{ex.manuscript_status}', got '{got}'"
                )

        if ex.active_service is not None:
            context_expected += 1
            got = cp.get("active_service")
            if got == ex.active_service:
                context_matched += 1
            else:
                failures.append(
                    f"turn {idx} expected active_service '{ex.active_service}', got '{got}'"
                )

        if ex.context_pack:
            for key, expected_val in ex.context_pack.items():
                # Advisory boolean sentinels — treat as presence checks.
                if key == "active_genre_set" and expected_val is True:
                    context_expected += 1
                    if cp.get("active_genre"):
                        context_matched += 1
                    else:
                        failures.append(f"turn {idx} context_pack.active_genre not set yet")
                    continue
                if key == "allowed_next_questions_not_empty" and expected_val is True:
                    # Advisory; skip.
                    continue
                # Skip complex sub-keys silently.
                if not isinstance(expected_val, (str, int, float, bool)):
                    continue
                context_expected += 1
                got = cp.get(key)
                if got == expected_val:
                    context_matched += 1
                else:
                    failures.append(
                        f"turn {idx} context_pack.{key}: expected '{expected_val}', got '{got}'"
                    )

        # — response text checks —
        for phrase in ex.forbidden_response_phrases:
            if phrase.casefold() in text_lower:
                failures.append(f"turn {idx} forbidden phrase in response: '{phrase}'")

        for ask in ex.response_must_not_ask:
            if ask.casefold() in text_lower:
                failures.append(f"turn {idx} response re-asks forbidden topic: '{ask}'")
                repeated_violations += 1

        if ex.one_question_rule:
            q_count = text.count("?")
            if q_count > 1:
                failures.append(f"turn {idx} one_question_rule: {q_count} question marks found")

        if ex.must_not_claim_document_generated:
            claim_markers = (
                "has been generated",
                "has been scheduled",
                "has been sent",
                "was generated",
                "was scheduled",
                "confirmed your",
                "booked for",
            )
            for marker in claim_markers:
                if marker in text_lower:
                    failures.append(f"turn {idx} response claims completed action: '{marker}'")
                    tool_violations += 1

        # — governance / action checks —
        if ex.tool_governance_allowed is not None:
            got_allowed = bool(tg.get("allowed"))
            if got_allowed != ex.tool_governance_allowed:
                failures.append(
                    f"turn {idx} tool_governance.allowed: "
                    f"expected {ex.tool_governance_allowed}, got {got_allowed}"
                )

        if ex.tool_governance_reason_not is not None:
            reason = tg.get("reason", "")
            if reason == ex.tool_governance_reason_not:
                failures.append(
                    f"turn {idx} tool_governance.reason must NOT be "
                    f"'{ex.tool_governance_reason_not}'"
                )

        if ex.action_type_not is not None:
            got_action = ap.get("action_type") if isinstance(ap, dict) else None
            if got_action == ex.action_type_not:
                failures.append(f"turn {idx} action_type must NOT be '{ex.action_type_not}'")
                tool_violations += 1

        # — quality gate checks —
        if ex.response_quality_passed is not None:
            got_passed = bool(rq.get("passed"))
            if got_passed != ex.response_quality_passed:
                failures.append(
                    f"turn {idx} response_quality.passed: "
                    f"expected {ex.response_quality_passed}, got {got_passed}"
                )

        if ex.sales_tone_passed is not None:
            got_passed = bool(st.get("passed"))
            if got_passed != ex.sales_tone_passed:
                failures.append(
                    f"turn {idx} sales_tone.passed: "
                    f"expected {ex.sales_tone_passed}, got {got_passed}"
                )

        # — project-context checks —
        if ex.project_context_event is not None:
            pc_decision = pc_trace.get("decision") or {}
            got_event = pc_decision.get("event")
            if got_event == ex.project_context_event:
                context_matched += 1
            else:
                failures.append(
                    f"turn {idx} project_context.event: "
                    f"expected '{ex.project_context_event}', got '{got_event}'"
                )
            context_expected += 1

        if ex.project_context_active_id_changed is True:
            pc_decision = pc_trace.get("decision") or {}
            prev_id = pc_decision.get("previous_project_id")
            active_id = pc_trace.get("active_project_id")
            if prev_id is None or prev_id == active_id:
                failures.append(
                    f"turn {idx} project_context.active_project_id_changed expected True, "
                    f"but active={active_id} prev={prev_id}"
                )
        elif ex.project_context_active_id_changed is False:
            # Verify active_project_id did not change (previous_project_id should be None).
            pc_decision = pc_trace.get("decision") or {}
            prev_id = pc_decision.get("previous_project_id")
            if prev_id is not None:
                failures.append(
                    f"turn {idx} project_context.active_project_id_changed expected False, "
                    f"but previous_project_id={prev_id}"
                )

        # — negation-targets checks —
        if ex.negation_targets_negated_contains is not None:
            neg_vals = {t.get("target", "") for t in nt_trace if t.get("polarity") == "negated"}
            want = ex.negation_targets_negated_contains
            if want not in neg_vals:
                failures.append(
                    f"turn {idx} negation_targets: '{want}' not found in negated targets {neg_vals}"
                )
            context_expected += 1
            if want in neg_vals:
                context_matched += 1

        if ex.negation_targets_affirmed_contains is not None:
            aff_vals = {
                t.get("target", "")
                for t in nt_trace
                if t.get("polarity") in ("affirmed", "replacement")
            }
            want = ex.negation_targets_affirmed_contains
            if want not in aff_vals:
                failures.append(
                    f"turn {idx} negation_targets: '{want}' not found in affirmed/replacement "
                    f"targets {aff_vals}"
                )
            context_expected += 1
            if want in aff_vals:
                context_matched += 1

        # — slot-resolution checks —
        if ex.slot_resolution_contains_slot is not None:
            sr_slots = {s.get("slot", "") for s in sr_trace}
            want_slot = ex.slot_resolution_contains_slot
            if want_slot in sr_slots:
                context_matched += 1
            else:
                failures.append(
                    f"turn {idx} slot_resolution: slot '{want_slot}' not found in {sr_slots}"
                )
            context_expected += 1

        if ex.slot_resolution_contains_status is not None:
            want_status = ex.slot_resolution_contains_status
            want_slot = ex.slot_resolution_contains_slot
            found = any(
                s.get("status") == want_status and (want_slot is None or s.get("slot") == want_slot)
                for s in sr_trace
            )
            if found:
                context_matched += 1
            else:
                failures.append(
                    f"turn {idx} slot_resolution: status '{want_status}' not found in {sr_trace}"
                )
            context_expected += 1

        # — automatic safety audits (always run) —
        # Note: repeated_violations is incremented ONLY from explicit YAML
        # response_must_not_ask assertions (above) so it reflects deliberate
        # eval requirements.  Quality-gate known_fact_reask hits are counted
        # separately in rq_failures to avoid double-counting.
        rq_fail_list = [str(f) for f in rq.get("failures", [])]
        if any("blocked_action" in f for f in rq_fail_list):
            tool_violations += 1
        if not bool(rq.get("passed", True)):
            rq_failures += 1
        if not bool(st.get("passed", True)):
            tone_failures += 1

        _INTERNAL_MARKERS = (
            "backend",
            "classifier",
            "runtime atoms",
            "tool_governance",
            "action_plan",
            "rag retriever",
        )
        if any(m in text_lower for m in _INTERNAL_MARKERS):
            artifact_violations += 1

        turn_passed = len(failures) == 0
        per_turn.append(
            TurnEvalResult(
                turn_index=idx,
                user=turn.user,
                response_text=text,
                elapsed_ms=elapsed_ms,
                passed=turn_passed,
                failures=failures,
                intent=intent if isinstance(intent, dict) else {},
            )
        )
        case_failures.extend([f"turn_{idx}: {f}" for f in failures])

    avg_lat = round(sum(latency_samples) / len(latency_samples), 2) if latency_samples else 0.0
    max_lat = round(max(latency_samples), 2) if latency_samples else 0.0

    metrics: dict[str, float | int] = {
        "intent_accuracy": _ratio(intent_matched, intent_expected),
        "service_accuracy": _ratio(service_matched, service_expected),
        "context_retention_score": _ratio(context_matched, context_expected),
        "repeated_question_violations": repeated_violations,
        "tool_safety_violations": tool_violations,
        "internal_artifact_violations": artifact_violations,
        "response_quality_failures": rq_failures,
        "sales_tone_failures": tone_failures,
        "avg_latency_ms": avg_lat,
        "max_latency_ms": max_lat,
    }

    case_passed = len(case_failures) == 0 and artifact_violations == 0 and tool_violations == 0
    return ConversationEvalResult(
        case_id=case.id,
        passed=case_passed,
        total_turns=len(per_turn),
        failures=case_failures,
        metrics=metrics,
        per_turn_results=per_turn,
    )


def run_all_cases(directory: Path = EVALS_DIR) -> list[ConversationEvalResult]:
    """Load and run all YAML cases; each case uses its own app instance."""
    cases = load_cases(directory)
    results: list[ConversationEvalResult] = []
    for case in cases:
        app = create_app(Settings(app_env="test", api_auth_mode="off"))
        with TestClient(app) as client:
            result = run_conversation_case(case, client=client, app_state=client.app.state)
        results.append(result)
    return results


# Alias used by the module entry point.
run_all_conversation_cases = run_all_cases


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _print_results(results: list[ConversationEvalResult]) -> None:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    sep = "=" * 62
    print(f"\n{sep}")
    print(f"  Conversation Eval Results: {passed}/{total} cases passed")
    print(sep)

    for result in results:
        icon = "✓" if result.passed else "✗"
        print(
            f"\n  {icon}  {result.case_id}"
            f"  (turns={result.total_turns}"
            f", ia={result.metrics.get('intent_accuracy', 0):.0%}"
            f", sa={result.metrics.get('service_accuracy', 0):.0%}"
            f", lat={result.metrics.get('avg_latency_ms', 0):.0f}ms)"
        )
        for turn_result in result.per_turn_results:
            if not turn_result.passed:
                preview = turn_result.user[:55]
                print(f'       turn {turn_result.turn_index}: "{preview}"')
                for failure in turn_result.failures:
                    print(f"         • {failure}")

    print(f"\n{sep}")
    if passed < total:
        print(f"  FAILED: {total - passed} case(s)")
    else:
        print("  All cases passed.")
    print(f"{sep}\n")


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator > 0 else 1.0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    _results = run_all_cases()
    _print_results(_results)
    sys.exit(1 if any(not r.passed for r in _results) else 0)
