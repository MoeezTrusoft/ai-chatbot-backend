from dataclasses import dataclass

from prometheus_client import Counter, Histogram

from bookcraft.components.extraction.schemas import CombinedExtraction, StateDelta
from bookcraft.components.preprocessor.detectors.document_request_detector import (
    has_agreement_request,
    has_nda_request,
)
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.domain.enums import Source
from bookcraft.domain.state import ThreadState

EXTRACTION_SECONDS = Histogram("extraction_seconds", "Combined extraction latency.")
EXTRACTION_FIELDS = Counter(
    "extraction_fields_per_turn",
    "Fields extracted per turn.",
    ["category"],
)


@dataclass(slots=True)
class CombinedExtractor:
    provider_name: str = "mock_haiku"

    async def extract(self, message: ProcessedMessage, state: ThreadState) -> CombinedExtraction:
        del state
        with EXTRACTION_SECONDS.time():
            extraction = CombinedExtraction()
            atoms = message.deterministic_atoms
            if emails := atoms.get("emails"):
                email = _first_string(emails)
                if email:
                    extraction.contact.email = email
                    extraction.state_deltas.append(
                        StateDelta(
                            path="personal.email",
                            value=email,
                            confidence=0.98,
                            source=Source.USER_STATED,
                            extracted_by="deterministic_preextractor.v1",
                            raw_excerpt=email,
                        )
                    )
                    EXTRACTION_FIELDS.labels(category="contact").inc()
            if phones := atoms.get("phones"):
                phone = _first_string(phones)
                if phone:
                    extraction.contact.phone = phone
                    extraction.state_deltas.append(
                        StateDelta(
                            path="personal.phone",
                            value=phone,
                            confidence=0.92,
                            source=Source.USER_STATED,
                            extracted_by="deterministic_preextractor.v1",
                            raw_excerpt=phone,
                        )
                    )
                    EXTRACTION_FIELDS.labels(category="contact").inc()
            if word_counts := atoms.get("word_counts"):
                count = _first_int(word_counts)
                if count is not None:
                    extraction.project.word_count = count
                    extraction.state_deltas.append(
                        StateDelta(
                            path="project.word_count",
                            value=count,
                            confidence=0.96,
                            source=Source.USER_STATED,
                            extracted_by="deterministic_preextractor.v1",
                            raw_excerpt=f"{count} words",
                        )
                    )
                    EXTRACTION_FIELDS.labels(category="project").inc()
            if page_counts := atoms.get("page_counts"):
                count = _first_int(page_counts)
                if count is not None:
                    extraction.project.page_count = count
                    extraction.state_deltas.append(
                        StateDelta(
                            path="project.page_count",
                            value=count,
                            confidence=0.94,
                            source=Source.USER_STATED,
                            extracted_by="deterministic_preextractor.v1",
                            raw_excerpt=f"{count} pages",
                        )
                    )
                    EXTRACTION_FIELDS.labels(category="project").inc()
            if status := atoms.get("manuscript_status"):
                extraction.project.manuscript_status = str(status)
                extraction.state_deltas.append(
                    StateDelta(
                        path="project.manuscript_status",
                        value=status,
                        confidence=0.86,
                        source=Source.USER_STATED,
                        extracted_by="deterministic_preextractor.v1",
                        raw_excerpt=str(status),
                    )
                )
                EXTRACTION_FIELDS.labels(category="project").inc()
            if genre := atoms.get("genre"):
                genre_text = str(genre)
                extraction.project.genre = genre_text
                extraction.state_deltas.append(
                    StateDelta(
                        path="project.genre",
                        value=genre_text,
                        confidence=0.9,
                        source=Source.USER_STATED,
                        extracted_by="deterministic_preextractor.v1",
                        raw_excerpt=genre_text,
                    )
                )
                EXTRACTION_FIELDS.labels(category="project").inc()
            if services := _string_list(atoms.get("services")):
                service_list = services
                extraction.service_interest.services = service_list
                extraction.commercial.selected_services = service_list
                EXTRACTION_FIELDS.labels(category="service_interest").inc(len(service_list))
            if has_nda_request(
                message.normalized,
                negation_spans=message.negation_spans,
                counterfactual_spans=message.counterfactual_spans,
            ):
                extraction.document_request.requested_type = "nda"
            if has_agreement_request(
                message.normalized,
                negation_spans=message.negation_spans,
                counterfactual_spans=message.counterfactual_spans,
            ):
                extraction.document_request.requested_type = "agreement"
            if "?" in message.normalized:
                extraction.user_questions = [message.normalized]
            return extraction


def _first_string(value: object) -> str | None:
    if isinstance(value, list) and value and isinstance(value[0], str):
        return value[0]
    return None


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _first_int(value: object) -> int | None:
    if isinstance(value, list) and value and isinstance(value[0], int):
        return value[0]
    return None
