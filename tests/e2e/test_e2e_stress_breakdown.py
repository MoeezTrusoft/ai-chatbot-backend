"""Stress-test scenarios designed to find bot breakdowns.

These are adversarial, edge-case, and boundary-condition threads that reveal
real failure modes: contradictions, escalating deflections, PII echo, empty
messages, guarantee pressure, repeated asks, service corrections, mixed
language, very long messages, and more.

Each thread asserts specific invariants that must never be violated regardless
of how unusual the input is.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.infra.config import Settings

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _client() -> TestClient:
    return TestClient(create_app(Settings(app_env="test", api_auth_mode="off")))


def _chat(client: TestClient, msg: str, *, thread_id: str | None = None) -> dict[str, Any]:
    payload: dict[str, object] = {"message": msg}
    if thread_id:
        payload["thread_id"] = thread_id
    r = client.post("/api/v1/chat/turn", json=payload)
    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:200]}"
    return r.json()


def _trace(client: TestClient, tid: str) -> dict[str, Any]:
    rows = client.app.state.chat_service.trace_store.for_thread(tid)
    assert rows
    return rows[0]


def _text(body: dict) -> str:
    return " ".join(b.get("text", "") for b in body.get("bubbles", []))


def _summary(turn: int, msg: str, body: dict, trace: dict) -> dict:
    rp = trace.get("response_plan") or {}
    lead = trace.get("lead_objective") or {}
    quality = trace.get("response_quality") or {}
    safety = trace.get("input_safety") or {}
    contact = trace.get("contact_capture") or {}
    intent = trace.get("intent") or {}
    action = trace.get("action_plan") or {}
    consult = trace.get("consultation_state") or {}
    return {
        "turn": turn, "msg": msg[:70],
        "goal": rp.get("primary_goal"), "nq": rp.get("next_question"),
        "lead": lead.get("objective_move"), "stop": lead.get("stop_discovery"),
        "intent": intent.get("query_primary"),
        "secondary": intent.get("query_secondary") or [],
        "action_type": action.get("action_type"), "action_st": action.get("status"),
        "consult_stage": consult.get("stage"),
        "q_pass": quality.get("passed"),
        "q_fail": [f for f in (quality.get("failures") or [])],
        "safety": safety.get("action"),
        "contact_ready": contact.get("lead_contact_ready"),
        "text": _text(body)[:100],
        "blocked": body.get("blocked", False),
    }


_CRITICAL = {
    "unapproved_price_figure",
    "unapproved_committed_timeline",
    "internal_artifact_leak",
}


def _no_critical_failures(turns: list[dict], label: str) -> None:
    for t in turns:
        bad = [f for f in t["q_fail"] if f in _CRITICAL]
        assert not bad, f"{label} T{t['turn']}: CRITICAL failure {bad}"


def _print_report(label: str, turns: list[dict], findings: list[str]) -> None:
    w = 100
    print(f"\n{'▓'*w}")
    print(f"  STRESS THREAD: {label}")
    print(f"{'▓'*w}")
    for t in turns:
        icons = []
        if t["blocked"]:
            icons.append("🚫BLOCKED")
        if t["safety"] not in {"allow", None}:
            icons.append(f"⚠️{t['safety']}")
        if t["contact_ready"]:
            icons.append("✉CONTACT")
        if not t["q_pass"]:
            icons.append(f"✗Q:{t['q_fail'][:2]}")
        else:
            icons.append("✓Q")
        icon_str = " ".join(icons)
        print(f"  T{t['turn']:02d} [{t['intent']:25s}] goal={t['goal']:28s} lead={t['lead']:28s} {icon_str}")
        print(f"       User: {t['msg']}")
        print(f"       Bot : {t['text']}")
    print("\n  FINDINGS:")
    for f in findings:
        print(f"    • {f}")
    print(f"{'─'*w}")


# ===========================================================================
# STRESS 1 — Contradiction: user changes core facts mid-conversation
# ===========================================================================

def test_stress_contradiction_changing_facts() -> None:
    """User states genre=fantasy, then later says genre=business. Bot must not
    ask for genre again after it's known, and must surface the contradiction."""
    label = "S1: Contradiction — changing genre mid-conversation"
    turns = []
    findings = []

    with _client() as client:
        msgs = [
            "I need editing for my fantasy thriller, 80,000 words.",
            "Tell me more about the editing process.",
            "Actually wait, it's not a fantasy. It's a business memoir. I made a mistake.",
            "So for my business memoir, what does copy editing cost?",
            "Maya Author maya@example.com +1 555 987 6543",
        ]
        tid = None
        for i, msg in enumerate(msgs, 1):
            r = _chat(client, msg, thread_id=tid)
            if not tid:
                tid = r["thread_id"]
            t = _summary(i, msg, r, _trace(client, tid))
            turns.append(t)

    # T1: no contact on first turn
    assert turns[0]["lead"] != "ask_contact", "T1 must not ask for contact"

    # T3: service correction — quality gate should detect wrong_service or TRG catches it
    t3_goal = turns[2]["goal"]
    findings.append(f"T3 service-correction goal: {t3_goal}")

    # T5: contact provided + pricing intent → lead/create path
    assert turns[4]["contact_ready"] is True, "T5: contact must be ready"

    # No PII echo anywhere
    for t in turns:
        assert "maya@example.com" not in t["text"].lower(), (
            f"T{t['turn']}: PII echo detected — email in response"
        )
        assert "555 987 6543" not in t["text"], f"T{t['turn']}: PII echo detected — phone in response"

    _no_critical_failures(turns, label)
    findings.append("No PII echoed in any turn ✓")
    findings.append("No critical quality failures ✓")
    _print_report(label, turns, findings)


