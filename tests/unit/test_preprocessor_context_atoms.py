import pytest

from bookcraft.components.preprocessor.embedding import EmbeddingClient
from bookcraft.components.preprocessor.processor import SharedPreprocessor
from bookcraft.components.preprocessor.sidecars import PreprocessorSidecars


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
async def test_preprocessor_extracts_runtime_context_atoms(
    preprocessor: SharedPreprocessor,
) -> None:
    result = await preprocessor.process(
        "Generate the service agreement now for ghostwriting, proofreading, "
        "and marketing, even if the quote is not finalized."
    )

    assert result.deterministic_atoms["services"] == [
        "ghostwriting",
        "editing_proofreading",
        "marketing_promotion",
    ]
    assert result.deterministic_atoms["negated_terms"] == ["quote"]
    assert "agreement_request" in result.deterministic_atoms["query_cues"]


@pytest.mark.asyncio
async def test_preprocessor_extracts_safety_markers(
    preprocessor: SharedPreprocessor,
) -> None:
    result = await preprocessor.process(
        "If I signed today, would you promise a bestseller campaign and "
        "cut the price by 40 percent?"
    )

    assert result.deterministic_atoms["services"] == ["marketing_promotion"]
    assert "counterfactual" in result.deterministic_atoms["context_markers"]
    assert "guarantee_pressure" in result.deterministic_atoms["context_markers"]
    assert "price_number" in result.deterministic_atoms["forbid_markers"]
    assert "guarantee" in result.deterministic_atoms["forbid_markers"]
    assert "pricing_question" in result.deterministic_atoms["query_cues"]


@pytest.mark.asyncio
async def test_preprocessor_preserves_multiservice_order_and_negation(
    preprocessor: SharedPreprocessor,
) -> None:
    result = await preprocessor.process(
        "I need editing, formatting, publishing, and marketing, but no ghostwriting."
    )

    assert result.deterministic_atoms["services"] == [
        "editing_proofreading",
        "interior_formatting",
        "publishing_distribution",
        "marketing_promotion",
    ]
    assert result.deterministic_atoms["negated_services"] == ["ghostwriting"]


@pytest.mark.asyncio
async def test_preprocessor_negates_comma_separated_service_list(
    preprocessor: SharedPreprocessor,
) -> None:
    result = await preprocessor.process(
        "I do not need cover design, audiobook production, video trailer, "
        "author website, or marketing. I only need proofreading and "
        "clean print-ready formatting."
    )

    assert result.deterministic_atoms["services"] == [
        "editing_proofreading",
        "interior_formatting",
    ]
    assert set(result.deterministic_atoms["negated_services"]) == {
        "cover_design_illustration",
        "audiobook_production",
        "video_trailer",
        "author_website",
        "marketing_promotion",
    }
