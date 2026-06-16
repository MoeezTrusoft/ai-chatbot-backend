"""LLM-based metadata extractor.

Runs synchronously during handle_turn(), after deterministic extraction and before
response generation. The current response immediately benefits from extracted facts.

Confidence gating:
  ≥ 0.85  → StateDelta with full confidence; StateApplier will override existing
             state only if the delta confidence exceeds the stored confidence.
  < 0.85  → StateDelta with confidence set to 0.3; only fills currently empty fields.

Rich free-text facts (cover_preferences, section_structure, page_dimensions,
target_audience) do not map to FieldMeta paths and are returned separately in
``rich_metadata`` for the caller to merge into state.service_metadata["book_specs"].
"""

from __future__ import annotations

import re

import structlog
from prometheus_client import Counter, Histogram

from bookcraft.components.extraction.llm_schemas import ExtractedValue, LLMExtractedFacts
from bookcraft.components.extraction.schemas import StateDelta
from bookcraft.components.leads.contact_utils import is_non_name_token, is_valid_phone
from bookcraft.components.llm.protocols import LLMProvider
from bookcraft.domain.enums import Source, coerce_manuscript_status
from bookcraft.domain.state import ThreadState

logger = structlog.get_logger(__name__)

LLM_EXTRACTION_CALLS = Counter(
    "llm_extraction_calls_total",
    "LLM metadata extraction calls.",
    ["outcome"],  # success | failed | skipped
)
LLM_EXTRACTION_SECONDS = Histogram(
    "llm_extraction_seconds",
    "LLM metadata extraction latency.",
)
LLM_EXTRACTION_FIELDS = Counter(
    "llm_extraction_fields_total",
    "Fields extracted by LLM extractor.",
    ["field"],
)
NAME_AUTHORSHIP_REJECTED = Counter(
    "llm_extraction_name_authorship_rejected_total",
    "Customer-name deltas dropped because the name was a book author, not the customer.",
)
EXTRACTION_VALUE_REJECTED = Counter(
    "llm_extraction_value_rejected_total",
    "LLM-extracted values dropped by a validity guard (timezone-as-name, range-as-phone).",
    ["field", "reason"],
)

# Authorship attribution cues. A name appearing in these contexts is the book's
# AUTHOR, not the customer — guards the bug where "the past & the future written by
# Thomas Ray" caused "Thomas Ray" to be saved as personal.name.
_AUTHORSHIP_CUE = re.compile(
    r"\b(?:written|penned|authored|illustrated|created|published)\s+by\b"
    r"|\bauthor(?:ed|\s+is|:)",
    re.IGNORECASE,
)
# Trailing authorship clause on a book title. REQUIRES an authorship verb so titles
# that merely contain " by " (e.g. "Death by Chocolate") are never truncated.
_TITLE_AUTHORSHIP_SUFFIX = re.compile(
    r"\s*(?:[-—,]\s*)?(?:written|penned|authored|illustrated)\s+by\s+.+$",
    re.IGNORECASE,
)


def _name_is_book_authorship(
    name_value: object, source_quote: str | None, book_title: object
) -> bool:
    """True when an extracted ``name`` is really a book author, not the customer.

    Fires when the name's evidence quote carries an authorship cue, attributes the
    name with "by <name>", or the name is contained within the (raw) book title.
    The ``by <name>`` check is gated on the exact extracted name, so a title like
    "Death by Chocolate" never trips it (the model would not extract "Chocolate").
    """
    name = str(name_value or "").strip().casefold()
    if not name:
        return False
    quote = source_quote or ""
    if _AUTHORSHIP_CUE.search(quote):
        return True
    if re.search(r"\bby\s+" + re.escape(name) + r"\b", quote, re.IGNORECASE):
        return True
    if book_title and name in str(book_title).strip().casefold():
        return True
    return False


def _strip_authorship_from_title(title_value: object) -> object:
    """Strip a trailing "written/penned/authored by X" clause from a book title.

    "The Past & The Future written by Thomas Ray" → "The Past & The Future".
    Returns the value unchanged when there is no authorship suffix, when it is not a
    string, or when stripping would empty the title.
    """
    if not isinstance(title_value, str):
        return title_value
    stripped = _TITLE_AUTHORSHIP_SUFFIX.sub("", title_value).strip()
    return stripped or title_value

