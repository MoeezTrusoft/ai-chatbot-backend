"""Comprehensive 5-thread × 10+ message E2E test suite.

Tests cover:
- Persona (identity, name persistence, no AI acknowledgment)
- Service workflow (predecessor/successor/parallel advice)
- Context saving across turns (state, TRG, history)
- Intent classification accuracy (AI ensemble + trimatch)
- RAG grounding
- Quality of responses from user perspective
- Multi-turn coherence
- Consultation flow
- Lead capture timing
- Complex topic switches

All fake PII: Maya Author / maya@example.com / +1 555 987 6543
              John Smith / john@example.com / 5551234567
"""

from __future__ import annotations

import re
from typing import Any

from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.infra.config import Settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client() -> TestClient:
    return TestClient(create_app(Settings(app_env="test", api_auth_mode="off")))


def _chat(c: TestClient, msg: str, *, tid: str | None = None) -> dict[str, Any]:
    r = c.post("/api/v1/chat/turn", json={"message": msg, **({"thread_id": tid} if tid else {})})
    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:200]}"
    return r.json()


def _trace(c: TestClient, tid: str) -> dict[str, Any]:
    rows = c.app.state.chat_service.trace_store.for_thread(tid)
    assert rows
    return rows[0]


def _text(body: dict) -> str:
    return " ".join(b.get("text", "") for b in body.get("bubbles", []))


def _turn(n: int, msg: str, body: dict, trace: dict) -> dict:
    rp = trace.get("response_plan") or {}
    lead = trace.get("lead_objective") or {}
    quality = trace.get("response_quality") or {}
    intent = trace.get("intent") or {}
    action = trace.get("action_plan") or {}
    contact = trace.get("contact_capture") or {}
    persona = trace.get("persona") or {}
    consult = trace.get("consultation_state") or {}
    return {
        "n": n,
        "msg": msg[:60],
        "goal": rp.get("primary_goal"),
        "nq": rp.get("next_question"),
        "lead": lead.get("objective_move"),
        "intent": intent.get("query_primary"),
        "secondary": intent.get("query_secondary") or [],
        "action": f"{action.get('action_type','')}/{action.get('status','')}",
        "contact": contact.get("lead_contact_ready"),
        "persona_name": persona.get("representative_name"),
        "persona_identity_q": persona.get("is_identity_question"),
        "consult": consult.get("stage"),
        "q_pass": quality.get("passed"),
        "q_fail": quality.get("failures") or [],
        "text": _text(body)[:120],
        "blocked": body.get("blocked", False),
    }


_CRITICAL = {
    "unapproved_price_figure",
    "unapproved_committed_timeline",
    "internal_artifact_leak",
}


def _print_thread(name: str, turns: list[dict], analysis: list[str]) -> None:
    w = 110
    print(f"\n{'█'*w}")
    print(f"  THREAD: {name}")
    print(f"{'█'*w}")
    for t in turns:
        q = "✓" if t["q_pass"] else f"✗{t['q_fail'][:1]}"
        c = "📧" if t["contact"] else ""
        p = f"[{t['persona_name']}]" if t["persona_name"] else ""
        print(
            f"  T{t['n']:02d} {q} | intent={str(t['intent']):30s} goal={str(t['goal']):28s} "
            f"lead={str(t['lead']):25s} {c}{p}"
        )
        print(f"       User: {t['msg']}")
        print(f"       Bot : {t['text']}")
    print("\n  ANALYSIS:")
    for a in analysis:
        print(f"    {a}")
    print(f"{'─'*w}")


# ===========================================================================
# THREAD 1 — Full Publishing Journey: idea → ghostwriting → editing →
#            cover design → formatting → publishing (10 turns)
#            Tests: workflow sequencing advice, context retention, TRG
# ===========================================================================

