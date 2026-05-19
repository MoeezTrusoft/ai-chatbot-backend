from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from bookcraft.components.context.delegation import load_slot_statuses
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.domain.enums import QueryIntentType, ServiceCategory

# ---------------------------------------------------------------------------
# Portfolio-request cue detection
# ---------------------------------------------------------------------------

_PORTFOLIO_CUE_RE = re.compile(
    r"\b(?:samples?|portfolio|examples?|previous\s+work|show\s+me\s+designs?"
    r"|show\s+examples?|send\s+samples?|just\s+show\s+me|share\s+designs?)\b",
    re.IGNORECASE,
)

# Cues that indicate the user is insisting on samples without providing a filter.
_INSISTENCE_RE = re.compile(
    r"\b(?:i\s+don'?t\s+know|not\s+sure|no\s+idea|just\s+show|send\s+anything"
    r"|you\s+decide|whatever|any\s+samples?|don'?t\s+care|doesn'?t\s+matter)\b",
    re.IGNORECASE,
)

# Slot names considered "portfolio filters" — declining these allows fallback.
_FILTER_SLOT_NAMES = frozenset({"genre", "cover_style", "category", "word_or_page_count"})

# Statuses that indicate a slot was actively refused/delegated.
_REFUSED_STATUSES = frozenset({"declined", "delegated", "unknown_by_user", "not_applicable"})


# ---------------------------------------------------------------------------
# Pydantic model
# ---------------------------------------------------------------------------


class PortfolioFallbackDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: Literal[
        "ask_filter_once",
        "use_context_filter",
        "fallback_general_samples",
        "fallback_service_samples",
    ]
    reason: str
    filters: dict[str, str | list[str] | bool] = Field(default_factory=dict)
    audit: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


class PortfolioFallbackPolicy:
    """Decides how to handle a portfolio/sample request based on available context."""

    def decide(
        self,
        *,
        message: str,
        intent: IntentVote,
        state: Any,  # ThreadState — avoid circular import
        context_pack: Any | None = None,
        action_plan: Any | None = None,
    ) -> PortfolioFallbackDecision | None:
        """Return a fallback decision or None if this is not a portfolio request."""
        if not _is_portfolio_request(message, intent, action_plan):
            return None

        audit: list[str] = []

        # --- Read persisted portfolio filter state ---
        pfs: dict[str, Any] = dict(getattr(state, "portfolio_filter_state", None) or {})
        asked_count: int = int(pfs.get("asked_count", 0))
        fallback_allowed: bool = bool(pfs.get("fallback_allowed", False))
        audit.append(f"portfolio:asked_count:{asked_count}")

        # --- Check declined/delegated filter slots in state ---
        raw_statuses = getattr(state, "slot_resolution_statuses", None) or []
        slot_statuses = load_slot_statuses(raw_statuses)
        filter_slot_refused = any(
            s.slot in _FILTER_SLOT_NAMES and s.status in _REFUSED_STATUSES and s.forbidden_reask
            for s in slot_statuses
        )
        if filter_slot_refused:
            audit.append("portfolio:filter_slot_refused:True")

        # --- Check message for insistence cues ---
        has_insistence = bool(_INSISTENCE_RE.search(message))
        if has_insistence:
            audit.append("portfolio:insistence:True")

        # --- Determine active context filters ---
        active_service = _get_active_service(state, intent, context_pack)
        active_genre = _get_active_genre(state, context_pack)

        if active_service:
            audit.append(f"portfolio:active_service:{active_service}")
        if active_genre:
            audit.append(f"portfolio:active_genre:{active_genre}")

        # Build filter dict (never include refused genre).
        filters: dict[str, str | list[str] | bool] = {}
        if active_service:
            filters["service"] = active_service
        if active_genre and not filter_slot_refused:
            filters["genre"] = active_genre

        # --- Decision tree ---

        # Trigger fallback when user explicitly refuses/delegates filter OR insists.
        if filter_slot_refused or has_insistence or fallback_allowed or asked_count >= 1:
            if active_service:
                reason = (
                    "user_declined_filter"
                    if filter_slot_refused
                    else (
                        "user_delegated_filter"
                        if has_insistence and "decide" in message.casefold()
                        else (
                            "user_insisted_on_samples"
                            if has_insistence
                            else "filter_asked_once_fallback_allowed"
                        )
                    )
                )
                return PortfolioFallbackDecision(
                    strategy="fallback_service_samples",
                    reason=reason,
                    filters=filters,
                    audit=audit + ["portfolio:strategy:fallback_service_samples"],
                )
            else:
                general_reason = (
                    "user_declined_filter"
                    if filter_slot_refused
                    else (
                        "user_insisted_on_samples"
                        if has_insistence
                        else "filter_asked_once_fallback_allowed"
                    )
                )
                return PortfolioFallbackDecision(
                    strategy="fallback_general_samples",
                    reason=general_reason,
                    filters=filters,
                    audit=audit + ["portfolio:strategy:fallback_general_samples"],
                )

        # Context filter available — service or genre known.
        if active_service or active_genre:
            return PortfolioFallbackDecision(
                strategy="use_context_filter",
                reason="context_filter_available",
                filters=filters,
                audit=audit + ["portfolio:strategy:use_context_filter"],
            )

        # No context yet — ask once.
        return PortfolioFallbackDecision(
            strategy="ask_filter_once",
            reason="portfolio_filter_missing_first_request",
            filters={},
            audit=audit + ["portfolio:strategy:ask_filter_once"],
        )


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------