_HIGH_CONFIDENCE_THRESHOLD = 0.85
_LOW_CONFIDENCE_FILL_VALUE = 0.3  # fills only empty fields via StateApplier rules

# Fields that have FieldMeta state paths and are converted to StateDelta.
_FIELD_TO_STATE_PATH: dict[str, str] = {
    "name": "personal.name",
    "email": "personal.email",
    "phone": "personal.phone",
    "preferred_contact_method": "personal.preferred_contact_method",
    "timezone": "personal.timezone",
    "book_title": "project.title",
    "genre": "project.genre",
    "sub_genre": "project.sub_genre",
    "word_count": "project.word_count",
    "page_count": "project.page_count",
    "manuscript_status": "project.manuscript_status",
    "budget_range": "commercial.budget_range",
    "timeline": "commercial.timeline_expectation",
}

# Rich free-text fields stored in state.service_metadata["book_specs"].
_RICH_TEXT_FIELDS = {"page_dimensions", "cover_preferences", "section_structure", "target_audience"}

_EXTRACTION_SYSTEM = """\
You are a metadata extraction assistant for BookCraft, a professional publishing services company.
Your sole task is to extract factual information explicitly shared by the user about themselves
and their book project. You output structured JSON — nothing else.

EXTRACTION RULES:
1. Extract ONLY facts the user has explicitly stated — including direct answers to the
   assistant's questions. A user's direct reply to a question ("Its all in head right now."
   answering "is it all in your head?") IS an explicit statement, not an inference.
   Never infer facts the user did not state or imply at all.
2. Resolve coreferences before extraction: if the user says "my thriller" and the known state
   already has genre=thriller, do not re-extract genre — instead note the coreference in
   coreference_notes and leave the genre field null.
3. Confidence scores:
   - 0.90–1.00: user made a clear, direct statement ("my book is called X", "I have 60,000 words")
   - 0.60–0.84: hedged statement ("I think around 60k", "maybe thriller", "probably fiction")
   - 0.40–0.59: inference only (not stated directly — use sparingly)
4. source_quote: copy the exact phrase from the user message that justifies the extraction.
   Leave as empty string "" if no specific phrase applies.
5. Leave any field null if there is no evidence for it in the user message.
6. For word_count and page_count: value must be an integer, not a string.
7. Do not extract information that was already in the known state UNLESS the user is
   explicitly correcting or updating it.
8. For manuscript_status: read the user's full statement and use your own judgment to pick
   the single best-fit value from this closed list — do NOT require a literal phrase match.
   Valid values and their meanings:
     "not_started"      — no writing done yet: just an idea, preparing, starting from scratch,
                          story still in their head, haven't begun writing
     "notes_only"       — has notes, bullet points, recordings, or a rough outline but no
                          actual draft pages written
     "early_draft"      — has some drafted content: any chapters written, pages drafted,
                          partial manuscript, prologue written, chapters completed
     "full_draft"       — complete or near-complete draft manuscript, full book written
     "editing_complete" — already finished their own editing pass; they do NOT want an
                          editing service — treat editing as negated for this conversation
   Confidence rules:
     0.92 — user's statement clearly implies the stage (even if phrased indirectly)
     0.70 — statement is vague or hedged ("I think I have some notes", "maybe a draft")
   Representative examples (guidance, not exhaustive):
     "I have 5 chapters"                  → early_draft,      0.92
     "Drafted"  (answer to chapter question) → early_draft,   0.92
     "Prologue and 5 complete chapters"   → early_draft,      0.92
     "Chapters completed done"            → early_draft,      0.92
     "I have a few books, did a lot of spiritual writing" → early_draft, 0.85
     "I have lore already written out and some random scenes" → notes_only, 0.92
     "I have a chapter summary / summary of first chapters" → notes_only, 0.92
     "Partially outlined"                 → notes_only,       0.92
     "The outline isn't 100%, just ideas" → notes_only,       0.85
     "Just an idea space / loose idea"    → notes_only,       0.85
     "Preparing myself, getting everything I need to start" → not_started, 0.92
     "Still in my head"                   → not_started,      0.92
     "Starting from scratch"              → not_started,      0.92
     "I have some notes"                  → notes_only,       0.85
     "I have lore, character arcs, how it ends" → notes_only, 0.92
     "Full manuscript done"               → full_draft,       0.92
     "Done with editing"                  → editing_complete, 0.92
10. For word_count: extract the integer even when phrased as an estimate.
    "around 130,000" → 130000, confidence 0.70 (hedged)
    "maybe 100,000"  → 100000, confidence 0.70 (hedged)
    "probably 80k"   → 80000,  confidence 0.60 (hedged)
    "about 50,000 words" → 50000, confidence 0.75
    When the user CORRECTS a previous word count ("100,000 sounds more reasonable",
    "let's say 80k instead") — extract the new value at confidence 0.90 so it
    overrides the prior hedged extraction.
11. For preferred_contact_method: extract when the user states how they want to be reached.
    Examples:
    "i'd prefer email"           → "email",  0.92
    "prefer to be contacted by phone" → "phone", 0.92
    "email is better for me"     → "email",  0.92
    "please call me"             → "phone",  0.92
12. For timezone: ALWAYS normalize to an IANA timezone string — never store the raw alias.
    Common US mappings:
    "eastern", "EST", "EDT", "eastern time", "eastern timezone" → "America/New_York"
    "central", "CST", "CDT", "central time", "central timezone" → "America/Chicago"
    "mountain", "MST", "MDT", "mountain time"                   → "America/Denver"
    "pacific", "PST", "PDT", "pacific time", "pacific timezone" → "America/Los_Angeles"
    "alaska", "AKST"                                             → "America/Anchorage"
    "hawaii", "HST"                                              → "Pacific/Honolulu"
    "PKT", "Pakistan"                                            → "Asia/Karachi"
    Extract the IANA string, not the alias. Confidence 0.92 for any clear timezone statement.
13. For name: this is the CUSTOMER's OWN name only — NEVER a book's author or a name
    printed in a title. Do NOT extract a name that appears after "written by", "by",
    "authored by", or inside a book title as the customer name; that is the author. If
    the ONLY name present is the book's author (e.g. "The Past & The Future written by
    Thomas Ray"), leave name EMPTY. For a genuine self-identification ("my name is X",
    "I'm X", "this is X", "call me X"), normalize obvious typos (e.g. "Chri9stopher" →
    "Christopher") and extract the cleaned name at confidence 0.92.
14. For book_title: extract ONLY the title itself, stripping any trailing authorship
    clause — "The Past & The Future written by Thomas Ray" → book_title "The Past & The
    Future" (and do NOT put "Thomas Ray" in name).
15. A timezone abbreviation or phrase (EST, CST, PST, "eastern", "central time", "EST -
    clifford@x.com") is a TIMEZONE, never a name. In "EST - clifford@x.com" the name is
    EMPTY, timezone is "America/New_York", email is "clifford@x.com". Use the assistant's
    question for context: a reply to "what timezone are you in?" is a timezone, not a name.
16. For phone: extract ONLY a real phone number (10–15 digits). A year/era range
    ("1770-1810"), an age range ("6-12"), page counts, word counts, ISBNs, or any number
    that is not a dialable phone MUST NOT be extracted as phone — leave phone EMPTY.
"""