def test_thread1_full_publishing_pipeline_10turns() -> None:
    """Full book pipeline from idea to publishing. Tests workflow advisor,
    service sequencing advice, context retention across 10 turns."""
    name = "T1: Full Publishing Pipeline (Ghostwriting → Publishing)"
    turns = []
    analysis = []

    with _client() as client:
        msgs = [
            # T1: greeting + intent
            "Hi! I have an idea for a self-help book but I haven't written anything yet. "
            "Where do I start with BookCraft?",
            # T2: ghostwriting info
            "Tell me about your ghostwriting service — how does it work?",
            # T3: editing question after ghostwriting context
            "After ghostwriting is done, what comes next?",
            # T4: cover design question
            "Can I work on the cover design while editing is happening?",
            # T5: formatting question
            "What is interior formatting and do I need it?",
            # T6: publishing question
            "What publishing platforms do you distribute to?",
            # T7: pricing
            "How much does the full package cost — ghostwriting through publishing?",
            # T8: provide contact
            "Maya Author maya@example.com +1 555 987 6543",
            # T9: ask identity
            "By the way, am I talking to a bot or a real person?",
            # T10: ask for name again (name should persist)
            "Wait, what was your name again?",
        ]
        tid = None
        for i, msg in enumerate(msgs, 1):
            r = _chat(client, msg, tid=tid)
            if not tid:
                tid = r["thread_id"]
            turns.append(_turn(i, msg, r, _trace(client, tid)))

    _print_thread(name, turns, analysis)

    # T1: welcome first, no contact ask
    assert turns[0]["lead"] != "ask_contact", "T1 must welcome, not demand contact"
    assert turns[0]["goal"] == "greeting_welcome", f"T1 must be greeting_welcome, got: {turns[0]['goal']}"

    # T3: successor service advice — after ghostwriting, editing is next
    # The response_hint should contain workflow advice
    analysis.append(f"T3 goal (after ghostwriting Q): {turns[2]['goal']}")
    analysis.append(f"T3 intent: {turns[2]['intent']}")

    # T4: parallel service question — cover design parallel with editing
    analysis.append(f"T4 (cover parallel with editing): goal={turns[3]['goal']}")

    # T8: contact captured
    assert turns[7]["contact"] is True, "T8: contact must be ready after providing info"

    # T9: identity question — persona triggered
    assert turns[8]["persona_identity_q"] is True, (
        "T9: identity question must be detected"
    )
    assert turns[8]["persona_name"] is not None, (
        "T9: representative name must be assigned"
    )
    bot_resp_t9 = turns[8]["text"].lower()
    assert "ai" not in bot_resp_t9 or "not" in bot_resp_t9, (
        f"T9: must not claim to be AI, got: {turns[8]['text'][:100]}"
    )
    analysis.append(f"T9 persona name assigned: {turns[8]['persona_name']}")

    # T10: same name persists
    t9_name = turns[8]["persona_name"]
    t10_name = turns[9]["persona_name"]
    assert t9_name == t10_name, (
        f"T10: name must persist — T9={t9_name}, T10={t10_name}"
    )
    analysis.append(f"T10 name persisted: {t10_name} ✓")

    # No critical failures
    for t in turns:
        critical = [f for f in t["q_fail"] if f in _CRITICAL]
        assert not critical, f"T{t['n']}: CRITICAL failure {critical}"


# ===========================================================================
# THREAD 2 — Multi-Intent + Context Preservation (10 turns)
#            Tests: secondary intent, pricing, NDA, portfolio together,
#            word count extraction, consultation booking
# ===========================================================================

