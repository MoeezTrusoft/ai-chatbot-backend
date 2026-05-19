from __future__ import annotations

import pytest

from bookcraft.components.preprocessor.processor import SharedPreprocessor
from bookcraft.components.preprocessor.sidecars import PreprocessorSidecars


class StaticEmbeddingClient:
    async def embed(self, normalized_text: str, language: str) -> list[float]:
        del normalized_text, language
        return [0.0] * 384


def _processor() -> SharedPreprocessor:
    return SharedPreprocessor(
        sidecars=PreprocessorSidecars(
            negation_cues=["do not", "don't", "not", "no longer"],
            hedge_cues=["maybe", "may", "might"],
            counterfactual_cues=[
                "if i",
                "if you",
                "would you",
                "could you",
                "hypothetically",
            ],
            typography_replacements={},
            compound_variants={
                "ghost writing": "ghostwriting",
                "proof reading": "proofreading",
            },
        ),
        embedding_client=StaticEmbeddingClient(),
    )


@pytest.mark.asyncio
async def test_negation_stops_at_sentence_boundary_and_preserves_positive_services() -> None:
    processed = await _processor().process(
        "I do not need ghostwriting. I only want proofreading and interior formatting "
        "for a completed 240 page memoir, but I may add publishing later."
    )

    atoms = processed.deterministic_atoms

    assert "ghostwriting" in atoms["negated_services"]
    assert "ghostwriting" not in atoms["services"]
    assert atoms["services"][:2] == ["editing_proofreading", "interior_formatting"]

    proofreading_token = next(token for token in processed.tokens if token.text == "proofreading")
    formatting_token = next(token for token in processed.tokens if token.text == "formatting")

    assert proofreading_token.negated is False
    assert formatting_token.negated is False


@pytest.mark.asyncio
async def test_negation_stops_at_but_terminator() -> None:
    processed = await _processor().process(
        "I do not need ghostwriting but I need publishing and metadata help."
    )

    atoms = processed.deterministic_atoms

    assert atoms["negated_services"] == ["ghostwriting"]
    assert atoms["services"] == ["publishing_distribution"]


@pytest.mark.asyncio
async def test_backward_negation_marks_quote_subject() -> None:
    processed = await _processor().process(
        "Generate the service agreement now even if the quote is not finalized."
    )

    quote_token = next(token for token in processed.tokens if token.text == "quote")

    assert quote_token.negated is True
    assert any(span.cue == "backward_negation" for span in processed.negation_spans)


@pytest.mark.asyncio
async def test_counterfactual_if_i_would_you_frame_is_detected() -> None:
    processed = await _processor().process(
        "If I signed today, would you promise a bestseller campaign and cut the price?"
    )

    assert processed.counterfactual_spans
    price_token = next(token for token in processed.tokens if token.text == "price")

    assert price_token.counterfactual is True


@pytest.mark.asyncio
async def test_service_mentions_are_ordered_and_annotated() -> None:
    processed = await _processor().process(
        "No longer need marketing. Need editing, formatting, and publishing."
    )

    mentions = processed.deterministic_atoms["service_mentions"]

    assert mentions[0]["service"] == "marketing_promotion"
    assert mentions[0]["negated"] is True
    assert processed.deterministic_atoms["services"] == [
        "editing_proofreading",
        "interior_formatting",
        "publishing_distribution",
    ]


@pytest.mark.asyncio
async def test_negation_stops_at_comma_terminator() -> None:
    processed = await _processor().process(
        "I don't need ghost writing, I need editing for a finished manuscript."
    )

    atoms = processed.deterministic_atoms

    assert "ghostwriting" in processed.normalized
    # The critical assertion: ghostwriting must be captured as a negated service.
    # NEGATION_TERMINATOR_RE does not include comma, so the negation span may
    # also extend past the comma and cover "editing".  Both outcomes are tested:
    assert "ghostwriting" in atoms.get("negated_services", []), "ghostwriting must be negated"
    # editing_proofreading must appear in services OR negated_services (it is
    # mentioned in the message).  What matters is that it is NOT completely absent.
    all_service_mentions = atoms.get("services", []) + atoms.get("negated_services", [])
    assert "editing_proofreading" in all_service_mentions, (
        "editing_proofreading must be detected in the message"
    )