_EXTRACTION_USER_TEMPLATE = """\
KNOWN STATE (already captured — do not re-extract unless user is correcting):
{known_facts}

ASSISTANT'S LAST MESSAGE (the question or statement the user is replying to):
{assistant_message}

USER'S REPLY:
{user_message}

EXTRACTION INSTRUCTION:
Read the user's reply IN CONTEXT of the assistant's previous message above.
If the assistant asked about a specific field (manuscript stage, word count, genre,
name, email, phone, timezone, etc.) and the user's reply answers it — even briefly
or informally — that reply IS an explicit statement about that field. Extract it.

Examples of Q&A extractions that MUST be captured:
  Assistant asked "is it all in your head right now?" → User: "Its all in head right now."
  → manuscript_status: "not_started", confidence 0.92

  Assistant asked "roughly how long is the first book?" → User: "around 130,000"
  → word_count: 130000, confidence 0.70

  Assistant asked "what's your name and best email?" → User: "Gina gina@example.com"
  → name: "Gina", email: "gina@example.com", confidence 0.92

  Assistant asked "what's your book's title?" → User: "the past & the future written by Thomas Ray"
  → book_title: "The Past & The Future"  (NOT name — "Thomas Ray" is the AUTHOR, not the customer)

  Assistant asked "do you have notes or are you starting from scratch?" → User: "starting from scratch"
  → manuscript_status: "not_started", confidence 0.92

  Assistant asked "do you have an outline or draft chapters?" → User: "partially outlined"
  → manuscript_status: "notes_only", confidence 0.92

  Assistant asked "what timezone are you in?" → User: "eastern timezone"
  → timezone: "America/New_York", confidence 0.92

  Assistant asked "what timezone works for you?" → User: "central"
  → timezone: "America/Chicago", confidence 0.92

Extract all factual metadata the user has shared. Do NOT extract from the assistant message.
Respond with a valid JSON object matching the LLMExtractedFacts schema.
"""