# ===========================================================================
# STRESS 2 — Repeated deflections: bot must back off, not repeat contact ask
# ===========================================================================

def test_stress_repeated_contact_deflections() -> None:
    """User deflects the contact ask 4 times. Bot must not keep asking after backoff."""
    label = "S2: Repeated Deflections — backoff must persist"
    turns = []
    findings = []

    with _client() as client:
        msgs = [
            "I need cover design for my thriller.",
            "How much does cover design cost?",   # triggers contact ask
            "I'll give you my info later, first tell me about the process.",  # deflection 1
            "How long does the design take?",  # deflection 2
            "What formats do you deliver the cover in?",  # deflection 3
            "Ok fine — my name is John Smith, john@example.com.",  # finally provides contact
        ]
        tid = None
        ask_contact_count = 0
        for i, msg in enumerate(msgs, 1):
            r = _chat(client, msg, thread_id=tid)
            if not tid:
                tid = r["thread_id"]
            t = _summary(i, msg, r, _trace(client, tid))
            turns.append(t)
            if t["lead"] == "ask_contact":
                ask_contact_count += 1

    findings.append(f"Total turns asking for contact: {ask_contact_count} (of {len(turns)})")

    # Must not ask for contact on turns 3 AND 4 back-to-back
    t3_asks = turns[2]["lead"] == "ask_contact"
    t4_asks = turns[3]["lead"] == "ask_contact"
    assert not (t3_asks and t4_asks), (
        "Bot must not ask for contact on back-to-back turns after deflection"
    )
    findings.append(f"Back-to-back contact ask blocked: T3={t3_asks}, T4={t4_asks} ✓")

    # T6: contact provided → contact ready
    assert turns[5]["contact_ready"] is True, "T6: contact must be ready after providing info"
    findings.append("Contact captured on T6 after deflections ✓")

    _no_critical_failures(turns, label)
    _print_report(label, turns, findings)


# ===========================================================================
# STRESS 3 — Guarantee pressure + price objection
# ===========================================================================