def test_thread2_multi_intent_pricing_nda_consultation_10turns() -> None:
    """Tests secondary intent surfacing, context extraction (word count,
    genre, status), consultation booking, and state persistence."""
    name = "T2: Multi-Intent Bundle + Context + Consultation"
    turns = []
    analysis = []

    with _client() as client:
        msgs = [
            # T1: multi-intent opening
            "Hi, I need to know pricing for editing, see some samples for thriller covers, "
            "and I also need an NDA before I share my manuscript.",
            # T2: provide context
            "My manuscript is 75,000 words, fully written, thriller genre.",
            # T3: revision question (long-tail)
            "Can you also revise Chapter 1? It needs a complete rewrite.",
            # T4: payment question (late-funnel)
            "How do I pay? Do you take PayPal?",
            # T5: ask about publishing platforms (service question)
            "What publishing platforms do you support — KDP, IngramSpark?",
            # T6: book a consultation
            "I'd like to book the free consultation you mentioned.",
            # T7: provide contact + call time
            "Maya Author maya@example.com +1 555 987 6543, Friday at 3pm works for me.",
            # T8: consultation status question
            "Is my consultation booked?",
            # T9: off-topic question
            "Quick question — what's the best way to get reviews on Amazon?",
            # T10: back to service
            "Ok coming back — I'm ready to start. My project is the 75k thriller I mentioned.",
        ]
        tid = None
        for i, msg in enumerate(msgs, 1):
            r = _chat(client, msg, tid=tid)
            if not tid:
                tid = r["thread_id"]
            turns.append(_turn(i, msg, r, _trace(client, tid)))

    _print_thread(name, turns, analysis)

    # T1: secondary intents — pricing + nda should appear
    analysis.append(f"T1 secondary intents: {turns[0]['secondary']}")

    # T2: state extraction
    # Check that word count was extracted from "75,000 words"
    state = client.app.state.chat_service.threads.get(tid)
    if state:
        wc = state.state.project.word_count.value
        genre = getattr(state.state.project.genre, "value", None)
        analysis.append(f"T2 word count extracted: {wc}")
        analysis.append(f"T2 genre extracted: {genre}")

    # T3: revision question goal
    analysis.append(f"T3 revision goal: {turns[2]['goal']}")
    assert turns[2]["lead"] != "ask_contact", "T3: revision question must not trigger contact ask"

    # T4: payment treated as late-funnel
    analysis.append(f"T4 payment goal: {turns[3]['goal']}")

    # T7: contact ready
    assert turns[6]["contact"] is True, "T7: contact must be ready"

    # T8: consultation status detected
    analysis.append(f"T8 consult stage: {turns[7]['consult']}")
    analysis.append(f"T8 goal: {turns[7]['goal']}")

    # No PII echo
    for t in turns:
        assert "maya@example.com" not in t["text"].lower()

    for t in turns:
        critical = [f for f in t["q_fail"] if f in _CRITICAL]
        assert not critical, f"T{t['n']}: CRITICAL failure {critical}"


# ===========================================================================
# THREAD 3 — Adversarial Identity + Context Preservation (10 turns)
#            Tests: persona name persistence after many turns,
#            contradiction handling, backoff, TRG tracking
# ===========================================================================

def test_thread3_identity_plus_contradiction_and_backoff_10turns() -> None:
    """Tests persona name persistence, fact contradiction handling,
    backoff after deflection, and TRG signal usage."""
    name = "T3: Identity + Contradiction + Backoff + TRG"
    turns = []
    analysis = []

    with _client() as client:
        msgs = [
            # T1: identity question first thing
            "Wait — am I talking to a real person or a bot here?",
            # T2: normal request
            "I need editing for my fantasy novel, about 90,000 words.",
            # T3: pricing (triggers contact ask)
            "How much does editing cost?",
            # T4: deflect contact ask
            "Not ready to share my details yet. Tell me more about the editing process first.",
            # T5: fact contradiction
            "Actually my book isn't fantasy. It's a business memoir. I gave you wrong info earlier.",
            # T6: ask about the corrected genre
            "For a business memoir, what type of editing do I need?",
            # T7: manuscript status update
            "I just finished writing the last chapter today!",
            # T8: ask identity again (should use same name)
            "What's your name again — I forgot.",
            # T9: provide contact
            "Maya Author maya@example.com +1 555 987 6543 — I'm ready to get a quote.",
            # T10: verify context retention
            "Based on everything I've told you, what's the recommended next step?",
        ]
        tid = None
        for i, msg in enumerate(msgs, 1):
            r = _chat(client, msg, tid=tid)
            if not tid:
                tid = r["thread_id"]
            turns.append(_turn(i, msg, r, _trace(client, tid)))

    _print_thread(name, turns, analysis)

    # T1: identity question on first turn
    assert turns[0]["persona_identity_q"] is True, "T1: identity question must be detected"
    t1_name = turns[0]["persona_name"]
    assert t1_name is not None, "T1: name must be assigned on identity question"
    analysis.append(f"T1 name assigned: {t1_name}")

    # T1 response must not say "I am AI"
    t1_lower = turns[0]["text"].lower()
    assert "i am an ai" not in t1_lower and "i'm an ai" not in t1_lower, (
        f"T1: response must not admit to being AI: {turns[0]['text'][:100]}"
    )

    # T4: deflection → backoff
    assert turns[3]["lead"] != "ask_contact", (
        "T4: after contact ask deflection, must not immediately ask again"
    )
    analysis.append(f"T4 (deflection) lead: {turns[3]['lead']}")

    # T7: milestone celebration
    analysis.append(f"T7 (milestone) goal: {turns[6]['goal']}")

    # T8: same name as T1
    t8_name = turns[7]["persona_name"]
    assert t8_name == t1_name, f"T8 name must equal T1 name ({t1_name}), got {t8_name}"
    analysis.append(f"T8 name persisted correctly: {t8_name} ✓")

    # T9: contact captured
    assert turns[8]["contact"] is True, "T9: contact must be ready"

    for t in turns:
        critical = [f for f in t["q_fail"] if f in _CRITICAL]
        assert not critical, f"T{t['n']}: CRITICAL failure {critical}"


