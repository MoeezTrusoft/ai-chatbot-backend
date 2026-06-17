#!/usr/bin/env python3
"""Production-safe consultation + lead + confirmation smoke test.

RUN THIS ON THE SERVER (tacticalrmm), where the bot is on 127.0.0.1:8001 and the
CSR Node app is on localhost:5050. It drives the LIVE chatbot with a clearly
labelled TEST customer, exercises:
    • lead creation (name + email)
    • the always-ask-for-phone behaviour
    • consultation booking WITH confirmation ("yes")
…and verifies the bot's action_events + AI state, then prints exactly which test
records to delete. Stdlib only (urllib) — no pip installs required.

  python3 scripts/prod_consultation_smoke.py
  python3 scripts/prod_consultation_smoke.py --bot-url http://127.0.0.1:8001 --csr-url http://localhost:5050

⚠️  This WRITES real rows (a test lead + consultation) and triggers a real CSR Node
    /api/consultations POST. It uses obvious TEST data (email e2e-smoke+…@trusoft.pk,
    name "E2E Smoke Test") and prints the thread_id / appointment_id / email so you
    can delete them. Pass --bearer if the bot has JWT auth enabled.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

RESULTS: list[tuple[str, bool, str]] = []
CLEANUP: dict[str, object] = {}


def _post(url: str, payload: dict, bearer: str | None) -> tuple[int, dict]:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if bearer:
        req.add_header("Authorization", f"Bearer {bearer}")
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {"_error": e.read().decode()[:300]}
    except Exception as e:  # noqa: BLE001
        return 0, {"_error": str(e)}


def _get(url: str, bearer: str | None) -> tuple[int, dict]:
    req = urllib.request.Request(url, method="GET")
    if bearer:
        req.add_header("Authorization", f"Bearer {bearer}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {"_error": e.read().decode()[:300]}
    except Exception as e:  # noqa: BLE001
        return 0, {"_error": str(e)}


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((label, ok, detail))
    print(f"   {'✓' if ok else '✗'} {label}{('  — ' + detail) if detail else ''}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Production consultation/lead smoke test")
    ap.add_argument("--bot-url", default="http://127.0.0.1:8001")
    ap.add_argument("--csr-url", default="http://localhost:5050")
    ap.add_argument("--bearer", default=None, help="JWT if the bot has api_auth_mode=jwt")
    args = ap.parse_args()

    turn_url = f"{args.bot_url.rstrip('/')}/api/v1/chat/turn"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    test_email = f"e2e-smoke+{stamp}@trusoft.pk"
    test_name = "E2E Smoke Test"

    def turn(message: str, thread_id: str | None) -> dict:
        payload = {"message": message}
        if thread_id:
            payload["thread_id"] = thread_id
        status, body = _post(turn_url, payload, args.bearer)
        if status != 200:
            print(f"   [turn HTTP {status}] {body.get('_error','')}", flush=True)
        return body if status == 200 else {}

    print("=" * 74)
    print(" PRODUCTION consultation + lead + confirmation smoke test")
    print(f" bot={args.bot_url}  csr={args.csr_url}  test_email={test_email}")
    print("=" * 74)

    # ── Reachability ──
    s, _ = _get(f"{args.bot_url.rstrip('/')}/api/v1/chat/debug/state/00000000-0000-0000-0000-000000000000", args.bearer)
    check("bot reachable", s in (200, 404, 422), f"HTTP {s}")
    if s == 0:
        print("\nABORT: cannot reach the bot. Run this ON the server, or pass --bot-url.")
        return 2

    # ── Consultation booking WITH always-ask-phone + confirmation ──
    print("\n▶ Consultation booking (phone ask → confirm)")
    r = turn(f"Hi, I'd like to book a free consultation for ghostwriting. [{test_name}]", None)
    tid = r.get("thread_id")
    CLEANUP["thread_id"] = tid
    check("turn 1 returned a thread_id", bool(tid), str(tid))
    if not tid:
        return 2

    # Email only (no phone yet) → the bot should ask for a phone number.
    turn(f"My name is {test_name} and my email is {test_email}.", tid)
    _, dbg = _get(f"{args.bot_url.rstrip('/')}/api/v1/chat/debug/state/{tid}", args.bearer)
    stage = (dbg.get("consultation") or {}).get("stage")
    name_in_state = ((dbg.get("personal") or {}).get("name") or {})
    check("bot asks for phone even with email (issue 3)",
          stage == "requested_phone_needed", f"stage={stage}")
    check("name saved in AI state (issue 1)",
          isinstance(name_in_state, dict) and name_in_state.get("value") == test_name,
          str(name_in_state.get("value") if isinstance(name_in_state, dict) else None))

    # Provide a VALID 10-digit phone, then an explicit time + timezone, then confirm.
    turn("Sure, my phone is +1 202 555 0147.", tid)
    turn("Let's schedule the call for next Tuesday at 2 PM Eastern time.", tid)
    rc = turn("yes, go ahead and schedule it", tid)
    events = rc.get("action_events") or []
    sched = next((e for e in events if e.get("type") == "consultation_scheduled"), None)
    check("consultation_scheduled action_event emitted",
          sched is not None, str([e.get("type") for e in events]))
    if sched:
        CLEANUP["appointment_id"] = sched.get("appointment_id")
        check("consultation has CSR assigned", bool(sched.get("csr_name")), str(sched.get("csr_name")))
        check("consultation has start time", bool(sched.get("starts_at_utc")), str(sched.get("starts_at_utc")))

    # Final AI state should show the booking + contact.
    _, dbg2 = _get(f"{args.bot_url.rstrip('/')}/api/v1/chat/debug/state/{tid}", args.bearer)
    consult = dbg2.get("consultation") or {}
    check("AI state shows consultation handled",
          consult.get("stage") in ("scheduled", "pending_confirmation", "handoff_created"),
          str(consult.get("stage")))

    # ── CSR Node verification (best-effort; /api/consultations is POST-only) ──
    print("\n▶ CSR Node check (informational — not counted)")
    cs, cbody = _get(f"{args.csr_url.rstrip('/')}/api/consultations", args.bearer)
    if cs == 200:
        rows = cbody if isinstance(cbody, list) else (cbody.get("data") or cbody.get("consultations") or [])
        found = test_email in json.dumps(rows)
        print(f"   {'✓' if found else '·'} consultation visible in CSR Node list ({len(rows) if isinstance(rows, list) else '?'} rows)")
    else:
        print(f"   · /api/consultations is not GET-readable (HTTP {cs}) — confirm the test "
              f"booking on the CSR dashboard for {test_email}")

    # ── Summary + cleanup ──
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n" + "=" * 74)
    print(f" RESULT: {passed}/{len(RESULTS)} checks passed")
    print(" CLEANUP — delete these TEST records:")
    print(f"   • thread_id      : {CLEANUP.get('thread_id')}")
    print(f"   • appointment_id : {CLEANUP.get('appointment_id')}")
    print(f"   • test email     : {test_email}  (delete the lead + consultation rows)")
    print("   SQL (adjust table/column names to your schema):")
    print(f"     DELETE FROM sales_consultations WHERE customer_email = '{test_email}';")
    print(f"     DELETE FROM sales_leads         WHERE email = '{test_email}';")
    print("   …and remove the matching consultation from the CSR Node DB / dashboard.")
    print("=" * 74)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
