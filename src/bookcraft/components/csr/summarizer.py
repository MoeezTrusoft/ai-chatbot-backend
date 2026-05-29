"""CSR conversation summarizer with sliding-window compression.

Maintains two structures in ThreadState:
  csr_context_recent_verbatim — last 3 CSR turns (verbatim)
  csr_context_abstract        — LLM-compressed summary of older turns

When a 4th turn arrives, the oldest verbatim turn is compressed into the
abstract (single LLM call), then removed from the verbatim window.
This bounds prompt size while preserving recency without truncation.
"""

from __future__ import annotations

import structlog

from bookcraft.components.llm.protocols import LLMProvider
from bookcraft.domain.state import ThreadState

logger = structlog.get_logger(__name__)

_VERBATIM_WINDOW = 3  # number of most-recent CSR turns kept in full

_COMPRESS_SYSTEM = """\
You are a conversation summarizer for a publishing services company.
Compress the supplied CSR conversation turn into the existing abstract.
Preserve all factual content (services discussed, timelines, user concerns, contact details).
Keep the output under 200 words. Output plain text only — no JSON, no markdown.
"""

_COMPRESS_USER = """\
EXISTING ABSTRACT:
{abstract}

NEW CSR TURN TO INCORPORATE:
CSR ({csr_name}): {csr_message}
{user_turn}

Produce an updated abstract incorporating all information from the new turn.
"""


class _CompressedAbstract:
    text: str

    def __init__(self, text: str = "") -> None:
        self.text = text

    # Minimal BaseModel-like interface for adapter.structured()
    @classmethod
    def model_validate(cls, data: object) -> "_CompressedAbstract":
        if isinstance(data, dict):
            return cls(text=str(data.get("text", "")))
        return cls(text=str(data))

    def model_dump(self) -> dict[str, str]:
        return {"text": self.text}


from pydantic import BaseModel  # noqa: E402


class _AbstractModel(BaseModel):
    text: str = ""


class CsrContextSummarizer:
    """Sliding-window CSR conversation summarizer."""

    def __init__(self, adapter: LLMProvider | None = None) -> None:
        self._adapter = adapter

    async def ingest(
        self,
        state: ThreadState,
        user_message: str | None,
        csr_message: str,
        csr_name: str = "CSR",
    ) -> None:
        """Ingest one CSR turn into the sliding window."""
        turn = {
            "role": "csr",
            "csr_name": csr_name,
            "csr_message": csr_message,
            "user_message": user_message or "",
        }

        verbatim: list[dict[str, str]] = list(state.csr_context_recent_verbatim)

        if len(verbatim) < _VERBATIM_WINDOW:
            verbatim.append(turn)
        else:
            # Oldest turn overflows — compress it into the abstract.
            oldest = verbatim.pop(0)
            verbatim.append(turn)
            new_abstract = await self._compress(
                existing_abstract=state.csr_context_abstract,
                csr_name=oldest["csr_name"],
                csr_message=oldest["csr_message"],
                user_message=oldest.get("user_message", ""),
            )
            state.csr_context_abstract = new_abstract

        state.csr_context_recent_verbatim = verbatim

    async def _compress(
        self,
        existing_abstract: str,
        csr_name: str,
        csr_message: str,
        user_message: str,
    ) -> str:
        """Compress the oldest CSR turn into the running abstract via LLM."""
        if self._adapter is None:
            # No adapter — naive append truncated to 800 chars
            snippet = f"CSR ({csr_name}): {csr_message[:200]}"
            if user_message:
                snippet += f" | User: {user_message[:100]}"
            combined = f"{existing_abstract} {snippet}".strip()
            return combined[:800]

        user_turn_line = f"User: {user_message}" if user_message else ""
        user_prompt = _COMPRESS_USER.format(
            abstract=existing_abstract or "(none yet)",
            csr_name=csr_name,
            csr_message=csr_message,
            user_turn=user_turn_line,
        )

        try:
            result = await self._adapter.structured(
                system=_COMPRESS_SYSTEM,
                user=user_prompt,
                output_model=_AbstractModel,
                purpose="csr_context_compression",
            )
            if isinstance(result, _AbstractModel) and result.text:
                return result.text
        except Exception as exc:
            logger.warning(
                "csr_context_compression_failed",
                exception_class=exc.__class__.__name__,
            )

        # Fallback: naive append
        snippet = f"CSR ({csr_name}): {csr_message[:200]}"
        return f"{existing_abstract} {snippet}".strip()[:800]
