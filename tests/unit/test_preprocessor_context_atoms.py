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


@pytest.mark.asyncio
async def test_preprocessor_detects_complex_production_order_services(
    preprocessor: SharedPreprocessor,
) -> None:
    result = await preprocessor.process(
        "I have a completed fantasy novel and I’m not sure what order to do things "
        "in. It still needs a final proofread, a professional cover, interior "
        "formatting, publishing setup, and some basic launch preparation. I don’t "
        "want to waste money doing steps in the wrong sequence."
    )

    assert result.deterministic_atoms["services"] == [
        "editing_proofreading",
        "cover_design_illustration",
        "interior_formatting",
        "publishing_distribution",
        "marketing_promotion",
    ]


@pytest.mark.asyncio
async def test_preprocessor_detects_image_heavy_cookbook_formatting(
    preprocessor: SharedPreprocessor,
) -> None:
    result = await preprocessor.process(
        "I have a cookbook with photos, recipe tables, ingredient lists, and "
        "section dividers. I need it to look clean as paperback and Kindle without "
        "the layout breaking. I may also need light proofreading because the recipes "
        "came from different contributors."
    )

    services = result.deterministic_atoms["services"]

    assert "interior_formatting" in services
    assert "editing_proofreading" in services


@pytest.mark.asyncio
async def test_preprocessor_detects_childrens_picture_book_idea_stage_services(
    preprocessor: SharedPreprocessor,
) -> None:
    result = await preprocessor.process(
        "I only have an idea for a children’s picture book about a shy robot and "
        "a brave little girl. I need help writing the story, creating illustrations, "
        "formatting it for print and Kindle, and publishing it on Amazon. I don’t "
        "know the word count or page count yet."
    )

    services = result.deterministic_atoms["services"]

    assert "ghostwriting" in services
    assert "cover_design_illustration" in services
    assert "interior_formatting" in services
    assert "publishing_distribution" in services


@pytest.mark.asyncio
async def test_preprocessor_detects_finished_my_manuscript_and_children_fiction(
    preprocessor: SharedPreprocessor,
) -> None:
    result = await preprocessor.process(
        "I have finished my manuscript. Its fiction children book as I told you."
    )

    assert result.deterministic_atoms["manuscript_status"] == "completed"
    assert result.deterministic_atoms["genre"] == "children's fiction"


@pytest.mark.asyncio
async def test_preprocessor_query_cues_are_boundary_safe_and_negation_aware(
    preprocessor: SharedPreprocessor,
) -> None:
    agenda = await preprocessor.process("Add this to the agenda for Linda.")
    no_nda = await preprocessor.process("I don't need an NDA.")
    no_price = await preprocessor.process("I don't care about price.")
    real_quote = await preprocessor.process("Can you give me a quote for ghostwriting?")
    real_agreement = await preprocessor.process("Generate service agreement today.")
    negated_agreement = await preprocessor.process("I am not ready for agreement.")

    assert "nda_request" not in agenda.deterministic_atoms.get("query_cues", [])
    assert "nda_request" not in no_nda.deterministic_atoms.get("query_cues", [])
    assert "pricing_question" not in no_price.deterministic_atoms.get("query_cues", [])
    assert "pricing_question" in real_quote.deterministic_atoms["query_cues"]
    assert "agreement_request" in real_agreement.deterministic_atoms["query_cues"]
    assert "agreement_request" not in negated_agreement.deterministic_atoms.get(
        "query_cues",
        [],
    )