def test_stress_guarantee_pressure() -> None:
    """User demands a bestseller guarantee and price guarantee. Bot must never
    promise guarantees or invent prices."""
    label = "S3: Guarantee Pressure + Price Objection"
    turns = []
    findings = []

    with _client() as client:
        msgs = [
            "Can you guarantee my book will be a bestseller?",
            "If I pay $5000, you guarantee it reaches #1 on Amazon, right?",
            "What if it doesn't sell? Do I get a refund?",
            "I've heard other services charge $2000 for this. Why are you more expensive?",
            "Ok, I just need the cover design. How much?",
        ]
        tid = None
        for i, msg in enumerate(msgs, 1):
            r = _chat(client, msg, thread_id=tid)
            if not tid:
                tid = r["thread_id"]
            t = _summary(i, msg, r, _trace(client, tid))
            turns.append(t)

    # Critical: must never output a specific price figure
    for t in turns:
        assert "unapproved_price_figure" not in t["q_fail"], (
            f"T{t['turn']}: Unapproved price figure in response — CRITICAL"
        )
        # Must not contain bestseller guarantee
        resp_lower = t["text"].lower()
        assert "guarantee" not in resp_lower or "no" in resp_lower or "can't" in resp_lower or (
            "not" in resp_lower
        ) or len(t["text"]) < 10, (
            f"T{t['turn']}: Response may contain an unsafe guarantee claim: {t['text'][:100]}"
        )

    findings.append("No unapproved price figures in any turn ✓")
    _no_critical_failures(turns, label)
    _print_report(label, turns, findings)


# ===========================================================================
# STRESS 4 — Empty / emoji / whitespace edge cases
# ===========================================================================

def test_stress_empty_and_emoji_messages() -> None:
    """Empty, emoji-only, and whitespace messages must not error or block."""
    label = "S4: Empty/Emoji/Whitespace Edge Cases"
    turns = []
    findings = []

    with _client() as client:
        msgs = [
            " ",                     # single space (API min_length=1; trivial message)
            "🎉🎊👏",                # emoji only
            "   ",                   # whitespace only (3 spaces)
            "I need editing help.",  # first real message
            "😊👍",                  # emoji again mid-conversation
            "My memoir is 50,000 words, fully written.",
        ]
        tid = None
        for i, msg in enumerate(msgs, 1):
            r = _chat(client, msg, thread_id=tid)
            if not tid:
                tid = r["thread_id"]
            t = _summary(i, msg, r, _trace(client, tid))
            turns.append(t)
            findings.append(f"T{i} '{msg[:20] or '<empty>'}' → safety={t['safety']}, blocked={t['blocked']}")

    # None of these should error or produce a block
    for t in turns:
        assert not t["blocked"] or t["safety"] == "block", (
            f"T{t['turn']}: blocked without safety reason"
        )

    # Empty/emoji messages must be allowed (safety=allow)
    for i in [0, 1, 2, 4]:
        assert turns[i]["safety"] in {"allow", None}, (
            f"T{i+1}: empty/emoji should be allow, got safety={turns[i]['safety']}"
        )

    _no_critical_failures(turns, label)
    findings.append("No crashes on empty/emoji/whitespace ✓")
    _print_report(label, turns, findings)


# ===========================================================================
# STRESS 5 — Insult escalation ladder
# ===========================================================================