def read_portfolio_filter_state(state: Any) -> dict[str, Any]:
    raw = getattr(state, "portfolio_filter_state", None)
    return dict(raw) if isinstance(raw, dict) else {}


def update_portfolio_filter_state(
    state: Any,
    *,
    decision: PortfolioFallbackDecision,
    turn_id: str | None = None,
) -> None:
    """Persist portfolio filter tracking back to state."""
    pfs = read_portfolio_filter_state(state)
    strategy = decision.strategy

    if strategy == "ask_filter_once":
        pfs["asked_count"] = int(pfs.get("asked_count", 0)) + 1
        pfs["last_asked_turn_id"] = turn_id
        pfs["fallback_allowed"] = False
    elif strategy in ("fallback_general_samples", "fallback_service_samples"):
        pfs["fallback_allowed"] = True
        pfs["declined"] = True
    elif strategy == "use_context_filter":
        pfs.setdefault("asked_count", 0)

    pfs["last_strategy"] = strategy
    state.portfolio_filter_state = pfs


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _is_portfolio_request(
    message: str,
    intent: IntentVote,
    action_plan: Any | None,
) -> bool:
    if intent.query_primary == QueryIntentType.PORTFOLIO_REQUEST:
        return True
    from bookcraft.components.actions.schemas import ActionType

    if (
        action_plan is not None
        and getattr(action_plan, "action_type", None) == ActionType.PORTFOLIO_LOOKUP
    ):
        return True
    return bool(_PORTFOLIO_CUE_RE.search(message))


def _get_active_service(
    state: Any,
    intent: IntentVote,
    context_pack: Any | None,
) -> str | None:
    if context_pack is not None:
        svc = getattr(context_pack, "active_service", None)
        if svc:
            return str(svc)
    svcs = getattr(state, "project", None)
    if svcs is not None:
        discussed = getattr(svcs, "services_discussed", None) or []
        if discussed:
            raw = getattr(discussed[-1], "service", None)
            val = getattr(raw, "value", None) if raw is not None else None
            if val is not None:
                if isinstance(val, ServiceCategory):
                    return val.value
                return str(val)
    if intent.service_primary is not None:
        return intent.service_primary.value
    return None


def _get_active_genre(state: Any, context_pack: Any | None) -> str | None:
    if context_pack is not None:
        genre = getattr(context_pack, "active_genre", None)
        if genre:
            return str(genre)
    project = getattr(state, "project", None)
    if project is not None:
        field = getattr(project, "genre", None)
        if field is not None:
            val = getattr(field, "value", None)
            if val:
                return str(val)
    return None