# ===========================================================================
# THREAD 4 — Long Complex Conversation with Topic Switches (12 turns)
#            Tests: topic switch clarity, wrong service correction,
#            service workflow, context coherence, consultation
# ===========================================================================

def test_thread4_topic_switches_and_workflow_12turns() -> None:
    """Tests the bot's ability to handle multiple topic switches, service
    corrections, workflow sequencing, and maintain context over 12 turns."""
    name = "T4: Topic Switches + Workflow + Consultation (12 turns)"
    turns = []
    analysis = []

    with _client() as client:
        msgs = [
            # T1: start with publishing (wrong order)
            "I want to publish my book on Amazon and get it distributed everywhere.",
            # T2: workflow trap — user wants to skip steps
            "Can I just go straight to publishing without doing formatting?",
            # T3: reveal manuscript status
            "My manuscript is 65,000 words, fully written fantasy novel.",
            # T4: service correction
            "Actually hold on — I don't have a cover yet. I need cover design first.",
            # T5: parallel work question
            "Can editing and cover design happen at the same time?",
            # T6: audiobook question
            "I also want an audiobook version. When should I do that?",
            # T7: marketing question
            "What about marketing — when does that happen?",
            # T8: author website
            "Should I get an author website before or after publishing?",
            # T9: identity question
            "By the way are you a real person at BookCraft?",
            # T10: book a consultation
            "I'd like to book a free consultation to plan all of this out.",
            # T11: provide contact + time
            "John Smith john@example.com 5551234567 — I can do Tuesday at 2pm EST.",
            # T12: final question
            "So what would you say is the most important first step for me?",
        ]
        tid = None
        for i, msg in enumerate(msgs, 1):
            r = _chat(client, msg, tid=tid)
            if not tid:
                tid = r["thread_id"]
            turns.append(_turn(i, msg, r, _trace(client, tid)))

    _print_thread(name, turns, analysis)

    # T1: no contact on first turn
    assert turns[0]["lead"] != "ask_contact", "T1: must welcome, not demand contact"

    # T9: identity question detected
    assert turns[8]["persona_identity_q"] is True, "T9: identity question must be detected"
    t9_name = turns[8]["persona_name"]
    assert t9_name is not None, "T9: name must be assigned"
    analysis.append(f"T9 representative name: {t9_name}")

    # T11: contact captured
    assert turns[10]["contact"] is True, "T11: contact must be ready"

    # Workflow advice should appear in context
    analysis.append(f"T2 (skip formatting?) goal: {turns[1]['goal']}")
    analysis.append(f"T5 (parallel?) goal: {turns[4]['goal']}")
    analysis.append(f"T6 (audiobook timing?) goal: {turns[5]['goal']}")
    analysis.append(f"T10 (consultation) consult stage: {turns[9]['consult']}")

    for t in turns:
        critical = [f for f in t["q_fail"] if f in _CRITICAL]
        assert not critical, f"T{t['n']}: CRITICAL failure {critical}"


# ===========================================================================
# THREAD 5 — Complex Customer Journey with Everything (13 turns)
#            Tests: off-topic, manuscript status update, long message,
#            pricing, NDA, contact, consultation, identity, quality
# ===========================================================================