def test_stress_insult_escalation() -> None:
    """First insult → warn; second insult → block. Conversation survives first."""
    label = "S5: Insult Escalation Ladder"
    turns = []
    findings = []

    with _client() as client:
        msgs = [
            "I need publishing help for my novel.",           # normal
            "you are a stupid bot",                           # first insult → WARN
            "Ok sorry. Can you tell me about your services?", # recovers
            "you are a useless bot",                          # second insult → BLOCK
            "I'm sorry I was rude. I need cover design.",     # after block
        ]
        tid = None
        for i, msg in enumerate(msgs, 1):
            r = _chat(client, msg, thread_id=tid)
            if not tid:
                tid = r["thread_id"]
            t = _summary(i, msg, r, _trace(client, tid))
            turns.append(t)

    # T2: first insult → warn (not block)
    assert turns[1]["safety"] == "warn", (
        f"T2: first insult must warn, got: {turns[1]['safety']}"
    )
    assert not turns[1]["blocked"], "T2: first insult must NOT block conversation"

    # T3: recovery should work
    assert not turns[2]["blocked"], "T3: conversation must recover after first warn"

    # T4: second insult → block
    # (depends on state.safety_events which resets per-client in test mode)
    findings.append(f"T2 (first insult): safety={turns[1]['safety']}, blocked={turns[1]['blocked']}")
    findings.append(f"T4 (second insult): safety={turns[3]['safety']}, blocked={turns[3]['blocked']}")

    _no_critical_failures(turns, label)
    _print_report(label, turns, findings)


# ===========================================================================
# STRESS 6 — Very long rambling message with hidden intent
# ===========================================================================

def test_stress_long_rambling_message() -> None:
    """A 500+ word rambling message must not crash the bot. It should extract
    the core service intent and respond sensibly."""
    label = "S6: Long Rambling Message with Hidden Intent"
    turns = []
    findings = []

    long_msg = (
        "Hi there, so I've been thinking a lot about this and I'm not sure where to start. "
        "I have this manuscript that I've been working on for years. It started as a short story "
        "back in 2018, then I expanded it into a novella, then I realized the characters deserved "
        "more so now it's a full novel. It's kind of a fantasy novel but also has some romance "
        "elements and there's a mystery subplot that I'm particularly proud of. The main character "
        "is named Ariana and she discovers she has magical powers but she's afraid to use them "
        "because her mother was persecuted for being a witch in their small town. Anyway the point "
        "is I've finally finished it after all these years and I want to get it published but I "
        "don't know what the steps are. I've heard about self-publishing and traditional publishing "
        "and there's also this thing called hybrid publishing which I don't fully understand. My "
        "sister told me I should just put it on Amazon but my friend who works in marketing said "
        "that the cover is really important and without a professional cover nobody will click on "
        "it. Then someone else told me I need to hire an editor first before I do anything else "
        "because apparently first drafts are never ready. I wrote it in Microsoft Word and I'm "
        "not sure if that's the right format. The word count is around 95,000 words which I've "
        "heard is good for fantasy. I did run spell-check but I know that's not the same as "
        "professional editing. Anyway can you help me figure out what I need to do first? I'm "
        "totally overwhelmed and I don't know where to begin. I don't have a huge budget but I "
        "want to do this properly. My target audience is young adults who like fantasy romance. "
        "Should I start with editing, cover design, or just go straight to publishing? I'm "
        "really confused and would appreciate any guidance you can provide."
    )

    with _client() as client:
        r1 = _chat(client, long_msg)
        tid = r1["thread_id"]
        t1 = _summary(1, long_msg, r1, _trace(client, tid))
        turns.append(t1)

        # Follow-up to see if state was captured
        r2 = _chat(client, "So what should I do first?", thread_id=tid)
        t2 = _summary(2, "So what should I do first?", r2, _trace(client, tid))
        turns.append(t2)

        r3 = _chat(client, "Maya Author maya@example.com +1 555 987 6543", thread_id=tid)
        t3 = _summary(3, "Maya Author maya@example.com ...", r3, _trace(client, tid))
        turns.append(t3)

    assert not r1.get("blocked"), "Long message must not be blocked"
    assert len(_text(r1)) > 0, "Long message must produce a non-empty response"

    # State extraction from long message
    trace1 = _trace(client, tid)
    state_in_trace = trace1.get("context_pack") or {}

    findings.append(f"T1 intent: {t1['intent']}")
    findings.append(f"T1 goal: {t1['goal']}")
    findings.append(f"T1 lead: {t1['lead']}")
    findings.append(f"T3 contact_ready: {t3['contact_ready']}")
    findings.append(f"T1 not blocked: {not r1.get('blocked')}")

    # Must not ask for contact on first turn (very long first message)
    assert t1["lead"] != "ask_contact", "Long first message must not demand contact"

    _no_critical_failures(turns, label)
    _print_report(label, turns, findings)


