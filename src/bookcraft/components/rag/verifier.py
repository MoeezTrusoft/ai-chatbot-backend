import json
import re
from dataclasses import dataclass
from pathlib import Path

from prometheus_client import Counter

from bookcraft.components.rag.pipeline import load_chunks
from bookcraft.components.rag.schemas import RagIngestionReport, RejectedChunk

RAG_REJECTED_CHUNKS = Counter(
    "rag_rejected_chunk_total",
    "RAG chunks rejected by verifier.",
    ["reason"],
)

FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("exact_price", re.compile(r"\$\s?\d+", re.I)),
    ("percentage", re.compile(r"\b\d+\s?%", re.I)),
    ("timeline_range", re.compile(r"\b\d+\s?[-–]\s?\d+\s?(days|weeks|months)\b", re.I)),
    ("concrete_timeline", re.compile(r"\b\d+\s?(business days|days|weeks|months)\b", re.I)),
    ("unit_pricing", re.compile(r"\b(per word|per page|per hour|pfh|monthly fee)\b", re.I)),
    (
        "quote_calculation",
        re.compile(r"\b(quote calculation|discount logic|delivery estimate)\b", re.I),
    ),
]


@dataclass(frozen=True, slots=True)
class RagVerifier:
    strict: bool = True

    def verify_build_dir(self, build_dir: Path) -> RagIngestionReport:
        chunks = load_chunks(build_dir)
        source_checksums = json.loads((build_dir / "source_checksums.json").read_text())
        if not isinstance(source_checksums, dict):
            msg = "source_checksums.json must contain an object."
            raise ValueError(msg)
        rejected: list[RejectedChunk] = []
        accepted = 0
        for chunk in chunks:
            match = self._first_forbidden(chunk.content)
            if match is None:
                accepted += 1
                continue
            reason, pattern, excerpt = match
            RAG_REJECTED_CHUNKS.labels(reason=reason).inc()
            rejected.append(
                RejectedChunk(
                    chunk_id=chunk.chunk_id,
                    source_filename=chunk.metadata.source_filename,
                    section=chunk.metadata.section,
                    reason=reason,
                    pattern=pattern,
                    excerpt=excerpt,
                )
            )
        report = RagIngestionReport(
            accepted_count=accepted,
            rejected_count=len(rejected),
            source_checksums={str(key): str(value) for key, value in source_checksums.items()},
            verifier_status="failed" if rejected else "passed",
            rejected_chunks=rejected,
        )
        (build_dir / "rejected_chunks_report.json").write_text(
            json.dumps(report.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )
        if self.strict and rejected:
            msg = f"RAG verifier rejected {len(rejected)} chunks."
            raise ValueError(msg)
        return report

    @staticmethod
    def _first_forbidden(content: str) -> tuple[str, str, str] | None:
        for reason, pattern in FORBIDDEN_PATTERNS:
            match = pattern.search(content)
            if match:
                start = max(0, match.start() - 60)
                end = min(len(content), match.end() + 60)
                return reason, pattern.pattern, content[start:end]
        return None