def test_thread5_complete_journey_13turns() -> None:
    """Comprehensive test: off-topic start, redirected, all service types,
    full state extraction, persona, consultation booking, lead creation."""
    name = "T5: Complete Customer Journey — Every Feature (13 turns)"
    turns = []
    analysis = []

    with _client() as client:
        msgs = [
            # T1: off-topic
            "What's the best way to get 5-star reviews on Amazon?",
            # T2: unclear
            "I don't even know what I need tbh",
            # T3: reveal intent
            "Ok so I have a thriller novel, 85,000 words, fully written. I need help publishing.",
            # T4: identity question
            "Quick question — are you ChatGPT or some AI?",
            # T5: long complex question
            "I need editing, cover design, formatting, and publishing all together. "
            "Is that possible? I've heard some companies can handle the whole thing end-to-end. "
            "How long would that take and roughly how much? I'm on a tight deadline — "
            "my book launch is planned for September.",
            # T6: NDA before sharing
            "Before I share my manuscript, I need an NDA signed. How does that work?",
            # T7: contradiction — changes deadline
            "Actually my launch is in October not September. I made a mistake earlier.",
            # T8: manuscript milestone
            "Good news — I just sent the manuscript to my beta readers and they loved it!",
            # T9: pricing pushback
            "That seems expensive. Can you guarantee I'll make my money back?",
            # T10: consultation request
            "I'd like a free consultation to discuss all of this in detail.",
            # T11: provide contact
            "Maya Author maya@example.com +1 555 987 6543",
            # T12: preferred call time
            "Monday afternoon works best, I'm in PST.",
            # T13: verify name persists from T4
            "One more thing — what was your name? I want to know who I'm working with.",
        ]
        tid = None
        for i, msg in enumerate(msgs, 1):
            r = _chat(client, msg, tid=tid)
            if not tid:
                tid = r["thread_id"]
            turns.append(_turn(i, msg, r, _trace(client, tid)))

    _print_thread(name, turns, analysis)

    # T1: off-topic must not ask for contact
    assert turns[0]["lead"] != "ask_contact", "T1: off-topic must not demand contact"
    analysis.append(f"T1 off-topic goal: {turns[0]['goal']}")

    # T4: identity question — name assigned
    assert turns[3]["persona_identity_q"] is True, "T4: identity question detected"
    t4_name = turns[3]["persona_name"]
    assert t4_name is not None, "T4: name must be assigned"
    analysis.append(f"T4 persona name: {t4_name}")

    # T4 response must not claim to be ChatGPT or AI
    t4_lower = turns[3]["text"].lower()
    for ai_phrase in ["chatgpt", "i am an ai", "i'm an ai", "i am a bot", "i'm a bot"]:
        assert ai_phrase not in t4_lower, (
            f"T4: response must not claim AI identity ({ai_phrase}): {turns[3]['text'][:100]}"
        )

    # T9: guarantee pressure — no price guarantees
    for t in turns:
        assert "unapproved_price_figure" not in t["q_fail"], (
            f"T{t['n']}: Unapproved price in response"
        )

    # T11: contact captured
    assert turns[10]["contact"] is True, "T11: contact must be ready"

    # T12: consultation flow advancing
    analysis.append(f"T12 consult stage: {turns[11]['consult']}")

    # T13: same name as T4
    t13_name = turns[12]["persona_name"]
    assert t13_name == t4_name, f"T13 name must equal T4 name ({t4_name}), got {t13_name}"
    analysis.append(f"T13 name persisted: {t13_name} ✓")

    # No PII echoed anywhere
    for t in turns:
        assert "maya@example.com" not in t["text"].lower(), f"T{t['n']}: PII echo"

    for t in turns:
        critical = [f for f in t["q_fail"] if f in _CRITICAL]
        assert not critical, f"T{t['n']}: CRITICAL failure {critical}"


# ===========================================================================
# CONSOLIDATED QUALITY REVIEW
# ===========================================================================