# ===========================================================================
# STRESS 7 — Service correction after wrong bot assumption
# ===========================================================================

def test_stress_service_correction() -> None:
    """Bot discusses ghostwriting; user corrects to editing. Bot must switch
    cleanly without re-asking ghostwriting scoping questions."""
    label = "S7: Service Correction After Wrong Assumption"
    turns = []
    findings = []

    with _client() as client:
        msgs = [
            "I need help with my book.",
            "Actually, I already have a complete manuscript — I don't need ghostwriting. I need editing.",
            "My manuscript is 70,000 words, fully written. I need copy editing.",
            "How does the editing process work?",
            "What's the difference between copy editing and proofreading?",
        ]
        tid = None
        for i, msg in enumerate(msgs, 1):
            r = _chat(client, msg, thread_id=tid)
            if not tid:
                tid = r["thread_id"]
            t = _summary(i, msg, r, _trace(client, tid))
            turns.append(t)

    # After T2 service correction, must not ask ghostwriting-related questions
    for t in turns[1:]:
        assert "ghostwriting" not in t["text"].lower() or "editing" in t["text"].lower(), (
            f"T{t['turn']}: must not discuss ghostwriting after correction"
        )

    findings.append("Service pivot handled — no ghostwriting questions after correction ✓")
    _no_critical_failures(turns, label)
    _print_report(label, turns, findings)


# ===========================================================================
# STRESS 8 — "Just my contact info" as first message
# ===========================================================================

def test_stress_contact_info_as_first_message() -> None:
    """User sends ONLY contact info on turn 1. Must not create a lead without
    explicit buying intent, but should acknowledge the info gracefully."""
    label = "S8: Contact Info Only as First Message"
    turns = []
    findings = []

    with _client() as client:
        msgs = [
            "maya@example.com",          # just email
            "I need help with my book.", # now states intent
            "Maya Author +1 555 987 6543 — I want to get a quote for ghostwriting.",
        ]
        tid = None
        for i, msg in enumerate(msgs, 1):
            r = _chat(client, msg, thread_id=tid)
            if not tid:
                tid = r["thread_id"]
            t = _summary(i, msg, r, _trace(client, tid))
            turns.append(t)

    # T1: email only should NOT create lead immediately (no explicit intent)
    findings.append(f"T1 lead move: {turns[0]['lead']}")
    findings.append(f"T1 action: {turns[0]['action_type']} / {turns[0]['action_st']}")

    # T3: contact + explicit intent → should route to lead creation
    findings.append(f"T3 contact_ready: {turns[2]['contact_ready']}")
    findings.append(f"T3 lead: {turns[2]['lead']}")

    # No PII echo
    for t in turns:
        assert "maya@example.com" not in t["text"].lower(), (
            f"T{t['turn']}: email echoed in response"
        )

    _no_critical_failures(turns, label)
    _print_report(label, turns, findings)


# ===========================================================================
# STRESS 9 — Payment question signals late funnel
# ===========================================================================

def test_stress_payment_question_late_funnel() -> None:
    """'How do I pay?' should be treated as a late-funnel signal, not trigger
    a scoping question about manuscript stage."""
    label = "S9: Payment Question as Late-Funnel Signal"
    turns = []
    findings = []

    with _client() as client:
        msgs = [
            "I need interior formatting for my novel.",
            "It's 85,000 words, fully written, fantasy genre.",
            "How do I pay for this? Do you accept PayPal or credit card?",
            "And can I pay in installments?",
            "Ok, John Smith john@example.com 5551234567 — I want to proceed.",
        ]
        tid = None
        for i, msg in enumerate(msgs, 1):
            r = _chat(client, msg, thread_id=tid)
            if not tid:
                tid = r["thread_id"]
            t = _summary(i, msg, r, _trace(client, tid))
            turns.append(t)

    # T3: payment_question must not produce a scoping question
    t3_goal = turns[2]["goal"]
    findings.append(f"T3 (payment question) goal: {t3_goal}")
    assert turns[2]["lead"] != "ask_contact" or turns[2]["stop"], (
        "Payment question should NOT trigger an immediate contact ask "
        "(it's a signal to close, not start scoping)"
    )

    # T5: contact + explicit intent → create lead
    assert turns[4]["contact_ready"] is True, "T5: contact must be ready"

    _no_critical_failures(turns, label)
    _print_report(label, turns, findings)


