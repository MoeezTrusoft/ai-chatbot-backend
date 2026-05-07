import re
from dataclasses import dataclass

from bookcraft.components.response.schemas import FormattedBubble


@dataclass(slots=True)
class ResponseFormatter:
    max_bubble_chars: int = 500

    def format(self, text: str) -> list[FormattedBubble]:
        sanitized = self._sanitize(text)
        if not sanitized:
            sanitized = "I can help with your BookCraft project. What would you like to work on?"
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", sanitized) if part.strip()]
        bubbles: list[FormattedBubble] = []
        for paragraph in paragraphs:
            for chunk in self._chunks(paragraph):
                bubbles.append(
                    FormattedBubble(
                        text=chunk,
                        bubble_index=len(bubbles),
                        rich_segments=self._rich_segments(chunk),
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
    def _rich_segments(text: str) -> list[dict[str, str]]:
        segments: list[dict[str, str]] = []
        for match in re.finditer(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", text):
            segments.append({"type": "email", "text": match.group(0)})
        for match in re.finditer(r"https?://\S+", text):
            segments.append({"type": "url", "text": match.group(0)})
        return segments