def _build_known_facts_block(state: ThreadState) -> str:
    """Summarise the current FieldMeta state as a human-readable block for the prompt."""
    lines: list[str] = []

    def _add(label: str, field_meta: object) -> None:
        val = getattr(field_meta, "value", None)
        if val is not None:
            lines.append(f"  {label}: {val}")

    _add("name", state.personal.name)
    _add("email", state.personal.email)
    _add("phone", state.personal.phone)
    _add("preferred_contact_method", state.personal.preferred_contact_method)
    _add("timezone", state.personal.timezone)
    _add("book_title", state.project.title)
    _add("genre", state.project.genre)
    _add("sub_genre", state.project.sub_genre)
    _add("word_count", state.project.word_count)
    _add("page_count", state.project.page_count)
    _add("manuscript_status", state.project.manuscript_status)
    _add("budget_range", state.commercial.budget_range)
    _add("timeline", state.commercial.timeline_expectation)

    # Rich metadata from service_metadata["book_specs"]
    book_specs = (state.service_metadata or {}).get("book_specs", {})
    for key, val in book_specs.items():
        if val:
            lines.append(f"  {key}: {val}")

    return "\n".join(lines) if lines else "  (none yet)"


def _delta_confidence(ev: ExtractedValue) -> float:
    """Return the confidence to store in StateDelta, applying low-fill downscaling."""
    if ev.confidence >= _HIGH_CONFIDENCE_THRESHOLD:
        return ev.confidence
    # Low-confidence extraction: force below any reasonable deterministic value
    # so StateApplier only applies it when the field is currently empty.
    return _LOW_CONFIDENCE_FILL_VALUE


def _facts_to_deltas(facts: LLMExtractedFacts) -> list[StateDelta]:
    """Convert structured LLM output into StateDelta objects for existing StateApplier."""
    deltas: list[StateDelta] = []

    # Raw (uncleaned) title is needed to disambiguate an author name from the
    # customer name BEFORE the title's own authorship suffix is stripped below.
    _title_ev: ExtractedValue | None = getattr(facts, "book_title", None)
    raw_book_title = _title_ev.value if _title_ev is not None else None

    for field_name, state_path in _FIELD_TO_STATE_PATH.items():
        ev: ExtractedValue | None = getattr(facts, field_name, None)
        if ev is None or ev.value is None:
            continue

        raw_value = ev.value
        # Enforce integer type for count fields
        if field_name in {"word_count", "page_count"}:
            try:
                raw_value = int(raw_value)
            except (TypeError, ValueError):
                continue

        # The extraction prompt uses a coarse 5-value vocabulary
        # (not_started / notes_only / early_draft / full_draft / editing_complete)
        # that is NOT a valid ManuscriptStatus. Map it to the canonical enum
        # before persisting; drop the delta if it cannot be mapped so we never
        # write a value that fails validation on the next load.
        if field_name == "manuscript_status":
            coerced = coerce_manuscript_status(raw_value)
            if coerced is None:
                continue
            raw_value = coerced.value

        # Guard: never persist a book's author as the customer's name. Dropping the
        # delta keeps personal.name empty so the consultation flow still asks for the
        # real name instead of skipping it (and addressing the customer as the author).
        if field_name == "name" and _name_is_book_authorship(
            raw_value, ev.source_quote, raw_book_title
        ):
            NAME_AUTHORSHIP_REJECTED.inc()
            logger.info(
                "llm_name_authorship_rejected",
                rejected_name=str(raw_value),
                source_quote=ev.source_quote,
                book_title=str(raw_book_title) if raw_book_title is not None else None,
            )
            continue

        # Guard: a timezone abbreviation ("EST"), filler word, or bare abbreviation
        # is not a customer name (bug: "EST - clifford@…" captured "EST" as the name).
        if field_name == "name" and is_non_name_token(raw_value):
            EXTRACTION_VALUE_REJECTED.labels(field="name", reason="non_name_token").inc()
            logger.info("llm_name_rejected_non_name", rejected_name=str(raw_value))
            continue

        # Guard: a "phone" that is not a real number — a year/era range like
        # "1770-1810", an age range like "6-12", or anything without 10–15 digits —
        # must never reach personal.phone (bug: a historical period saved as phone).
        if field_name == "phone" and not is_valid_phone(str(raw_value)):
            EXTRACTION_VALUE_REJECTED.labels(field="phone", reason="not_a_phone").inc()
            logger.info("llm_phone_rejected_not_a_number", rejected_phone=str(raw_value))
            continue

        # Keep the book title clean: "<Title> written by <Author>" → "<Title>".
        if field_name == "book_title":
            raw_value = _strip_authorship_from_title(raw_value)

        deltas.append(
            StateDelta(
                path=state_path,
                value=raw_value,
                confidence=_delta_confidence(ev),
                source=Source.AI_EXTRACTED,
                extracted_by="llm_metadata_extractor.v1",
                raw_excerpt=ev.source_quote or None,
            )
        )
        LLM_EXTRACTION_FIELDS.labels(field=field_name).inc()

    return deltas