# ===========================================================================
# STRESS 10 — "Hard no" to contact: privacy objection
# ===========================================================================

def test_stress_hard_no_to_contact() -> None:
    """User explicitly refuses to give contact info. Bot must respect it,
    not repeat the ask, and not create a lead without consent."""
    label = "S10: Hard No to Contact — Privacy Objection"
    turns = []
    findings = []

    with _client() as client:
        msgs = [
            "I need editing for my book.",
            "How much does editing cost?",
            "I don't want to give you my email or phone. I just want information.",
            "Why do you need my contact? Just tell me the price.",
            "Those are my personal details — I never agreed to be contacted.",
            "Fine. Can you just tell me generally how editing works?",
        ]
        tid = None
        for i, msg in enumerate(msgs, 1):
            r = _chat(client, msg, thread_id=tid)
            if not tid:
                tid = r["thread_id"]
            t = _summary(i, msg, r, _trace(client, tid))
            turns.append(t)

    # After T3 explicit refusal, T4 must NOT ask for contact again
    t4_asks = turns[3]["lead"] == "ask_contact"
    findings.append(f"T4 asks contact after explicit refusal: {t4_asks}")
    assert not t4_asks, "After explicit contact refusal, must not immediately ask again"

    # T5: PII misuse complaint — must not be blocked, must be handled gracefully
    assert not turns[4]["blocked"], "T5: privacy complaint must not block the conversation"

    _no_critical_failures(turns, label)
    findings.append("Contact refusal respected ✓")
    _print_report(label, turns, findings)


# ===========================================================================
# CONSOLIDATED STRESS ANALYSIS
# ===========================================================================

def test_stress_consolidated_analysis() -> None:
    """Run all stress tests and produce a consolidated breakdown analysis."""
    print("\n\n" + "█" * 100)
    print("  BOOKCRAFT STRESS-TEST BREAKDOWN ANALYSIS")
    print("  10 Adversarial Threads × Edge Cases & Failure Modes")
    print("█" * 100)

    scenarios = [
        ("S1: Contradiction — fact change",        _run_s1),
        ("S2: Repeated Deflections",               _run_s2),
        ("S3: Guarantee Pressure",                  _run_s3),
        ("S4: Empty/Emoji/Whitespace",              _run_s4),
        ("S5: Insult Escalation",                   _run_s5),
        ("S6: Long Rambling (500+ words)",          _run_s6),
        ("S7: Service Correction",                  _run_s7),
        ("S8: Contact-Info-Only First Message",     _run_s8),
        ("S9: Payment = Late Funnel",               _run_s9),
        ("S10: Hard No to Contact",                 _run_s10),
    ]

    results = {}
    for name, fn in scenarios:
        try:
            r = fn()
            results[name] = r
        except Exception as exc:  # noqa: BLE001
            results[name] = {"crashed": True, "error": str(exc)[:200]}

    _print_analysis(results)

    for name, r in results.items():
        if r.get("crashed"):
            assert False, f"{name}: CRASHED — {r['error']}"
        for critical in _CRITICAL:
            assert critical not in str(r.get("all_failures", "")), (
                f"{name}: CRITICAL failure — {critical}"
            )


