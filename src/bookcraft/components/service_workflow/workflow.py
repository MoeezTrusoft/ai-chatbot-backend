"""BookCraft service workflow: predecessor, successor, and parallel relationships.

Encodes the logical order in which BookCraft services are performed so the bot
can advise authors on sequencing, warn about out-of-order requests, and suggest
what work can run in parallel to save time.

How it informs the user:
  1. Proactive next-step: "After Editing, Interior Formatting is the natural next step."
  2. Parallel savings:    "Cover Design can run alongside Editing — saves time."
  3. Out-of-order warn:   "Publishing needs Formatting done first."
  4. Full pipeline view:  "Here is the recommended sequence for your project: ..."
  5. Milestone:           "Editing done — you're ready for Formatting and Audiobook."

Engines compute. Claude writes final customer-facing prose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Canonical service names
# ---------------------------------------------------------------------------

_SERVICE_NAMES: dict[str, str] = {
    "ghostwriting":              "Ghostwriting",
    "editing_proofreading":      "Editing & Proofreading",
    "cover_design_illustration": "Cover Design & Illustrations",
    "interior_formatting":       "Interior Layout & Formatting",
    "publishing_distribution":   "Publishing & Distribution",
    "marketing_promotion":       "Marketing & Promotion",
    "audiobook_production":      "Audiobook Production",
    "video_trailer":             "Video Trailer",
    "author_website":            "Author's Website",
}

# Aliases: common user phrasings → canonical service key
_SERVICE_ALIASES: dict[str, str] = {
    "ghostwriting":          "ghostwriting",
    "ghost writing":         "ghostwriting",
    "ghost write":           "ghostwriting",
    "editing":               "editing_proofreading",
    "proofreading":          "editing_proofreading",
    "copy editing":          "editing_proofreading",
    "copyediting":           "editing_proofreading",
    "developmental editing": "editing_proofreading",
    "cover design":          "cover_design_illustration",
    "cover illustration":    "cover_design_illustration",
    "book cover":            "cover_design_illustration",
    "formatting":            "interior_formatting",
    "interior formatting":   "interior_formatting",
    "layout":                "interior_formatting",
    "interior layout":       "interior_formatting",
    "publishing":            "publishing_distribution",
    "distribution":          "publishing_distribution",
    "publish":               "publishing_distribution",
    "marketing":             "marketing_promotion",
    "promotion":             "marketing_promotion",
    "launch marketing":      "marketing_promotion",
    "pre-launch":            "marketing_promotion",
    "after launch":          "marketing_promotion",
    "audiobook":             "audiobook_production",
    "audio book":            "audiobook_production",
    "narration":             "audiobook_production",
    "video trailer":         "video_trailer",
    "book trailer":          "video_trailer",
    "trailer":               "video_trailer",
    "author website":        "author_website",
    "website":               "author_website",
    "author site":           "author_website",
}

# ---------------------------------------------------------------------------
# Workflow graph — canonical, matches the spec exactly
# ---------------------------------------------------------------------------

_WORKFLOW: dict[str, dict[str, object]] = {
    "ghostwriting": {
        "predecessors": [],
        "successors": ["editing_proofreading"],
        "parallel": [],
        "timing_note": "Starting point — no prerequisites.",
    },
    "editing_proofreading": {
        "predecessors": ["ghostwriting"],
        "successors": ["interior_formatting", "audiobook_production"],
        "parallel": ["cover_design_illustration"],
        "timing_note": (
            "Cover Design can run simultaneously — start both to save time."
        ),
    },
    "cover_design_illustration": {
        "predecessors": ["ghostwriting"],
        "successors": ["interior_formatting", "audiobook_production"],
        "parallel": ["editing_proofreading"],
        "timing_note": (
            "Editing & Proofreading can run simultaneously — start both to save time."
        ),
    },
    "interior_formatting": {
        "predecessors": ["ghostwriting", "editing_proofreading", "cover_design_illustration"],
        "successors": [
            "publishing_distribution", "marketing_promotion",
            "video_trailer", "author_website",
        ],
        "parallel": [],
        "timing_note": (
            "Needs both Editing and Cover Design completed first. "
            "After Formatting, Publishing, Marketing, Video Trailer, and "
            "Author Website all become available."
        ),
    },
    "publishing_distribution": {
        "predecessors": [
            "ghostwriting", "editing_proofreading",
            "cover_design_illustration", "interior_formatting",
        ],
        "successors": ["marketing_promotion"],
        "parallel": ["marketing_promotion", "author_website", "video_trailer"],
        "timing_note": (
            "At-launch Marketing, Author Website, and Video Trailer can run in parallel "
            "while the book is being distributed. After-launch Marketing follows."
        ),
    },
    "marketing_promotion": {
        "predecessors": [
            "ghostwriting", "editing_proofreading", "cover_design_illustration",
            "interior_formatting", "publishing_distribution",
        ],
        "successors": [],
        "parallel": [],
        "timing_note": (
            "Pre-launch marketing starts after Formatting. "
            "At-launch marketing runs alongside Publishing. "
            "After-launch marketing follows Publishing."
        ),
    },
    "audiobook_production": {
        "predecessors": [
            "ghostwriting", "editing_proofreading", "cover_design_illustration",
            "interior_formatting", "publishing_distribution",
        ],
        "successors": [],
        "parallel": [],
        "timing_note": "Can begin once Editing and Formatting are complete.",
    },
    "video_trailer": {
        "predecessors": [
            "ghostwriting", "editing_proofreading", "cover_design_illustration",
            "interior_formatting", "publishing_distribution",
        ],
        "successors": [],
        "parallel": [],
        "timing_note": "Produced after Formatting is done.",
    },
    "author_website": {
        "predecessors": [
            "ghostwriting", "editing_proofreading", "cover_design_illustration",
            "interior_formatting", "publishing_distribution",
        ],
        "successors": [],
        "parallel": [],
        "timing_note": "Can be built after Formatting and alongside Publishing.",
    },
}

def _get_preds(service: str) -> list[str]:
    """Helper to get typed predecessor list from the workflow graph."""
    val = _WORKFLOW.get(service, {}).get("predecessors", [])
    if isinstance(val, list):
        return [str(v) for v in val]
    return []


def _get_succs(service: str) -> list[str]:
    val = _WORKFLOW.get(service, {}).get("successors", [])
    if isinstance(val, list):
        return [str(v) for v in val]
    return []


def _get_pars(service: str) -> list[str]:
    val = _WORKFLOW.get(service, {}).get("parallel", [])
    if isinstance(val, list):
        return [str(v) for v in val]
    return []


# Stable topological order for display
_TOPO_ORDER: list[str] = [
    "ghostwriting",
    "editing_proofreading",
    "cover_design_illustration",
    "interior_formatting",
    "publishing_distribution",
    "marketing_promotion",
    "audiobook_production",
    "video_trailer",
    "author_website",
]


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class WorkflowAdvice:
    """Structured workflow advice for one requested service.

    Carries structured facts; Claude writes final customer-facing prose.
    """

    service: str
    service_name: str
    can_start_now: bool
    blocking_predecessors: list[str] = field(default_factory=list)
    parallel_services: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    summary: str = ""
    timing_note: str = ""
    ordering_violation: bool = False

    def as_prompt_facts(self) -> str:
        """Structured fact block for the LLM prompt."""
        lines = [f"Service Workflow — {self.service_name}:"]

        if self.ordering_violation:
            pred_names = [_SERVICE_NAMES.get(p, p) for p in self.blocking_predecessors]
            lines.append(
                f"  ⚠ OUT-OF-ORDER REQUEST: {self.service_name} requires "
                f"{', '.join(pred_names)} to be completed first. "
                "Gently advise the author of the correct sequence."
            )
        elif not self.blocking_predecessors:
            lines.append("  • Can start immediately — no prerequisites.")
        else:
            pred_names = [_SERVICE_NAMES.get(p, p) for p in self.blocking_predecessors]
            lines.append(f"  • Prerequisites (complete first): {', '.join(pred_names)}")

        if self.parallel_services:
            par_names = [_SERVICE_NAMES.get(s, s) for s in self.parallel_services]
            lines.append(
                f"  • Runs in parallel with (can happen simultaneously): {', '.join(par_names)}"
            )

        if self.next_steps:
            next_names = [_SERVICE_NAMES.get(s, s) for s in self.next_steps]
            lines.append(f"  • Natural next step(s) after: {', '.join(next_names)}")

        if self.timing_note:
            lines.append(f"  • Timing: {self.timing_note}")

        lines.append(
            "Use these facts to advise naturally — do not expose the structured format."
        )
        return "\n".join(lines)


@dataclass
class MultiServiceAdvice:
    """Advice for a set of requested services — full pipeline view."""

    services: list[str]
    ordered_sequence: list[str] = field(default_factory=list)
    parallel_opportunities: list[tuple[str, str]] = field(default_factory=list)
    can_start_immediately: list[str] = field(default_factory=list)
    summary: str = ""

    def as_prompt_facts(self) -> str:
        lines = ["Multi-Service Pipeline Advice:"]
        if self.ordered_sequence:
            seq_names = [_SERVICE_NAMES.get(s, s) for s in self.ordered_sequence]
            lines.append(f"  • Recommended sequence: {' → '.join(seq_names)}")
        if self.can_start_immediately:
            imm_names = [_SERVICE_NAMES.get(s, s) for s in self.can_start_immediately]
            lines.append(f"  • Can start right now: {', '.join(imm_names)}")
        if self.parallel_opportunities:
            for a, b in self.parallel_opportunities:
                lines.append(
                    f"  • Time-saving parallel: {_SERVICE_NAMES.get(a, a)} "
                    f"and {_SERVICE_NAMES.get(b, b)} can run simultaneously."
                )
        lines.append(
            "Advise the author naturally. Highlight parallel opportunities as time-saving wins."
        )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

_SEQUENCE_QUESTION_RE = re.compile(
    r"\b(?:"
    r"what\s+(?:comes?|is)\s+(?:next|after|before)|"
    r"(?:after|before|following|preceding)\s+(?:the\s+)?(?:editing|formatting|ghostwriting|cover|publishing|marketing|audiobook|trailer|website)|"
    r"(?:what|which)\s+(?:is\s+the\s+)?(?:order|sequence|step)|"
    r"what\s+(?:order|sequence)|"
    r"the\s+(?:order|sequence|steps?)\s+(?:of|for)|"
    r"in\s+what\s+order|"
    r"can\s+(?:i|we)\s+do\s+(?:both|them|it)\s+(?:at\s+the\s+same\s+time|simultaneously|together|in\s+parallel)|"
    r"(?:at\s+the\s+same\s+time|simultaneously|in\s+parallel)|"
    r"full\s+(?:process|pipeline|workflow|journey)|"
    r"(?:do|start)\s+(?:\w+\s+)?(?:while|during)\s+(?:the\s+)?\w+"
    r")\b",
    re.IGNORECASE,
)


def is_sequencing_question(text: str) -> bool:
    """Return True when the user is asking about service order or parallel work."""
    return bool(_SEQUENCE_QUESTION_RE.search(text))


def resolve_service_aliases(text: str) -> list[str]:
    """Extract canonical service keys mentioned in a text string."""
    found: list[str] = []
    lowered = text.lower()
    for alias, canonical in _SERVICE_ALIASES.items():
        if alias in lowered and canonical not in found:
            found.append(canonical)
    return found


# ---------------------------------------------------------------------------
# Workflow engine
# ---------------------------------------------------------------------------


class ServiceWorkflow:
    """Computes workflow advice given the author's current stage.

    Called from chat.py; results are injected into the LLM prompt.

    User-facing advice patterns:
      advise()              → one service, checks prerequisites
      advise_multi()        → full pipeline for multiple services
      next_steps_after()    → milestone celebration: "what's next after editing?"
      detect_violation()    → is the user requesting a service out of order?
      user_guidance()       → compact hint string for response_hint injection
      full_pipeline_text()  → complete ordered pipeline display
    """

    def advise(
        self,
        *,
        requested_service: str,
        completed_services: list[str] | None = None,
        in_progress_services: list[str] | None = None,
    ) -> WorkflowAdvice | None:
        if requested_service not in _WORKFLOW:
            return None

        node = _WORKFLOW[requested_service]
        preds = _get_preds(requested_service)
        succs = _get_succs(requested_service)
        pars = _get_pars(requested_service)
        timing: str = str(node.get("timing_note", ""))

        done = set(completed_services or [])
        in_prog = set(in_progress_services or [])
        already_started = done | in_prog

        blocking = [p for p in preds if p not in already_started]
        can_start = len(blocking) == 0
        next_steps = [s for s in succs if s not in already_started]
        svc_name = _SERVICE_NAMES.get(requested_service, requested_service)

        if can_start:
            summary = f"{svc_name} can start now."
            if pars:
                par_names = [_SERVICE_NAMES.get(s, s) for s in pars]
                summary += f" Can run in parallel with {', '.join(par_names)}."
        else:
            block_names = [_SERVICE_NAMES.get(b, b) for b in blocking]
            summary = f"{svc_name} requires {', '.join(block_names)} to complete first."

        return WorkflowAdvice(
            service=requested_service,
            service_name=svc_name,
            can_start_now=can_start,
            blocking_predecessors=blocking,
            parallel_services=pars,
            next_steps=next_steps,
            summary=summary,
            timing_note=timing,
            ordering_violation=not can_start and bool(preds),
        )

    def advise_multi(self, services: list[str]) -> MultiServiceAdvice:
        valid = [s for s in services if s in _WORKFLOW]
        if not valid:
            return MultiServiceAdvice(services=services)

        ordered = sorted(
            valid,
            key=lambda s: _TOPO_ORDER.index(s) if s in _TOPO_ORDER else 99,
        )

        parallel_pairs: list[tuple[str, str]] = []
        seen: set[frozenset[str]] = set()
        for svc in ordered:
            for par in _get_pars(svc):
                if par in valid:
                    pair: frozenset[str] = frozenset({svc, par})
                    if pair not in seen:
                        seen.add(pair)
                        parallel_pairs.append((svc, par))

        can_start_now = [
            s for s in ordered
            if not any(
                p in valid
                for p in _get_preds(s)
            )
        ]

        if len(ordered) == 1:
            summary = f"Start with {_SERVICE_NAMES.get(ordered[0], ordered[0])}."
        else:
            names = [_SERVICE_NAMES.get(s, s) for s in ordered]
            summary = "Sequence: " + " → ".join(names) + "."
            if parallel_pairs:
                par_notes = [
                    f"{_SERVICE_NAMES.get(a, a)} + {_SERVICE_NAMES.get(b, b)}"
                    for a, b in parallel_pairs
                ]
                summary += f" Parallel wins: {'; '.join(par_notes)}."

        return MultiServiceAdvice(
            services=valid,
            ordered_sequence=ordered,
            parallel_opportunities=parallel_pairs,
            can_start_immediately=can_start_now,
            summary=summary,
        )

    def next_steps_after(self, completed_service: str) -> list[str]:
        """Services that become available after completing one service."""
        if completed_service not in _WORKFLOW:
            return []
        return _get_succs(completed_service)

    def detect_violation(
        self, requested_service: str, completed_services: list[str]
    ) -> list[str]:
        """Return missing prerequisites. Empty = can start now."""
        if requested_service not in _WORKFLOW:
            return []
        done = set(completed_services)
        return [p for p in _get_preds(requested_service) if p not in done]

    def user_guidance(
        self,
        service: str,
        completed: list[str] | None = None,
        in_progress: list[str] | None = None,
    ) -> str:
        """Compact guidance string for injection into response_hint."""
        advice = self.advise(
            requested_service=service,
            completed_services=completed,
            in_progress_services=in_progress,
        )
        return advice.as_prompt_facts() if advice else ""

    def full_pipeline_text(self, highlight_services: list[str] | None = None) -> str:
        """Full ordered pipeline with parallel annotations for the LLM prompt."""
        hl = set(highlight_services or [])
        lines = ["BookCraft Full Service Pipeline (canonical order):"]
        for i, svc in enumerate(_TOPO_ORDER, 1):
            svc_name = _SERVICE_NAMES[svc]
            pars_list = _get_pars(svc)
            mark = " ◄ your service" if svc in hl else ""

            par_note = ""
            if pars_list:
                par_in_scope = [_SERVICE_NAMES.get(p, p) for p in pars_list if p in hl]
                par_all = [_SERVICE_NAMES.get(p, p) for p in pars_list]
                if par_in_scope:
                    par_note = f" [parallel with {', '.join(par_in_scope)}]"
                elif par_all:
                    par_note = f" [can run alongside {', '.join(par_all)}]"

            lines.append(f"  {i}. {svc_name}{par_note}{mark}")

        lines.append(
            "\nAdvise the author on this sequence naturally. "
            "Highlight parallel opportunities as time and cost savings."
        )
        return "\n".join(lines)