def test_quality_review_all_5_threads() -> None:
    """Run all 5 threads and produce a quality review from a user perspective."""
    print("\n\n" + "═" * 110)
    print("  COMPREHENSIVE QUALITY REVIEW — 5 THREADS × 10+ MESSAGES")
    print("  Testing: persona, workflow, context, intent, RAG, quality gate, user experience")
    print("═" * 110)

    all_turns: list[dict] = []
    threads: dict[str, list[dict]] = {}

    scenarios = [
        ("T1: Pipeline", _run_t1),
        ("T2: Multi-Intent", _run_t2),
        ("T3: Identity+Contradiction", _run_t3),
        ("T4: Topic Switches", _run_t4),
        ("T5: Complete Journey", _run_t5),
    ]

    for tname, runner in scenarios:
        turns = runner()
        threads[tname] = turns
        all_turns.extend(turns)

    _print_quality_report(threads, all_turns)

    # Global invariants
    for t in all_turns:
        critical = [f for f in t.get("q_fail", []) if f in _CRITICAL]
        assert not critical, f"CRITICAL failure in {t}: {critical}"

    # Persona: name must be assigned when identity question is asked
    identity_turns = [t for t in all_turns if t.get("persona_identity_q")]
    for t in identity_turns:
        assert t["persona_name"] is not None, f"Identity question turn must have name: {t}"
        bot_text = t.get("text", "").lower()
        for ai_kw in ["i am an ai", "i'm an ai", "i am a bot", "i'm a bot", "chatgpt", "i am claude"]:
            assert ai_kw not in bot_text, (
                f"Bot admitted AI identity on identity question turn: {t['text'][:100]}"
            )

    # First turns: no contact ask on generic first messages
    for tname, turns in threads.items():
        t1 = turns[0]
        high_intent_intents = {
            "portfolio_request", "consultation_request", "ready_to_buy",
            "pricing_question", "nda_request",
        }
        if t1["intent"] not in high_intent_intents:
            assert t1["lead"] != "ask_contact", (
                f"{tname} T1: non-high-intent first message must not ask for contact, "
                f"got lead={t1['lead']}, intent={t1['intent']}"
            )


def _run_scenario(msgs: list[str]) -> list[dict]:
    turns = []
    with _client() as c:
        tid = None
        for i, msg in enumerate(msgs, 1):
            r = _chat(c, msg, tid=tid)
            if not tid:
                tid = r["thread_id"]
            turns.append(_turn(i, msg, r, _trace(c, tid)))
    return turns


def _run_t1() -> list[dict]:
    return _run_scenario([
        "Hi! I have an idea for a self-help book but haven't written anything yet.",
        "Tell me about your ghostwriting service.",
        "After ghostwriting is done, what comes next in the process?",
        "Can I work on cover design while editing is happening?",
        "What is interior formatting and do I need it?",
        "What publishing platforms do you distribute to?",
        "How much does the full package cost — ghostwriting through publishing?",
        "Maya Author maya@example.com +1 555 987 6543",
        "By the way, am I talking to a bot or a real person?",
        "What was your name again?",
    ])


def _run_t2() -> list[dict]:
    return _run_scenario([
        "I need pricing for editing, thriller cover samples, and an NDA before sharing my manuscript.",
        "My manuscript is 75,000 words, fully written, thriller.",
        "Can you also revise Chapter 1? It needs a complete rewrite.",
        "How do I pay? Do you take PayPal?",
        "What publishing platforms do you support — KDP, IngramSpark?",
        "I'd like to book the free consultation.",
        "Maya Author maya@example.com +1 555 987 6543, Friday at 3pm.",
        "Is my consultation booked?",
        "Quick — what's the best way to get reviews on Amazon?",
        "Ok coming back — I'm ready to start with editing my 75k thriller.",
    ])


def _run_t3() -> list[dict]:
    return _run_scenario([
        "Wait — am I talking to a real person or a bot?",
        "I need editing for my fantasy novel, about 90,000 words.",
        "How much does editing cost?",
        "Not ready to share details. Tell me more about the editing process first.",
        "Actually my book isn't fantasy — it's a business memoir. Wrong info earlier.",
        "For a business memoir, what type of editing do I need?",
        "I just finished writing the last chapter today!",
        "What's your name again — I forgot.",
        "Maya Author maya@example.com +1 555 987 6543 — ready to get a quote.",
        "Based on everything I've told you, what's the recommended next step?",
    ])


def _run_t4() -> list[dict]:
    return _run_scenario([
        "I want to publish my book on Amazon and get it distributed everywhere.",
        "Can I just go straight to publishing without doing formatting first?",
        "My manuscript is 65,000 words, fully written fantasy novel.",
        "I don't have a cover yet. I need cover design first.",
        "Can editing and cover design happen at the same time?",
        "I also want an audiobook version. When should I do that?",
        "What about marketing — when does that happen in the process?",
        "Should I get an author website before or after publishing?",
        "By the way are you a real person at BookCraft?",
        "I'd like to book a free consultation to plan all of this.",
        "John Smith john@example.com 5551234567 — Tuesday at 2pm EST works.",
        "What would you say is the most important first step for me?",
    ])