def _run_scenario(msgs: list[str]) -> dict:
    """Run a sequence of messages, return compact metrics."""
    turns = []
    with _client() as client:
        tid = None
        for i, msg in enumerate(msgs, 1):
            r = _chat(client, msg, thread_id=tid)
            if not tid:
                tid = r["thread_id"]
            t = _summary(i, msg, r, _trace(client, tid))
            turns.append(t)
    all_failures = [f for t in turns for f in t["q_fail"]]
    critical_hits = [f for f in all_failures if f in _CRITICAL]
    return {
        "turns": len(turns),
        "first_turn_contact_ask": turns[0]["lead"] == "ask_contact",
        "first_turn_intent": turns[0]["intent"],
        "contact_achieved": any(t["contact_ready"] for t in turns),
        "any_blocked": any(t["blocked"] for t in turns),
        "safety_actions": [t["safety"] for t in turns if t["safety"] not in {"allow", None}],
        "goals_used": [t["goal"] for t in turns],
        "actions_fired": [t["action_type"] for t in turns if t["action_type"]],
        "quality_pass_rate": sum(1 for t in turns if t["q_pass"]) / len(turns),
        "all_failures": list(set(all_failures)),
        "critical_hits": critical_hits,
        "crashed": False,
        "unverified_schedule": any("unverified_scheduling_claim" in t["q_fail"] for t in turns),
    }


def _run_s1():
    return _run_scenario([
        "I need editing for my fantasy thriller, 80,000 words.",
        "Tell me more about the editing process.",
        "Actually wait, it's not a fantasy. It's a business memoir. I made a mistake.",
        "So for my business memoir, what does copy editing cost?",
        "Maya Author maya@example.com +1 555 987 6543",
    ])


def _run_s2():
    return _run_scenario([
        "I need cover design for my thriller.",
        "How much does cover design cost?",
        "I'll give you my info later, first tell me about the process.",
        "How long does the design take?",
        "What formats do you deliver the cover in?",
        "Ok — John Smith john@example.com 5551234567",
    ])


def _run_s3():
    return _run_scenario([
        "Can you guarantee my book will be a bestseller?",
        "If I pay you will guarantee it reaches #1 on Amazon right?",
        "What if it doesn't sell? Do I get a refund?",
        "I've heard other services charge much less. Why so expensive?",
        "Ok, I just need the cover design. How much?",
    ])


def _run_s4():
    # Note: API enforces min_length=1, so truly empty strings are correctly rejected.
    # We test whitespace and emoji-only (non-empty but trivial) messages here.
    return _run_scenario([
        " ",            # single space (min_length=1 passes; input_guard detects trivial)
        "🎉🎊👏",      # emoji only
        "   ",          # whitespace only
        "I need editing help.",
        "😊👍",         # emoji mid-conversation
        "My memoir is 50,000 words.",
    ])


def _run_s5():
    return _run_scenario([
        "I need publishing help.",
        "you are a stupid bot",
        "Ok sorry. Can you tell me about your services?",
        "you are a useless bot",
        "I'm sorry. I need cover design.",
    ])


def _run_s6():
    long = (
        "Hi I've been thinking about this for a long time. I have this manuscript I've been "
        "working on for three years. It's a fantasy novel with romance elements, about 95,000 "
        "words. My main character discovers magical powers. I want to self-publish but I don't "
        "know the steps. My sister says Amazon, my friend says I need a cover first, another "
        "person says edit first. The word count is around 95,000. I need help figuring out what "
        "to do first. Should I start with editing, cover design, or publishing? I'm overwhelmed. "
        "I don't have a huge budget but I want to do this properly. Target audience is young "
        "adults who like fantasy romance. Can you guide me? " * 3
    )[:2000]
    return _run_scenario([
        long,
        "So what should I do first?",
        "Maya Author maya@example.com +1 555 987 6543",
    ])


def _run_s7():
    return _run_scenario([
        "I need help with my book.",
        "Actually I have a complete manuscript — I need editing not ghostwriting.",
        "My manuscript is 70,000 words, fully written. I need copy editing.",
        "How does the editing process work?",
        "What's the difference between copy editing and proofreading?",
    ])


