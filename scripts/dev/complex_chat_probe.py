from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from uuid import UUID

import httpx


@dataclass(frozen=True, slots=True)
class ProbeTurn:
    name: str
    message: str
    must_contain_any: tuple[str, ...] = ()
    must_not_contain: tuple[str, ...] = ("Obligations of Confidentiality", "<%", "```json")
    no_currency: bool = False


PROBE_TURNS = [
    ProbeTurn(
        name="multi_service_scope",
        message=(
            "Hi, I'm comparing ghostwriting, developmental editing, and cover design for a "
            "76,000 word fantasy manuscript. I have a partial draft, I might need an NDA, "
            "and my email is avery.author@example.com. What should we do first?"
        ),
        must_contain_any=("BookCraft", "ghostwriting", "editing", "cover"),
    ),
    ProbeTurn(
        name="pricing_timeline_gate",
        message=(
            "Assume the book is fantasy, about 76000 words, and I want ghostwriting plus "
            "editing. Can you give a cost, discount, and timeline now?"
        ),
        must_contain_any=("deterministic", "approved", "won't guess", "scope"),
        no_currency=True,
    ),
    ProbeTurn(
        name="portfolio_with_confidentiality",
        message=(
            "Show me ghostwriting samples first, then if those are confidential show cover "
            "design examples that fit fantasy."
        ),
        must_contain_any=("confidential", "registry", "sample"),
    ),
    ProbeTurn(
        name="legal_template_gate",
        message=(
            "Please draft the NDA clauses and service agreement terms for me using the "
            "information above."
        ),
        must_contain_any=("approved template", "deterministic quote", "document queue"),
        no_currency=True,
    ),
    ProbeTurn(
        name="contact_and_clarification",
        message=(
            "My phone is +1 555 010 7788. I am not ready to sign today, but I want to know "
            "what you still need from me before a formal quote."
        ),
        must_contain_any=("word", "pages", "service", "manuscript", "BookCraft"),
        no_currency=True,
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run complex chat probes against BookCraft API.")
    parser.add_argument(
        "--base-url",
        default=os.getenv("BOOKCRAFT_CHAT_BASE_URL", "http://localhost:8000"),
        help="BookCraft API base URL.",
    )
    parser.add_argument("--json", action="store_true", help="Print full JSON responses.")
    args = parser.parse_args()

    thread_id: UUID | None = None
    summaries: list[dict[str, object]] = []
    with httpx.Client(timeout=20.0) as client:
        for turn in PROBE_TURNS:
            payload: dict[str, object] = {"message": turn.message}
            if thread_id is not None:
                payload["thread_id"] = str(thread_id)
            response = client.post(f"{args.base_url.rstrip('/')}/api/v1/chat/turn", json=payload)
            response.raise_for_status()
            body = response.json()
            thread_id = UUID(body["thread_id"])
            text = _response_text(body)
            _validate_turn(turn, text)
            summary = {
                "turn": turn.name,
                "intent": body["intent"]["query_primary"] if body.get("intent") else None,
                "service": body["intent"]["service_primary"] if body.get("intent") else None,
                "bubble_count": len(body["bubbles"]),
                "preview": text[:240],
            }
            summaries.append(summary)
            if args.json:
                print(json.dumps(body, indent=2))
            else:
                print(json.dumps(summary, indent=2))
    print(json.dumps({"status": "passed", "thread_id": str(thread_id), "turns": len(summaries)}))
    return 0


def _response_text(body: dict[str, object]) -> str:
    bubbles = body.get("bubbles")
    if not isinstance(bubbles, list):
        return ""
    return " ".join(bubble.get("text", "") for bubble in bubbles if isinstance(bubble, dict))


def _validate_turn(turn: ProbeTurn, text: str) -> None:
    lowered = text.lower()
    if turn.must_contain_any and not any(item.lower() in lowered for item in turn.must_contain_any):
        _fail(turn, f"expected one of {turn.must_contain_any!r} in response: {text}")
    for forbidden in turn.must_not_contain:
        if forbidden.lower() in lowered:
            _fail(turn, f"forbidden text {forbidden!r} found in response: {text}")
    if turn.no_currency and any(marker in text for marker in ["$", "USD ", " usd"]):
        _fail(turn, f"currency leaked in response: {text}")


def _fail(turn: ProbeTurn, message: str) -> None:
    print(f"{turn.name}: {message}", file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    raise SystemExit(main())