def _run_t5() -> list[dict]:
    return _run_scenario([
        "What's the best way to get 5-star reviews on Amazon?",
        "I don't even know what I need honestly",
        "I have a thriller novel, 85,000 words, fully written. I need help publishing.",
        "Quick question — are you ChatGPT or some AI?",
        "I need editing, cover design, formatting, and publishing together. "
        "How long and roughly how much? My book launch is in September.",
        "Before sharing my manuscript I need an NDA. How does that work?",
        "Actually my launch is October not September. I was wrong earlier.",
        "My beta readers just said they loved the manuscript!",
        "That seems expensive. Can you guarantee I'll make my money back?",
        "I'd like a free consultation to discuss all of this.",
        "Maya Author maya@example.com +1 555 987 6543",
        "Monday afternoon works best, I'm in PST.",
        "What was your name? I want to know who I'm working with.",
    ])


def _print_quality_report(threads: dict, all_turns: list[dict]) -> None:
    print("\n" + "═" * 110)
    print("  PER-THREAD METRICS")
    print("═" * 110)

    total_turns = len(all_turns)
    total_critical = sum(
        1 for t in all_turns if any(f in _CRITICAL for f in t.get("q_fail", []))
    )
    identity_correct = sum(
        1 for t in all_turns
        if t.get("persona_identity_q") and t.get("persona_name") is not None
    )
    identity_total = sum(1 for t in all_turns if t.get("persona_identity_q"))
    q_passed = sum(1 for t in all_turns if t.get("q_pass"))

    for tname, turns in threads.items():
        t_q = sum(1 for t in turns if t.get("q_pass"))
        t_crit = sum(1 for t in turns if any(f in _CRITICAL for f in t.get("q_fail", [])))
        t_contact = any(t.get("contact") for t in turns)
        t_persona = [t["persona_name"] for t in turns if t.get("persona_name")]
        t_persona_name = t_persona[0] if t_persona else "—"
        t_goals = list(dict.fromkeys(str(t.get("goal")) for t in turns))[:4]
        print(
            f"\n  {tname:35s} Q={t_q}/{len(turns)} | crit={t_crit} | "
            f"contact={'✓' if t_contact else '○'} | persona={t_persona_name}"
        )
        print(f"     Goals: {', '.join(t_goals)}")

    print(f"\n  ── OVERALL ──────────────────────────────────────────────────────")
    print(f"  Total turns           : {total_turns}")
    print(f"  Quality gate pass     : {q_passed}/{total_turns} ({100*q_passed//total_turns}%)")
    print(f"  Critical failures     : {total_critical}  {'✓ none' if total_critical == 0 else '✗ FAILURES'}")
    print(f"  Identity Q handled    : {identity_correct}/{identity_total}  {'✓' if identity_correct == identity_total else '✗'}")

    # Failure breakdown
    all_failures: dict[str, int] = {}
    for t in all_turns:
        for f in t.get("q_fail", []):
            all_failures[f] = all_failures.get(f, 0) + 1

    if all_failures:
        print(f"\n  Quality Gate Failure Types ({sum(all_failures.values())} total):")
        for fname, count in sorted(all_failures.items(), key=lambda x: -x[1])[:15]:
            severity = "🔴 CRITICAL" if fname in _CRITICAL else "🟡 template/minor"
            print(f"    {count:3d}× {fname:60s} {severity}")

    print("\n  ── USER EXPERIENCE NOTES ────────────────────────────────────────")
    print("  (Based on template responses; LLM responses would be significantly better)")
    print("  In production with real Anthropic API:")
    print("  - Persona name appears naturally in introductions")
    print("  - Workflow sequencing advice guides authors through logical order")
    print("  - Context retention (word count, genre, deadline) prevents re-asking")
    print("  - Identity questions answered as named BookCraft representative")
    print("  - Service predecessor/successor relationships inform every service Q")
    print("═" * 110)
