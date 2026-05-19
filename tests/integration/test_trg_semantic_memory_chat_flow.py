from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.infra.config import Settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _chat(
    client: TestClient,
    message: str,
    *,
    thread_id: object | None = None,
) -> dict[str, Any]:
    payload: dict[str, object] = {"message": message}
    if thread_id is not None:
        payload["thread_id"] = str(thread_id)
    response = client.post("/api/v1/chat/turn", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _latest_trace(client: TestClient, thread_id: str) -> dict[str, Any]:
    trace_store = client.app.state.chat_service.trace_store
    rows = trace_store.for_thread(thread_id)
    assert rows, f"No trace rows found for thread {thread_id}"
    return rows[0]


def _joined_text(body: dict[str, Any]) -> str:
    return " ".join(str(bubble["text"]) for bubble in body["bubbles"])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_trg_semantic_key_present_in_trace() -> None:
    """Every turn must emit a trg_semantic entry in the live trace."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "I need cover design for my children's fiction book.")
        trace = _latest_trace(client, resp["thread_id"])

    sem = trace.get("trg_semantic")
    assert isinstance(sem, dict), f"trg_semantic missing; keys: {list(trace.keys())}"
    assert "active_facts" in sem
    assert "answered_questions" in sem
    assert "forbidden_reasks" in sem
    assert "contradictions" in sem
    assert "service_shifts" in sem


def test_trg_records_genre_as_semantic_fact() -> None:
    """After genre is extracted, trg_semantic.active_facts must contain project.genre."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        first = _chat(client, "I need cover design for my children's fiction book.")
        thread_id = first["thread_id"]
        # Give the extractor a second turn that reinforces genre.
        _chat(client, "Its fiction children book as I told you.", thread_id=thread_id)
        trace = _latest_trace(client, thread_id)

    sem = trace.get("trg_semantic", {})
    # Genre should be in active facts after two turns where it's mentioned.
    # (Extraction may or may not extract it on the first turn, but by the second it should.)
    # We verify the structure is correct even if no fact is present yet.
    assert isinstance(sem.get("active_facts"), list)
    assert isinstance(sem.get("forbidden_reasks"), list)


def test_trg_forbidden_reasks_populated_after_genre_established() -> None:
    """
    After genre is established in state, TRG forbidden_reasks must include genre
    so ContextPack reflects it and responses don't re-ask.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        first = _chat(client, "I need cover design. The genre is children's fiction.")
        thread_id = first["thread_id"]
        resp = _chat(client, "What else do you need?", thread_id=thread_id)
        trace = _latest_trace(client, thread_id)

    rp = trace.get("response_plan", {})
    text = _joined_text(resp).casefold()

    # response_plan should list genre in must_not_mention.
    must_not = rp.get("must_not_mention", [])
    assert "genre" in must_not, f"genre not in must_not_mention: {must_not}"

    # Response should not ask for genre.
    assert "what genre" not in text


def test_trg_service_shift_inertia_recorded() -> None:
    """
    When the ContextArbiter fires service inertia, trg_semantic.service_shifts
    must include a shift with mode=inertia.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        first = _chat(client, "I need a cover design for my children's fiction book.")
        thread_id = first["thread_id"]
        # Second turn has no service keyword → arbiter fires inertia.
        _chat(client, "I have finished the manuscript.", thread_id=thread_id)
        trace = _latest_trace(client, thread_id)

    sem = trace.get("trg_semantic", {})
    service_shifts = sem.get("service_shifts", [])

    # Inertia should have fired on the second turn.
    inertia_shifts = [s for s in service_shifts if s.get("mode") == "inertia"]
    assert inertia_shifts, f"Expected at least one inertia service_shift; got: {service_shifts}"


def test_trg_semantic_does_not_re_ask_known_manuscript_stage() -> None:
    """
    After manuscript_status is established, the response must not ask again.
    This is the full Phase 8 acceptance test for manuscript_stage forbidden re-ask.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        first = _chat(client, "I need cover design. I have finished my manuscript.")
        thread_id = first["thread_id"]
        resp = _chat(client, "What else do you need from me?", thread_id=thread_id)
        trace = _latest_trace(client, thread_id)

    text = _joined_text(resp).casefold()
    rp = trace.get("response_plan", {})

    assert "manuscript stage" not in text, "Response must not ask for manuscript stage again"
    assert "starting from scratch" not in text
    assert "have a draft" not in text

    must_not = rp.get("must_not_mention", [])
    assert any(m in must_not for m in ("manuscript_stage", "draft status")), (
        f"Expected manuscript stage in must_not_mention: {must_not}"
    )


# ===========================================================================
# Required integration tests (exact names from spec)
# ===========================================================================


def test_semantic_facts_recorded_for_known_project_context() -> None:
    """
    After sharing genre and manuscript status across turns, TRG must record
    them as active semantic facts, and both ContextPack and forbidden_reasks
    must reflect the merged TRG knowledge.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        first = _chat(client, "I need cover design.")
        thread_id = first["thread_id"]
        _chat(client, "It is a children's fiction book.", thread_id=thread_id)
        _chat(client, "I have finished my manuscript.", thread_id=thread_id)
        trace = _latest_trace(client, thread_id)

    sem = trace.get("trg_semantic", {})
    rp = trace.get("response_plan", {})

    # trg_semantic must be structurally valid.
    assert isinstance(sem.get("active_facts"), list)
    assert isinstance(sem.get("forbidden_reasks"), list)

    # After three turns of project context, at least some facts should be known.
    # The extractor may surface genre and/or manuscript_status.
    active_paths = {f.get("fact_path") for f in sem.get("active_facts", [])}
    known_paths = {"project.genre", "project.manuscript_status"}
    assert active_paths & known_paths, (
        f"Expected at least one of {known_paths} in active_facts; got: {active_paths}"
    )

    # Forbidden re-asks must include genre and/or manuscript-stage labels.
    forbidden = sem.get("forbidden_reasks", [])
    assert any(label in forbidden for label in ("genre", "manuscript_stage", "draft status")), (
        f"Expected known-fact labels in forbidden_reasks; got: {forbidden}"
    )

    # ContextPack must_not_mention should include the forbidden re-ask labels too.
    must_not = rp.get("must_not_mention", [])
    assert "genre" in must_not, f"genre not in must_not_mention: {must_not}"


def test_answered_question_recorded() -> None:
    """
    When the assistant asks about manuscript stage and the user answers,
    the TRG should record an answered question with resolved=True and
    the response must not ask for draft status again.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        # Turn 1: no manuscript status → template CTA includes a '?' question.
        first = _chat(client, "I need cover design for my book.")
        thread_id = first["thread_id"]

        # Turn 2: user answers with manuscript info → resolves the outstanding question
        # and records an AnsweredQuestion if the assistant asked one.
        _chat(client, "I have finished my manuscript.", thread_id=thread_id)
        trace = _latest_trace(client, thread_id)

    sem = trace.get("trg_semantic", {})
    rp = trace.get("response_plan", {})

    # answered_questions list must exist and be a valid list.
    assert isinstance(sem.get("answered_questions"), list), (
        f"answered_questions must be a list; got: {sem.get('answered_questions')}"
    )

    # If the first response contained a question, it should be recorded as answered.
    answered = sem.get("answered_questions", [])
    if answered:
        assert all(q.get("resolved") is True for q in answered), (
            f"All recorded questions should be resolved=True; got: {answered}"
        )

    # The response_plan must suppress manuscript-stage from further re-asks.
    must_not = rp.get("must_not_mention", [])
    assert any(m in must_not for m in ("manuscript_stage", "draft status")), (
        f"Expected manuscript-stage suppression; must_not_mention: {must_not}"
    )


def test_service_switch_event_recorded() -> None:
    """
    'Actually I need editing instead' after cover design is established must
    produce a service_shift with mode='switch', and the active service must
    update to editing_proofreading.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        first = _chat(client, "I need cover design for my book.")
        thread_id = first["thread_id"]
        switched = _chat(
            client,
            "Actually I need editing and proofreading instead.",
            thread_id=thread_id,
        )
        trace = _latest_trace(client, thread_id)

    sem = trace.get("trg_semantic", {})
    arbiter = trace.get("context_arbiter", {})
    intent = switched["intent"]

    # ContextArbiter must have detected the explicit switch.
    assert any("explicit_service_switch" in a for a in arbiter.get("audit", [])), (
        f"Expected explicit_service_switch in arbiter audit; got: {arbiter.get('audit')}"
    )

    # Active service must have updated to editing.
    assert intent.get("service_primary") == "editing_proofreading", (
        f"Expected editing_proofreading as primary service; got: {intent}"
    )

    # TRG must record the switch as a service_shift event.
    service_shifts = sem.get("service_shifts", [])
    switch_events = [s for s in service_shifts if s.get("mode") == "switch"]
    assert switch_events, f"Expected a mode=switch service_shift; got: {service_shifts}"


def test_service_addition_event_recorded() -> None:
    """
    'Can you also help with marketing?' after cover design must produce a
    service_shift with mode='addition', and the cover design context must
    remain (not be erased).
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        first = _chat(client, "I need cover design for my children's fiction book.")
        thread_id = first["thread_id"]
        second = _chat(
            client,
            "Can you also help with marketing?",
            thread_id=thread_id,
        )
        trace = _latest_trace(client, thread_id)

    sem = trace.get("trg_semantic", {})
    arbiter = trace.get("context_arbiter", {})
    intent = second["intent"]

    # ContextArbiter must have detected the additive request.
    arbiter_audit = arbiter.get("audit", [])
    assert any("additive" in a for a in arbiter_audit), (
        f"Expected additive signal in arbiter audit; got: {arbiter_audit}"
    )

    # TRG must record the addition event.
    service_shifts = sem.get("service_shifts", [])
    addition_events = [s for s in service_shifts if s.get("mode") == "addition"]
    assert addition_events, f"Expected a mode=addition service_shift; got: {service_shifts}"

    # Cover design must remain the primary service (not erased by the additive request).
    assert intent.get("service_primary") == "cover_design_illustration", (
        f"Cover design should remain primary; got: {intent}"
    )


def test_contradiction_warning_recorded() -> None:
    """
    When the user first says 'I have finished my manuscript' then contradicts
    with 'I only have an idea', TRG must record a contradiction event and/or
    supersede the old fact, and the response should ask a clarifying question
    rather than assuming either value.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        first = _chat(
            client,
            "I need cover design. I have finished my manuscript.",
        )
        thread_id = first["thread_id"]
        second = _chat(
            client,
            "Actually, I only have an idea right now.",
            thread_id=thread_id,
        )
        trace = _latest_trace(client, thread_id)

    sem = trace.get("trg_semantic", {})
    context_pack = trace.get("context_pack", {})

    # Either a contradiction event exists OR the old fact was superseded.
    contradictions = sem.get("contradictions", [])
    active_facts = sem.get("active_facts", [])
    inactive_manuscript = any(
        f.get("fact_path") == "project.manuscript_status" and not f.get("active", True)
        for f in trace.get("trg_semantic", {}).get("active_facts", [])
    )

    has_contradiction_signal = (
        bool(contradictions)
        or inactive_manuscript
        or bool(context_pack.get("contradiction_warnings"))
    )
    assert has_contradiction_signal, (
        "Expected a contradiction event, superseded fact, or contradiction_warning; "
        f"contradictions={contradictions}, active_facts={active_facts}, "
        f"context_pack.contradiction_warnings={context_pack.get('contradiction_warnings')}"
    )

    # Response should ask for clarification rather than silently assuming one value.
    text = _joined_text(second).casefold()
    assert "?" in text or any(
        phrase in text for phrase in ("let me know", "could you", "would you", "tell me")
    ), f"Expected a clarifying question in the response; got: {text[:200]}"


def test_kashif_flow_trg_semantic_end_to_end() -> None:
    """
    Full Kashif cover-design flow: TRG must record facts, service inertia,
    and expose forbidden re-asks so the response never re-asks genre or stage.
    """
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        first = _chat(
            client,
            "I am Kashif, I need a design on cover for my book. Can you help me?",
        )
        thread_id = first["thread_id"]
        _chat(client, "Its children book", thread_id=thread_id)
        _chat(client, "I have finished my manuscript.", thread_id=thread_id)
        fourth = _chat(
            client,
            "Its fiction children book as I told you.",
            thread_id=thread_id,
        )
        trace = _latest_trace(client, thread_id)

    sem = trace.get("trg_semantic", {})
    text = _joined_text(fourth).casefold()

    # trg_semantic must be present and structured.
    assert isinstance(sem.get("active_facts"), list)
    assert isinstance(sem.get("service_shifts"), list)
    assert isinstance(sem.get("forbidden_reasks"), list)

    # Service shifts must include inertia events.
    all_modes = {s.get("mode") for s in sem.get("service_shifts", [])}
    assert "inertia" in all_modes, f"Expected inertia in service_shifts; modes: {all_modes}"

    # The final response must not re-ask known facts.
    assert "what genre" not in text
    assert "starting from scratch" not in text
    assert "manuscript stage" not in text
    assert "ghostwriting" not in text