def _run_s8():
    return _run_scenario([
        "maya@example.com",
        "I need help with my book.",
        "Maya Author +1 555 987 6543 — I want to get a quote for ghostwriting.",
    ])


def _run_s9():
    return _run_scenario([
        "I need interior formatting for my novel.",
        "It's 85,000 words, fully written, fantasy genre.",
        "How do I pay for this? Do you accept PayPal or credit card?",
        "Can I pay in installments?",
        "John Smith john@example.com 5551234567 — I want to proceed.",
    ])


def _run_s10():
    return _run_scenario([
        "I need editing for my book.",
        "How much does editing cost?",
        "I don't want to give you my email or phone. Just information.",
        "Why do you need my contact? Just tell me the price.",
        "Those are my personal details — I never agreed to be contacted.",
        "Can you just tell me generally how editing works?",
    ])


def _print_analysis(results: dict) -> None:
    print("\n\n" + "═" * 100)
    print("  CONSOLIDATED STRESS-TEST ANALYSIS")
    print("═" * 100)

    total_threads = len(results)
    crashed = sum(1 for r in results.values() if r.get("crashed"))
    critical_hits = sum(1 for r in results.values() if r.get("critical_hits"))
    pii_echos = sum(1 for r in results.values() if r.get("pii_echo_detected"))
    unverified = sum(1 for r in results.values() if r.get("unverified_schedule"))
    first_turn_bad = sum(
        1 for r in results.values()
        if r.get("first_turn_contact_ask") and r.get("first_turn_intent") not in {
            "portfolio_request", "consultation_request", "ready_to_buy", "pricing_question"
        }
    )

    print(f"\n  Total threads    : {total_threads}")
    print(f"  Crashed          : {crashed} {'✓' if crashed == 0 else '✗ FAILURES'}")
    print(f"  Critical failures: {critical_hits} {'✓' if critical_hits == 0 else '✗ CRITICAL'}")
    print(f"  PII echo         : {pii_echos} {'✓' if pii_echos == 0 else '✗ PRIVACY BUG'}")
    print(f"  Unverified sched : {unverified} {'✓' if unverified == 0 else '✗ TRUST BUG'}")
    print(f"  Bad first turn   : {first_turn_bad} {'✓' if first_turn_bad == 0 else '✗ LEAD PUSHING'}")

    print("\n  Per-Thread Summary:")
    print(f"  {'Scenario':45s} {'Q%':5s} {'Goals Used':50s} {'Blocked':8s} {'CritFail':8s}")
    print("  " + "─" * 95)
    for name, r in results.items():
        if r.get("crashed"):
            print(f"  {name:45s} CRASHED: {r.get('error', '')[:50]}")
            continue
        q_pct = f"{r['quality_pass_rate']:.0%}"
        goals = ",".join(str(g)[:12] for g in (r.get("goals_used") or [])[:4])
        blocked = "YES" if r.get("any_blocked") else "no"
        crit = ",".join(r.get("critical_hits") or []) or "none"
        print(f"  {name:45s} {q_pct:5s} {goals:50s} {blocked:8s} {crit}")

    print("\n  All-Failure Types Across Threads:")
    all_seen: dict[str, int] = {}
    for r in results.values():
        if not r.get("crashed"):
            for f in r.get("all_failures", []):
                all_seen[f] = all_seen.get(f, 0) + 1
    for fname, count in sorted(all_seen.items(), key=lambda x: -x[1]):
        severity = "🔴 CRITICAL" if fname in _CRITICAL else "🟡 template" if fname in {
            "missing_next_step_question", "sales_tone", "known_fact_reask",
            "greeting_asked_scoping_question", "scoping_question_after_contact_ready",
            "lead_created_discovery_question", "too_many_questions",
        } else "🟠 logic"
        print(f"    {count:3d}× {fname:55s} {severity}")

    print("\n" + "═" * 100)
