import pytest

from bookcraft.components.extraction import CombinedExtraction, CombinedExtractor, StateApplier
from bookcraft.components.extraction.schemas import StateDelta
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.preprocessor.embedding import EmbeddingClient
from bookcraft.components.preprocessor.processor import SharedPreprocessor
from bookcraft.components.preprocessor.sidecars import PreprocessorSidecars
from bookcraft.components.response.generator import _cta_for_intent
from bookcraft.domain.enums import QueryIntentType, SalesStage, ServiceCategory, Source
from bookcraft.domain.state import ThreadState


class StaticEmbeddingClient(EmbeddingClient):
    def __init__(self) -> None:
        super().__init__(
            tei_url="http://unused",
            timeout_seconds=0.1,
            dimensions=1,
            degraded_mode_enabled=True,
        )

    async def embed(self, normalized_text: str, language: str) -> list[float]:
        return [1.0]


@pytest.fixture
def preprocessor() -> SharedPreprocessor:
    return SharedPreprocessor(
        sidecars=PreprocessorSidecars(
            negation_cues=["no", "not", "without", "do not", "don't"],
            hedge_cues=["may", "might", "maybe", "could", "considering"],
            counterfactual_cues=["if", "would", "hypothetically"],
            typography_replacements={},
            compound_variants={},
        ),
        embedding_client=StaticEmbeddingClient(),
    )


@pytest.mark.asyncio
async def test_extractor_persists_finished_manuscript_and_children_fiction(
    preprocessor: SharedPreprocessor,
) -> None:
    processed = await preprocessor.process(
        "I have finished my manuscript. Its fiction children book as I told you."
    )

    extraction = await CombinedExtractor().extract(processed, ThreadState())
    state = StateApplier().apply(ThreadState(), extraction)

    assert state.project.manuscript_status.value == "completed_draft"
    assert state.project.genre.value == "children's fiction"


def test_cta_does_not_ask_known_manuscript_stage_or_genre_again() -> None:
    state = StateApplier().apply(
        ThreadState(),
        CombinedExtraction(
            state_deltas=[
                StateDelta(
                    path="project.manuscript_status",
                    value="completed_draft",
                    confidence=0.9,
                    source=Source.USER_STATED,
                    extracted_by="test",
                    raw_excerpt="finished manuscript",
                ),
                StateDelta(
                    path="project.genre",
                    value="children's fiction",
                    confidence=0.9,
                    source=Source.USER_STATED,
                    extracted_by="test",
                    raw_excerpt="children fiction",
                ),
            ]
        ),
    )

    intent = IntentVote(
        query_primary=QueryIntentType.SERVICE_QUESTION,
        service_primary=ServiceCategory.COVER_DESIGN_ILLUSTRATION,
        funnel_stage=SalesStage.SERVICE_DISCOVERY,
        needs_clarification=True,
        confidence=0.92,
        rationale="test",
        evidence=["test"],
    )

    cta = _cta_for_intent(intent, {}, state).casefold()

    assert "manuscript stage" not in cta
    assert "draft" not in cta
    assert "starting from scratch" not in cta
    assert "genre" not in cta
    assert "word count" in cta or "page count" in cta
