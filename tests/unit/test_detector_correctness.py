from __future__ import annotations

from collections.abc import Mapping

import pytest

from bookcraft.components.preprocessor.detectors import has_date_hint
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
            negation_cues=[
                "no",
                "not",
                "without",
                "do not",
                "don't",
                "haven't",
                "have not",
            ],
            hedge_cues=["may", "might", "maybe", "could", "considering"],
            counterfactual_cues=["if", "would", "hypothetically"],
            typography_replacements={},
            compound_variants={},
        ),
        embedding_client=StaticEmbeddingClient(),
    )


async def _atoms(
    preprocessor: SharedPreprocessor,
    text: str,
) -> Mapping[str, object]:
    result = await preprocessor.process(text)
    return result.deterministic_atoms


def _query_cues(atoms: Mapping[str, object]) -> list[str]:
    value = atoms.get("query_cues")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "I have not published it yet.",
        "I haven't published the book yet.",
    ],
)
async def test_negated_published_does_not_set_published(
    preprocessor: SharedPreprocessor,
    text: str,
) -> None:
    atoms = await _atoms(preprocessor, text)

    assert atoms.get("manuscript_status") != "published"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("The book is published already.", "published"),
        ("I have finished my manuscript.", "completed"),
        ("The draft is complete.", "completed"),
        ("I only have an idea.", "idea"),
        ("I have some chapters done.", "partial_draft"),
    ],
)
async def test_manuscript_status_positive_cases(
    preprocessor: SharedPreprocessor,
    text: str,
    expected: str,
) -> None:
    atoms = await _atoms(preprocessor, text)

    assert atoms.get("manuscript_status") == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("It is a memoir story.", "memoir"),
        ("It is a fiction children book.", "children's fiction"),
        ("It is a children book.", "children's book"),
        ("It is a fantasy novel.", "fantasy"),
        ("It is a non-fiction business book.", "business"),
    ],
)
async def test_genre_detection_precedence(
    preprocessor: SharedPreprocessor,
    text: str,
    expected: str,
) -> None:
    atoms = await _atoms(preprocessor, text)

    assert atoms.get("genre") == expected
    if text == "It is a non-fiction business book.":
        assert atoms.get("genre") != "fiction"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "Can you give me a quote for ghostwriting?",
        "How much does editing cost?",
    ],
)
async def test_pricing_positive_cases_include_pricing_question(
    preprocessor: SharedPreprocessor,
    text: str,
) -> None:
    atoms = await _atoms(preprocessor, text)

    assert "pricing_question" in _query_cues(atoms)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "I can't quote a fixed one yet.",
        "Use this quote in the opening chapter.",
        "I don't care about price right now.",
        "Don't send a quote yet.",
        "No pricing needed right now.",
    ],
)
async def test_pricing_negative_cases_do_not_include_pricing_question(
    preprocessor: SharedPreprocessor,
    text: str,
) -> None:
    atoms = await _atoms(preprocessor, text)

    assert "pricing_question" not in _query_cues(atoms)


@pytest.mark.asyncio
async def test_nda_positive_case_produces_query_cue(
    preprocessor: SharedPreprocessor,
) -> None:
    atoms = await _atoms(preprocessor, "I need an NDA before sharing the manuscript.")

    assert "nda_request" in _query_cues(atoms)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "I don't need an NDA.",
        "No NDA needed.",
        "Skip the NDA.",
        "agenda",
        "Linda",
        "Honda",
        "panda",
    ],
)
async def test_nda_negative_cases_do_not_produce_query_cue(
    preprocessor: SharedPreprocessor,
    text: str,
) -> None:
    atoms = await _atoms(preprocessor, text)

    assert "nda_request" not in _query_cues(atoms)


@pytest.mark.asyncio
async def test_agreement_positive_case_produces_query_cue(
    preprocessor: SharedPreprocessor,
) -> None:
    atoms = await _atoms(preprocessor, "Generate the service agreement.")

    assert "agreement_request" in _query_cues(atoms)


@pytest.mark.asyncio
async def test_negated_agreement_does_not_produce_query_cue(
    preprocessor: SharedPreprocessor,
) -> None:
    atoms = await _atoms(preprocessor, "I am not ready for agreement.")

    assert "agreement_request" not in _query_cues(atoms)


def test_date_hint_boundaries() -> None:
    assert not has_date_hint("novel")
    assert has_date_hint("Nov 20")
    assert has_date_hint("November 20, 2026")
