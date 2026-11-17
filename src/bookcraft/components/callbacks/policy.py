"""When to offer a call to a mobile visitor (rule set M-06 and M-10).

Deterministic. The policy answers one question after every bot reply to a mobile
visitor: should this reply carry the call offer? It never decides the wording
(``flow.py``) and never limits learning: extraction runs on every message
whether or not an offer is made.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from bookcraft.components.callbacks.hours import CallbackHours
from bookcraft.components.callbacks.schemas import CallbackFlowState

OfferAction = Literal["none", "offer_now", "offer_schedule"]

# "How do I get started?" and its relatives (M-06).
_HOW_TO_START_RE = re.compile(
    r"\b(?:how\s+(?:do|can|would|should)\s+(?:i|we)\s+(?:get\s+)?(?:start(?:ed)?|begin|sign\s*up|"
    r"proceed|move\s+forward)|what(?:'s|\s+is)\s+the\s+next\s+step|"
    r"what\s+are\s+the\s+next\s+steps|ready\s+to\s+(?:start|begin|get\s+started)|"
    r"(?:let'?s|i\s+want\s+to|i'?d\s+like\s+to)\s+(?:get\s+started|start|begin))\b",
    re.IGNORECASE,
)
# "Can I speak to someone?" (M-06).
_SPEAK_TO_SOMEONE_RE = re.compile(
    r"\b(?:(?:speak|talk|chat)\s+(?:to|with)\s+(?:(?:a|an|the|your|real|live|actual)\s+)*"
    r"(?:person|human|someone|somebody|agent|representative|rep|specialist|consultant|"
    r"team|expert|advisor)|call\s+me|give\s+me\s+a\s+call|phone\s+me|"
    r"can\s+(?:you|someone)\s+call|have\s+someone\s+call)\b",
    re.IGNORECASE,
)
# "I'm only researching" (M-10).
_RESEARCHING_RE = re.compile(
    r"\b(?:just|only|still)\s+(?:researching|browsing|looking(?:\s+around)?|exploring|"
    r"comparing|shopping\s+around|gathering\s+info(?:rmation)?|curious)\b"
    r"|\bnot\s+ready\s+(?:yet|to\s+(?:commit|buy|start|decide))\b"
    r"|\bjust\s+(?:want(?:ed)?\s+(?:some\s+)?info(?:rmation)?|checking)\b",
    re.IGNORECASE,
)


class CallOfferDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: OfferAction = "none"
    # Why an offer is due (first matching reason).
    trigger: str | None = None
    # Why a due offer was held back, or why nothing was considered.
    hold_reason: str | None = None
    buying_signal: bool = False
    audit: list[str] = Field(default_factory=list)

    @property
    def offer(self) -> bool:
        return self.action != "none"


@dataclass(slots=True)
class CallOfferPolicy:
    hours: CallbackHours
    # MOBILE_CALL_OFFER_BY: the latest bot reply (this visit) at which the offer is made.
    offer_by_reply: int = 6
    # MAX_CALL_OFFERS per visit.
    max_offers: int = 2
    # Decision 7 in the rule set: call-now is mobile only. Set True to extend it.
    desktop_enabled: bool = False

    def applies(self, device: str | None) -> bool:
        return device == "mobile" or (self.desktop_enabled and device == "desktop")

    @staticmethod
    def said_researching(message: str) -> bool:
        return bool(_RESEARCHING_RE.search(message or ""))

    @staticmethod
    def buying_signal(
        *,
        message: str,
        asked_price: bool,
        asked_for_person: bool,
        ready_to_buy: bool,
    ) -> str | None:
        """Return the buying signal in this message, if any."""
        if asked_for_person or _SPEAK_TO_SOMEONE_RE.search(message or ""):
            return "asked_for_person"
        if _HOW_TO_START_RE.search(message or ""):
            return "asked_how_to_start"
        if asked_price:
            return "asked_price"
        if ready_to_buy:
            return "ready_to_buy"
        return None

    def decide(
        self,
        *,
        device: str | None,
        flow: CallbackFlowState,
        message: str,
        reply_index: int,
        service_known: bool,
        stage_known: bool,
        asked_price: bool = False,
        asked_for_person: bool = False,
        ready_to_buy: bool = False,
        complaint: bool = False,
        needs_clarification: bool = False,
        call_opt_out: bool = False,
        deferred: bool = False,
        consultation_booked: bool = False,
        confirmation_pending: bool = False,
        csr_active: bool = False,
        now_utc: datetime | None = None,
    ) -> CallOfferDecision:
        """Decide whether the reply numbered ``reply_index`` (1-based, this visit) offers a call."""
        audit: list[str] = []
        if not self.applies(device):
            return CallOfferDecision(hold_reason="device_not_eligible", audit=["skip:device"])

        signal = self.buying_signal(
            message=message,
            asked_price=asked_price,
            asked_for_person=asked_for_person,
            ready_to_buy=ready_to_buy,
        )

        # --- What makes an offer due (M-06) -----------------------------------
        trigger: str | None = None
        if signal is not None:
            trigger = signal
        elif service_known and stage_known:
            trigger = "service_and_stage_known"
        elif reply_index >= self.offer_by_reply:
            trigger = "reply_limit_reached"
        if trigger is None:
            return CallOfferDecision(hold_reason="not_due", audit=["skip:not_due"])
        audit.append(f"due:{trigger}")

        # --- What holds a due offer back (M-10 wins over M-06) ------------------
        def held(reason: str) -> CallOfferDecision:
            audit.append(f"hold:{reason}")
            return CallOfferDecision(
                trigger=trigger, hold_reason=reason, buying_signal=signal is not None, audit=audit
            )

        if csr_active:
            return held("csr_active")
        if flow.requested_this_visit:
            return held("callback_already_requested")
        if consultation_booked:
            return held("consultation_already_booked")
        if flow.pending is not None:
            return held("offer_already_pending")
        if confirmation_pending:
            return held("other_confirmation_pending")
        if flow.declined_this_visit or call_opt_out:
            return held("prefers_chat_or_email")
        if deferred:
            return held("postponed")
        if complaint:
            return held("complaint_or_objection")
        if needs_clarification:
            # An unanswered question comes first: answer it, then offer on a later reply.
            return held("clarification_first")
        if flow.researching and signal is None:
            return held("only_researching")
        if flow.offers_this_visit >= self.max_offers:
            return held("offer_cap_reached")
        if flow.offers_this_visit >= 1 and signal is None:
            # The second offer is made only after a new sign of buying intent.
            return held("second_offer_needs_buying_signal")

        action: OfferAction = (
            "offer_now" if self.hours.call_now_available(now_utc) else "offer_schedule"
        )
        audit.append(f"offer:{action}")
        return CallOfferDecision(
            action=action, trigger=trigger, buying_signal=signal is not None, audit=audit
        )
