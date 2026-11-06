"""Email-only consultation track (chat 8868).

Rules enforced:
- A consultation is ONLY booked with the author's name AND a working phone number.
- When the phone is not functional/inactive or the client refuses to provide it, the
  bot must ask for the email ONLY and must NEVER say the consultation is booked,
  scheduled, locked in, or confirmed — a professional consultant contacts them.
- If neither phone nor email is given, ask for one.
- No video links / Google Meet / any medium other than a phone call.

Prompt-only steering is covered by the system-prompt rewrite; this file pins the
deterministic engine alignment (reducer stages, objective-engine moves/goals,
planner goals, and phone-availability detection).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from bookcraft.components.leads.contact_availability import ContactAvailabilityDetector
from bookcraft.components.response.planner import ResponsePlanner
from bookcraft.components.sales.consultation_objective import ConsultationObjectiveEngine
from bookcraft.components.sales.consultation_state import (
    ConsultationStage,
    reduce_consultation_state,
)
from bookcraft.components.sales.current_question_priority import CurrentQuestionPriorityResult
from bookcraft.domain.enums import QueryIntentType
from bookcraft.domain.state import ThreadState

# ---------------------------------------------------------------------------
# Phone-availability detector: explicit refusals must start the email-only track
# ---------------------------------------------------------------------------


def test_detects_phone_refusal_phrasings() -> None:
    d = ContactAvailabilityDetector()
    for msg in (
        "I won't provide my phone number",
        "I will not give you my number",
        "I'd prefer not to share my phone number",
        "rather not give my phone number",
        "I don't want to share my number",
        "I don't want to give you my phone",
        "I can't give you my number",
        "would not want to provide my phone",
        "I won't share my cell number",
    ):
        assert d.detect(msg).phone_unavailable is True, msg


def test_does_not_false_positive_on_provided_or_neutral_phone() -> None:
    d = ContactAvailabilityDetector()
    for msg in (
        "I'll provide my phone number now: 6099604230",
        "Here's my number — call anytime",
        "let me go get my phone",
        "I'll give you my phone later today",
    ):
        assert d.detect(msg).phone_unavailable is False, msg


# ---------------------------------------------------------------------------
# Reducer: email-only stages, never a booking, nothing else to ask
# ---------------------------------------------------------------------------


def _intent(primary=None) -> MagicMock:
    m = MagicMock()
    m.query_primary = primary or QueryIntentType.CONSULTATION_REQUEST
    return m


def _state(*, preferred_call_time: str | None = None) -> ThreadState:
    s = ThreadState()
    if preferred_call_time:
        s.preferred_call_time = preferred_call_time
    s.sales_actions.consultation.requested = True
    return s


def test_reducer_phone_unavailable_no_email_asks_email_only() -> None:
    decision = reduce_consultation_state(
        state=_state(),
        message="I can't provide a phone number",
        intent=_intent(),
        contact_ready=True,
        has_email=False,
        has_phone=False,
        require_phone=True,
        phone_unavailable=True,
    )
    assert decision.stage == ConsultationStage.REQUESTED_EMAIL_NEEDED
    assert decision.next_question == "missing_email"
    assert decision.can_schedule is False
    assert decision.stop_discovery is True


def test_reducer_phone_unavailable_with_email_never_schedules() -> None:
    decision = reduce_consultation_state(
        state=_state(preferred_call_time="Friday at 3pm"),
        message="my phone is broken",
        intent=_intent(),
        contact_ready=True,
        has_email=True,
        has_phone=False,
        require_phone=True,
        phone_unavailable=True,
    )
    # Even with a call time on file, email-only must NEVER schedule and must NOT
    # ask for a date/time/timezone — a consultant reaches out instead.
    assert decision.stage == ConsultationStage.EMAIL_FOLLOWUP_PREFERRED
    assert decision.can_schedule is False
    assert decision.next_question is None
    assert decision.stop_discovery is True


def test_reducer_email_only_states_persist_across_turns() -> None:
    prior_reducer_stage = ConsultationStage.EMAIL_FOLLOWUP_PREFERRED
    decision = reduce_consultation_state(
        state=_state(),
        message="okay thanks",
        intent=_intent(),
        contact_ready=True,
        has_email=True,
        has_phone=False,
        require_phone=True,
        phone_unavailable=True,
        prior_stage=prior_reducer_stage,
    )
    assert decision.stage == ConsultationStage.EMAIL_FOLLOWUP_PREFERRED
    assert decision.can_schedule is False


def test_reducer_phone_still_required_when_available() -> None:
    decision = reduce_consultation_state(
        state=_state(),
        message="I'd like to schedule",
        intent=_intent(),
        contact_ready=True,
        has_email=True,
        has_phone=False,
        require_phone=True,
        phone_unavailable=False,
    )
    assert decision.stage == ConsultationStage.REQUESTED_PHONE_NEEDED
    assert decision.next_question == "missing_phone"


# ---------------------------------------------------------------------------
# Objective engine: email-only moves and goals
# ---------------------------------------------------------------------------


def _contact_capture(
    *, ready: bool = True, has_phone: bool = False, has_email: bool = False
) -> MagicMock:
    m = MagicMock()
    m.lead_contact_ready = ready
    m.has_phone = has_phone
    m.has_email = has_email
    return m


def _lod(move: str = "continue_light_discovery") -> MagicMock:
    m = MagicMock()
    m.objective_move = move
    m.stage = "engaging"
    m.stop_discovery = move != "continue_light_discovery"
    m.recommended_primary_goal = None
    m.next_question = None
    return m


def _priority(has_priority: bool = False) -> CurrentQuestionPriorityResult:
    return CurrentQuestionPriorityResult(
        has_priority=has_priority,
        question_type=None,
        should_answer_before_capture=False,
        suppress_old_sales_path=False,
    )


def test_objective_engine_phone_unavailable_no_email_asks_email() -> None:
    engine = ConsultationObjectiveEngine()
    decision = engine.decide(
        message="I won't give my phone number",
        state=ThreadState(),
        lead_objective_decision=_lod(),
        contact_capture=_contact_capture(has_email=False),
        require_phone=True,
        phone_unavailable=True,
        current_question_priority=_priority(),
    )
    assert decision.objective_move == "ask_contact"
    assert decision.recommended_primary_goal == "consultation_email_capture"
    assert decision.next_question == "missing_email"
    assert decision.stop_discovery is True


def test_objective_engine_phone_unavailable_with_email_handoffs() -> None:
    engine = ConsultationObjectiveEngine()
    decision = engine.decide(
        message="my phone isn't working, my email is fine",
        state=ThreadState(),
        lead_objective_decision=_lod(),
        contact_capture=_contact_capture(has_email=True),
        require_phone=True,
        phone_unavailable=True,
        current_question_priority=_priority(),
    )
    assert decision.objective_move == "create_email_followup"
    assert decision.recommended_primary_goal == "consultation_email_followup_confirmation"
    assert decision.next_question is None
    assert decision.create_handoff is True


def test_objective_engine_email_only_beats_stray_call_time_handoff() -> None:
    # A call time on file must NOT resurrect the normal booking/handoff in the
    # email-only track.
    engine = ConsultationObjectiveEngine()
    state = ThreadState()
    state.preferred_call_time = "Friday at 3pm"
    decision = engine.decide(
        message="I prefer email, no phone",
        state=state,
        lead_objective_decision=_lod(),
        contact_capture=_contact_capture(has_email=True),
        require_phone=True,
        phone_unavailable=True,
        current_question_priority=_priority(),
    )
    assert decision.objective_move == "create_email_followup"
    assert decision.recommended_primary_goal == "consultation_email_followup_confirmation"


def test_objective_engine_phone_gate_unchanged_when_phone_available() -> None:
    engine = ConsultationObjectiveEngine()
    decision = engine.decide(
        message="I'd like a consultation",
        state=ThreadState(),
        lead_objective_decision=_lod(),
        contact_capture=_contact_capture(has_phone=False),
        require_phone=True,
        phone_unavailable=False,
        current_question_priority=_priority(),
    )
    assert decision.next_question == "missing_phone"
    assert decision.recommended_primary_goal == "consultation_time_capture"


# ---------------------------------------------------------------------------
# Planner: new goals map to email-only questions, never booking asks
# ---------------------------------------------------------------------------


def _mock_consult_objective(decision_attrs: dict) -> MagicMock:
    m = MagicMock()
    for k, v in decision_attrs.items():
        setattr(m, k, v)
    return m


def _context_pack() -> MagicMock:
    cp = MagicMock()
    cp.lead_created = False
    cp.is_greeting_turn = False
    cp.contact_ready = True
    cp.assessment_type = None
    cp.active_service = None
    cp.active_genre = None
    cp.manuscript_status = None
    cp.known_facts = []
    cp.missing_facts = []
    cp.forbidden_reasks = []
    cp.allowed_next_questions = []
    cp.disallowed_next_questions = []
    cp.delegated_slots = []
    cp.preferred_call_time = None
    cp.response_hint = None
    cp.project_event = None
    cp.attachments_received = []
    cp.specialist_role = None
    cp.contact_status = {}
    return cp


def _planner_plan(
    *,
    primary_goal: str,
    next_question: str | None,
    objective_move: str,
):
    planner = ResponsePlanner()
    intent = MagicMock()
    intent.query_primary = QueryIntentType.CONSULTATION_REQUEST
    intent.query_secondary = []
    cod = _mock_consult_objective(
        {
            "objective_move": objective_move,
            "recommended_primary_goal": primary_goal,
            "next_question": next_question,
        }
    )
    return planner.plan(
        intent=intent,
        state=ThreadState(),
        context_pack=_context_pack(),
        lead_objective_decision=None,
        consultation_objective_decision=cod,
    )


def test_planner_email_capture_goal_asks_missing_email() -> None:
    plan = _planner_plan(
        primary_goal="consultation_email_capture",
        next_question="missing_email",
        objective_move="ask_contact",
    )
    assert plan.primary_goal == "consultation_email_capture"
    assert plan.next_question == "missing_email"


def test_planner_email_followup_goal_asks_nothing() -> None:
    plan = _planner_plan(
        primary_goal="consultation_email_followup_confirmation",
        next_question=None,
        objective_move="create_email_followup",
    )
    assert plan.primary_goal == "consultation_email_followup_confirmation"
    assert plan.next_question is None


def test_planner_email_only_never_asks_call_time() -> None:
    plan = _planner_plan(
        primary_goal="consultation_email_followup_confirmation",
        next_question=None,
        objective_move="create_email_followup",
    )
    assert plan.next_question not in {"preferred_call_time", "preferred_call_time_slots"}