import re
from dataclasses import dataclass

from bookcraft.components.response.schemas import FormattedBubble


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


@dataclass(slots=True)
class ResponseFormatter:
    max_bubble_chars: int = 500

    def format(self, text: str, *, approved_urls: set[str] | None = None) -> list[FormattedBubble]:
        sanitized = self._sanitize(text)
        if not sanitized:
            sanitized = "I can help with your BookCraft project. What would you like to work on?"
        sanitized = _trim_leading_sentence_fragment(sanitized)
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
        stripped = stripped.replace("\u2014", "-").replace("\u2013", "-").replace("\u2026", "...")
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
