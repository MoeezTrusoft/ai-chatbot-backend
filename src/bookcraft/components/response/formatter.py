import re
from dataclasses import dataclass

from bookcraft.components.response.schemas import FormattedBubble

# Em/en dashes are banned in customer-facing copy: they read as machine-written to
# authors and the style guide asks for plain punctuation. The model is told this in the
# prompt, and this is the deterministic backstop for the times it does it anyway.
_EM_DASH_RE = re.compile(r"\s*\u2014+\s*")
_EN_DASH_RANGE_RE = re.compile(r"(?<=\d)\s*\u2013\s*(?=\d)")
_EN_DASH_RE = re.compile(r"\s*\u2013+\s*")


def strip_dashes(text: str) -> str:
    """Replace em dashes with a comma and en dashes with a hyphen.

    "Price \u2014 timeline" becomes "Price, timeline"; a numeric range "1\u20132" becomes
    "1-2". Repeated separators ("a, , b", "a,, b") are collapsed so the substitution
    never leaves visible debris behind.
    """
    if not text:
        return text

    cleaned = _EN_DASH_RANGE_RE.sub("-", text)
    cleaned = _EM_DASH_RE.sub(", ", cleaned)
    cleaned = _EN_DASH_RE.sub("-", cleaned)
    # Tidy the punctuation around the substitutions: "a \u2014 , b" becomes "a, , b"
    # first, so collapse repeated commas before touching the space before punctuation.
    cleaned = re.sub(r",(\s*,)+", ",", cleaned)
    cleaned = re.sub(r"\s+([,.;:!?])", r"\1", cleaned)
    cleaned = re.sub(r",\s*([.!?;:)])", r"\1", cleaned)
    cleaned = re.sub(r"\(\s*,\s*", "(", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned


def _trim_leading_sentence_fragment(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return stripped

    # RAG chunks can begin mid-sentence because of token overlap, e.g.
    # "ed on the next idea.\nEngagement Models" or "l use.\n- Publishing..."
    # Drop only short lowercase-leading fragments when the next segment looks
    # like a real heading/list/paragraph start.
    if stripped[0].islower():
        separators = [".\n", ". ", "!\n", "! ", "?\n", "? "]
        for separator in separators:
            index = stripped.find(separator)
            if 0 <= index <= 140:
                candidate = stripped[index + len(separator) :].lstrip()
                if candidate and (
                    candidate[0].isupper() or candidate.startswith(("-", "*", "#", "|"))
                ):
                    return candidate

    return stripped


def _flatten_single_line_markdown_table(text: str) -> str:
    compact = " ".join(text.strip().split())

    if compact.count("|") < 8 or "---" not in compact:
        return text

    cells = [cell.strip() for cell in compact.split("|") if cell.strip()]
    cells = [cell for cell in cells if not all(character in "-: " for character in cell)]

    if len(cells) < 6:
        return text

    headers = cells[:3]
    rows = cells[3:]
    lines: list[str] = []

    for index in range(0, len(rows), len(headers)):
        row = rows[index : index + len(headers)]
        if len(row) != len(headers):
            continue

        title = row[0]
        detail_parts = [
            f"{headers[column_index]}: {row[column_index]}"
            for column_index in range(1, len(headers))
            if row[column_index]
        ]

        if detail_parts:
            lines.append(f"- {title}: {'; '.join(detail_parts)}.")
        else:
            lines.append(f"- {title}")

    return "\n".join(lines) if lines else text


def _normalize_response_block(text: str) -> str:
    return _flatten_single_line_markdown_table(_trim_leading_sentence_fragment(text))


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str) -> list[str]:
    """Split text into sentences, flattening paragraph breaks."""
    flat = re.sub(r"\s+", " ", text or "").strip()
    return [part.strip() for part in _SENTENCE_SPLIT_RE.split(flat) if part.strip()]


def strip_questions(text: str) -> str:
    """Remove every question sentence.

    Used when a deterministic question (the call offer) is appended to a reply:
    each message asks at most one question (rule M-03), so the generated part
    must not ask another.
    """
    return " ".join(s for s in split_sentences(text) if not s.rstrip().endswith("?"))


def pack_sentences(text: str, *, limit: int, max_bubbles: int | None = None) -> list[str]:
    """Pack sentences into bubbles of at most ``limit`` characters.

    Whole sentences are kept together. A single sentence longer than the limit is
    split on word boundaries. When ``max_bubbles`` is reached the remaining
    sentences are dropped, so the reply ends at a sentence boundary.
    """
    bubbles: list[str] = []
    current = ""
    for sentence in split_sentences(text):
        pieces = [sentence]
        if len(sentence) > limit:
            pieces, chunk = [], ""
            for word in sentence.split():
                candidate = f"{chunk} {word}".strip()
                if len(candidate) > limit and chunk:
                    pieces.append(chunk)
                    chunk = word[:limit]
                else:
                    chunk = candidate[:limit]
            if chunk:
                pieces.append(chunk)
        for piece in pieces:
            candidate = f"{current} {piece}".strip()
            if len(candidate) <= limit:
                current = candidate
                continue
            if current:
                bubbles.append(current)
            current = piece
            if max_bubbles is not None and len(bubbles) >= max_bubbles:
                return bubbles[:max_bubbles]
    if current:
        bubbles.append(current)
    return bubbles[:max_bubbles] if max_bubbles is not None else bubbles


@dataclass(slots=True)
class ResponseFormatter:
    max_bubble_chars: int = 500

    def format(
        self,
        text: str,
        *,
        approved_urls: set[str] | None = None,
        max_chars: int | None = None,
        max_bubbles: int | None = None,
    ) -> list[FormattedBubble]:
        """Split a reply into chat bubbles.

        ``max_chars`` and ``max_bubbles`` are the hard mobile limits (Visitor
        Messaging Rule Set v2.4, M-02). When either is given the reply is packed
        sentence by sentence and cut at a sentence boundary, so a limit is never
        met by breaking a thought in half. Desktop replies pass neither and keep
        the paragraph-based split.
        """
        sanitized = self._sanitize(text)
        if not sanitized:
            sanitized = "I can help with your BookCraft project. What would you like to work on?"
        sanitized = _trim_leading_sentence_fragment(sanitized)
        if max_chars is not None or max_bubbles is not None:
            limit = max_chars or self.max_bubble_chars
            packed = pack_sentences(sanitized, limit=limit, max_bubbles=max_bubbles)
            return [
                FormattedBubble(
                    text=chunk,
                    bubble_index=index,
                    rich_segments=self._rich_segments(chunk, approved_urls or set()),
                )
                for index, chunk in enumerate(packed)
            ]
        paragraphs = [
            _normalize_response_block(part.strip())
            for part in re.split(r"\n\s*\n", sanitized)
            if part.strip()
        ]
        bubbles: list[FormattedBubble] = []
        for paragraph in paragraphs:
            for chunk in self._chunks(paragraph):
                bubbles.append(
                    FormattedBubble(
                        text=chunk,
                        bubble_index=len(bubbles),
                        rich_segments=self._rich_segments(chunk, approved_urls or set()),
                    )
                )
        return bubbles

    @staticmethod
    def _sanitize(text: str) -> str:
        stripped = re.sub(r"```.*?```", "", text, flags=re.S)
        stripped = re.sub(r"^\s{0,3}#{1,6}\s*", "", stripped, flags=re.M)
        stripped = stripped.replace("**", "").replace("__", "")
        stripped = strip_dashes(stripped).replace("\u2026", "...")
        stripped = stripped.replace("\u201c", '"').replace("\u201d", '"').replace("\u2019", "'")
        if stripped.strip().startswith("{") or stripped.strip().startswith("["):
            return (
                "I have the details I need for this step. "
                "Let me keep the conversation in plain language."
            )
        return stripped.strip()

    def _chunks(self, paragraph: str) -> list[str]:
        if len(paragraph) <= self.max_bubble_chars:
            return [paragraph]
        words = paragraph.split()
        chunks: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if len(candidate) > self.max_bubble_chars and current:
                chunks.append(current)
                current = word
            else:
                current = candidate
        if current:
            chunks.append(current)
        return chunks

    @staticmethod
    def _rich_segments(text: str, approved_urls: set[str]) -> list[dict[str, str]]:
        segments: list[dict[str, str]] = []
        for match in re.finditer(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", text):
            segments.append({"type": "email", "text": match.group(0)})
        for match in re.finditer(r"https?://\S+", text):
            url = match.group(0).rstrip(".,)")
            if url in approved_urls:
                segments.append({"type": "url", "text": url})
        return segments
