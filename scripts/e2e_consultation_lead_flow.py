#!/usr/bin/env python
"""Local end-to-end test of the consultation + lead + confirmation flow.

Drives the REAL ChatService.handle_turn pipeline against a temp SQLite DB (so the
lead and consultation services actually persist), captures the action_events the bot
emits to CSR Node, and captures the direct CSR Node /api/consultations POST payload.
No production systems are touched; the CSR Node call is intercepted, not sent.

Validates (multiple scenarios):
  S1  lead creation (single-message contact) → sales_leads row + lead_created event
  S2  full consultation booking WITH confirmation ("yes") → sales_consultations row,
      consultation_scheduled event, and the CSR Node POST payload
  S3  cross-turn contact (email turn 1, name later) survives persistence → lead ready
      (validates the contact-preservation fix)
  S4  bad-data guards: "(1770-1810)" not a phone, "EST" not a name (extraction fixes)

Run:  cd ai_chatbot && .venv/bin/python scripts/e2e_consultation_lead_flow.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
os.chdir(_ROOT)
for _n in ("httpx", "httpcore", "opentelemetry", "bookcraft", "uvicorn", "sqlalchemy", "asyncio"):
    logging.getLogger(_n).setLevel(logging.ERROR)
logging.basicConfig(level=logging.ERROR)

from sqlmodel import select  # noqa: E402

from bookcraft.api.main import build_chat_service, build_trg_engine  # noqa: E402
from bookcraft.components.consultations.service import ConsultationActionService  # noqa: E402
from bookcraft.components.response.chat_schemas import ChatTurnRequest  # noqa: E402
from bookcraft.components.response.schemas import ResponseDraft  # noqa: E402
from bookcraft.components.storage.db import (  # noqa: E402
    create_all,
    create_engine,
    create_session_factory,
)
from bookcraft.components.storage.models import (  # noqa: E402
    SalesConsultationRecord,
    SalesLeadRecord,
)
from bookcraft.components.storage.thread_repository import ThreadRepository  # noqa: E402
from bookcraft.infra.config import Settings  # noqa: E402

# ── Capture the direct CSR Node POST (intercept _push_to_csr_api) ────────────
CSR_NODE_POSTS: list[dict] = []


async def _capture_csr_push(self, *, request, result) -> None:  # noqa: ANN001
    CSR_NODE_POSTS.append(
        {
            "url": f"{(self.csr_node_api_url or '').rstrip('/')}/api/consultations",
            "name": request.name,
            "phone": request.phone,
            "email": request.email,
            "customerTimezone": request.customer_timezone,
            "startsAtUtc": result.starts_at_utc.isoformat(),
            "endsAtUtc": result.ends_at_utc.isoformat(),
            "externalAppointmentId": str(result.appointment_id),
            "csrName": result.csr_name,
            "csrId": result.csr_id,
            "source": "ai_chatbot",
        }
    )


ConsultationActionService._push_to_csr_api = _capture_csr_push  # type: ignore[method-assign]


class _FakeGenerator:
    """Deterministic stand-in for Sonnet — action flow is independent of bot text."""

    async def generate(self, **kwargs) -> ResponseDraft:  # noqa: ANN003
        return ResponseDraft(text="Thanks — happy to help with that.", source="claude_sonnet")

    async def repair(self, *, bad_text: str, **_kwargs) -> ResponseDraft:  # noqa: ANN003
        return ResponseDraft(text=bad_text, source="template_no_adapter")

    async def stream(self, **kwargs):  # noqa: ANN003
        yield "Thanks — happy to help with that."

    adapter = None


class _FakeLLMExtractor:
    """Mimics Sonnet extracting a sign-off name the deterministic capture misses.

    Production Sonnet pulls "Gonzalo Garcia" out of a long message that ends with a
    signature block; the deterministic bare-block extractor does not. This returns a
    personal.name delta for such messages so the issue-1 back-fill can be exercised.
    """

    async def extract(self, *, user_text: str, assistant_text: str, state):  # noqa: ANN001
        import re as _re

        from bookcraft.components.extraction.llm_extractor import LLMExtractionResult
        from bookcraft.components.extraction.schemas import StateDelta
        from bookcraft.domain.enums import Source

        deltas = []
        # "my name is X" (may contain digits the deterministic capture rejects, e.g.
        # "E2E Smoke Test"), or a capitalized "First Last" sign-off on its own line.
        m = _re.search(
            r"\bmy name is\s+([A-Za-z][A-Za-z0-9 ]*?)(?:\s+and\b|,|\.|\s+my\b|$)",
            user_text, _re.IGNORECASE,
        ) or _re.search(r"(?:^|\n)\s*([A-Z][a-z]+ [A-Z][a-z]+)\s*(?:\n|$)", user_text)
        if m:
            deltas.append(StateDelta(
                path="personal.name", value=m.group(1).strip(), confidence=0.92,
                source=Source.AI_EXTRACTED, extracted_by="fake_llm.v1", raw_excerpt=m.group(1).strip(),
            ))
        return LLMExtractionResult(state_deltas=deltas, rich_metadata={}, coreference_notes=[])


# ── Reporting ────────────────────────────────────────────────────────────────
RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((label, ok, detail))
    mark = "✓" if ok else "✗"
    print(f"   {mark} {label}{('  — ' + detail) if detail else ''}")


async def _leads(sf) -> list[SalesLeadRecord]:  # noqa: ANN001
    async with sf() as s:
        return list((await s.execute(select(SalesLeadRecord))).scalars().all())


async def _consultations(sf) -> list[SalesConsultationRecord]:  # noqa: ANN001
    async with sf() as s:
        return list((await s.execute(select(SalesConsultationRecord))).scalars().all())


async def main() -> int:
    tmp = tempfile.mkdtemp(prefix="e2e_consult_")
    db_url = f"sqlite+aiosqlite:///{tmp}/sales.db"
    settings = Settings(
        app_env="integration",
        api_auth_mode="off",
        llm_provider_mode="mock",
        database_url=db_url,
        csr_node_api_url="http://csr-node.local:5050",
        tei_url="http://127.0.0.1:9",  # unreachable → embedding degrades gracefully
        readiness_check_externals=False,
        rate_limit_per_ip_per_minute=10_000_000,
        log_level="ERROR",
    )

    engine = create_engine(settings, database_url=db_url)
    await create_all(engine)
    sf = create_session_factory(engine)

    chat = build_chat_service(
        settings,
        thread_repository=ThreadRepository(session_factory=sf),
        session_factory=sf,
        trg_engine=build_trg_engine(settings),
        elasticsearch_client=None,
    )
    chat.response_generator = _FakeGenerator()  # type: ignore[assignment]
    chat.llm_metadata_extractor = _FakeLLMExtractor()  # type: ignore[assignment]

    async def turn(message: str, thread_id: str | None) -> dict:
        resp = await chat.handle_turn(
            ChatTurnRequest(message=message, thread_id=thread_id, customer_id=uuid4())
            if thread_id is None
            else ChatTurnRequest(message=message, thread_id=thread_id)
        )
        return {
            "thread_id": str(resp.thread_id),
            "action_events": list(resp.action_events or []),
            "bubbles": [b.text for b in resp.bubbles],
        }

    print("=" * 76)
    print(" E2E — Consultation + Lead + Confirmation flow (real DB, CSR Node captured)")
    print("=" * 76)

    # ── Scenario 1: lead creation (single-message contact, no confirmation) ──
    print("\n▶ S1: Lead creation (name + email in one message)")
    tid = None
    r = await turn("I need editing for my completed fantasy novel.", tid)
    tid = r["thread_id"]
    r = await turn("My name is Sarah Khan and my email is sarah@example.com", tid)
    events = r["action_events"]
    leads = await _leads(sf)
    lead = next((x for x in leads if (x.email or "") == "sarah@example.com"), None)
    check("lead_created event emitted", any(e.get("type") == "lead_created" for e in events),
          str([e.get("type") for e in events]))
    check("sales_leads row persisted", lead is not None)
    if lead:
        check("lead.name == 'Sarah Khan'", lead.name == "Sarah Khan", repr(lead.name))
        check("lead.email correct", lead.email == "sarah@example.com", repr(lead.email))

    # ── Scenario 2: full consultation booking WITH confirmation ──
    print("\n▶ S2: Consultation booking with confirmation ('yes')")
    r = await turn("I'd like to book a free consultation for ghostwriting.", None)
    tid2 = r["thread_id"]
    await turn("My name is Maya Author, maya@example.com, +1 555 987 6543.", tid2)
    rprop = await turn("Let's schedule the call for Friday afternoon, Eastern time.", tid2)
    # The booking proposal should now be pending confirmation.
    dbg_prop = await chat.get_thread_debug_state(tid2)
    check("consultation reaches pending confirmation",
          (dbg_prop.get("consultation") or {}).get("stage") == "pending_confirmation",
          (dbg_prop.get("consultation") or {}).get("stage"))
    rc = await turn("yes, go ahead and schedule it", tid2)
    events2 = rc["action_events"]
    consults = await _consultations(sf)
    consult = next((c for c in consults if (c.customer_email or "") == "maya@example.com"), None)
    check("consultation_scheduled event emitted",
          any(e.get("type") == "consultation_scheduled" for e in events2),
          str([e.get("type") for e in events2]))
    check("sales_consultations row persisted", consult is not None)
    if consult:
        check("consultation.customer_name == 'Maya Author'",
              consult.customer_name == "Maya Author", repr(consult.customer_name))
        check("consultation has CSR assigned", bool(consult.csr_name), repr(consult.csr_name))
        check("consultation.status == 'scheduled'", consult.status == "scheduled", repr(consult.status))
    csr_post = next((p for p in CSR_NODE_POSTS if (p.get("email") or "") == "maya@example.com"), None)
    check("CSR Node POST captured", csr_post is not None)
    if csr_post:
        check("CSR POST name == 'Maya Author'", csr_post["name"] == "Maya Author", repr(csr_post["name"]))
        check("CSR POST has startsAtUtc", bool(csr_post["startsAtUtc"]))
        check("CSR POST source == 'ai_chatbot'", csr_post["source"] == "ai_chatbot")

    # ── Scenario 3: cross-turn contact survives persistence (redaction fix) ──
    print("\n▶ S3: Cross-turn contact (email turn 1, name later) survives persistence")
    r = await turn("I want a consultation. My email is clifford@safarisolutions.com", None)
    tid3 = r["thread_id"]
    rn = await turn("My name is Ann Carter", tid3)
    leads3 = await _leads(sf)
    lead3 = next((x for x in leads3 if (x.email or "") == "clifford@safarisolutions.com"), None)
    check("lead assembled across turns (email survived)", lead3 is not None)
    if lead3:
        check("lead3.name == 'Ann Carter'", lead3.name == "Ann Carter", repr(lead3.name))
        check("lead3.email survived turn 1", lead3.email == "clifford@safarisolutions.com", repr(lead3.email))

    # ── Scenario 4: extraction guards (bad data never becomes contact) ──
    print("\n▶ S4: Extraction guards — period not phone, timezone not name")
    r = await turn(
        "I am looking for a ghost-writer for frontier days (1770-1810). children (6-12).", None
    )
    tid4 = r["thread_id"]
    dbg = await chat.get_thread_debug_state(tid4)
    phone4 = (dbg.get("personal") or {}).get("phone")
    name4 = (dbg.get("personal") or {}).get("name")
    check("(1770-1810) NOT captured as phone", phone4 is None, repr(phone4))
    check("'looking for' NOT captured as name", name4 is None, repr(name4))
    await turn("EST - clifford2@safarisolutions.com", tid4)
    dbg2 = await chat.get_thread_debug_state(tid4)
    name4b = (dbg2.get("personal") or {}).get("name")
    email4b = (dbg2.get("personal") or {}).get("email")
    nameval = name4b.get("value") if isinstance(name4b, dict) else name4b
    check("'EST' NOT captured as name", nameval != "EST", repr(nameval))
    check("email still captured from 'EST - email'",
          isinstance(email4b, dict) and "clifford2@safarisolutions.com" in str(email4b.get("value")),
          repr(email4b.get("value") if isinstance(email4b, dict) else None))

    # ── Scenario 5 (chat 6040): sign-off name (LLM-only) must reach lead + CSR ──
    print("\n▶ S5: Sign-off name in a long message reaches the lead (issue 1)")
    gonzalo = (
        "Hi, I have a manuscript ready for editing and cover design. This is a romance "
        "story of about 110 pages. Could you please provide pricing details?\n"
        "Thank you,\nGonzalo Garcia\ngargonz@gmail.com"
    )
    rg = await turn(gonzalo, None)  # SINGLE turn — lead must be created same-turn
    tidg = rg["thread_id"]
    leads5 = await _leads(sf)
    lead5 = next((x for x in leads5 if (x.email or "") == "gargonz@gmail.com"), None)
    check("lead created from sign-off message", lead5 is not None)
    if lead5:
        check("lead5.name == 'Gonzalo Garcia' (LLM name reached the lead)",
              lead5.name == "Gonzalo Garcia", repr(lead5.name))
    dbgg = await chat.get_thread_debug_state(tidg)
    ci_name = ((dbgg.get("personal") or {}).get("name") or {})
    check("personal.name shows in AI state",
          isinstance(ci_name, dict) and ci_name.get("value") == "Gonzalo Garcia",
          repr(ci_name.get("value") if isinstance(ci_name, dict) else None))

    # ── Scenario 6 (chat 6040): bot asks for phone even with email (issue 3) ──
    print("\n▶ S6: Bot asks for phone before booking even when email is given (issue 3)")
    r = await turn("I'd like a free consultation for editing.", None)
    tid6 = r["thread_id"]
    # Digit-bearing name only the LLM catches (deterministic capture rejects "E2E"),
    # email but NO phone — contact must go ready SAME turn and the phone ask must fire.
    await turn("My name is E2E Smoke Test and my email is carlos@example.com. Let's do this via email.", tid6)
    dbg6 = await chat.get_thread_debug_state(tid6)
    stage6 = (dbg6.get("consultation") or {}).get("stage")
    check("bot asks for phone even with email (stage=requested_phone_needed)",
          stage6 == "requested_phone_needed", repr(stage6))
    # After asking once, providing only a time should proceed (loop-safe, not stuck on phone).
    await turn("Any weekday afternoon works.", tid6)
    dbg6b = await chat.get_thread_debug_state(tid6)
    check("phone ask is loop-safe (proceeds after asking once)",
          (dbg6b.get("consultation") or {}).get("stage") != "requested_phone_needed",
          (dbg6b.get("consultation") or {}).get("stage"))

    await engine.dispose()

    # ── Summary ──
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print("\n" + "=" * 76)
    print(f" RESULT: {passed}/{total} checks passed")
    print(f" Leads persisted: {len(await _leads(sf)) if False else len(leads3)} | "
          f"Consultations: {len(consults)} | CSR Node POSTs captured: {len(CSR_NODE_POSTS)}")
    print("=" * 76)
    if passed < total:
        print(" FAILURES:")
        for label, ok, detail in RESULTS:
            if not ok:
                print(f"   ✗ {label}  {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