def _facts_to_rich_metadata(facts: LLMExtractedFacts) -> dict[str, str]:
    """Extract free-text rich metadata that has no FieldMeta path."""
    result: dict[str, str] = {}
    for field_name in _RICH_TEXT_FIELDS:
        ev: ExtractedValue | None = getattr(facts, field_name, None)
        if ev is not None and ev.value and ev.confidence >= _HIGH_CONFIDENCE_THRESHOLD:
            result[field_name] = str(ev.value)
            LLM_EXTRACTION_FIELDS.labels(field=field_name).inc()
    return result


class LLMExtractionResult:
    """Result from LLMMetadataExtractor.extract()."""

    __slots__ = ("state_deltas", "rich_metadata", "coreference_notes")

    def __init__(
        self,
        state_deltas: list[StateDelta],
        rich_metadata: dict[str, str],
        coreference_notes: list[str],
    ) -> None:
        self.state_deltas = state_deltas
        self.rich_metadata = rich_metadata
        self.coreference_notes = coreference_notes


_EMPTY_RESULT = LLMExtractionResult(
    state_deltas=[],
    rich_metadata={},
    coreference_notes=[],
)


class LLMMetadataExtractor:
    """Calls the LLM to extract rich metadata synchronously during handle_turn()."""

    def __init__(self, adapter: LLMProvider) -> None:
        self._adapter = adapter

    async def extract(
        self,
        user_text: str,
        assistant_text: str,
        state: ThreadState,
    ) -> LLMExtractionResult:
        if not user_text.strip():
            LLM_EXTRACTION_CALLS.labels(outcome="skipped").inc()
            return _EMPTY_RESULT

        known_facts = _build_known_facts_block(state)
        user_prompt = _EXTRACTION_USER_TEMPLATE.format(
            known_facts=known_facts,
            user_message=user_text,
            assistant_message=assistant_text or "(none)",
        )

        try:
            with LLM_EXTRACTION_SECONDS.time():
                raw = await self._adapter.structured(
                    system=_EXTRACTION_SYSTEM,
                    user=user_prompt,
                    output_model=LLMExtractedFacts,
                    purpose="llm_metadata_extraction",
                )
        except Exception as exc:
            logger.warning(
                "llm_extraction_failed",
                exception_class=exc.__class__.__name__,
            )
            LLM_EXTRACTION_CALLS.labels(outcome="failed").inc()
            return _EMPTY_RESULT

        if not isinstance(raw, LLMExtractedFacts):
            try:
                facts = LLMExtractedFacts.model_validate(raw.model_dump())
            except Exception:
                LLM_EXTRACTION_CALLS.labels(outcome="failed").inc()
                return _EMPTY_RESULT
        else:
            facts = raw

        deltas = _facts_to_deltas(facts)
        rich = _facts_to_rich_metadata(facts)

        if facts.coreference_notes:
            logger.debug(
                "llm_extraction_coreferences",
                notes=facts.coreference_notes,
            )

        LLM_EXTRACTION_CALLS.labels(outcome="success").inc()
        return LLMExtractionResult(
            state_deltas=deltas,
            rich_metadata=rich,
            coreference_notes=facts.coreference_notes,
        )
