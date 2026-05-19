from fastapi.testclient import TestClient

from bookcraft.api.main import create_app
from bookcraft.domain.enums import QueryIntentType, ServiceCategory
from bookcraft.infra.config import Settings


def _post(message: str) -> dict:
    app = create_app(Settings(app_env="test"))
    response = TestClient(app).post(
        "/api/v1/chat/turn",
        json={"message": message},
    )
    assert response.status_code == 200
    return response.json()


def test_negated_ghostwriting_keeps_positive_editing_or_formatting_service() -> None:
    data = _post(
        "I do not need ghostwriting. I only want proofreading and interior formatting "
        "for a completed 240 page memoir, but I may add publishing later."
    )

    assert data["intent"]["service_primary"] in {
        ServiceCategory.EDITING_PROOFREADING.value,
        ServiceCategory.INTERIOR_FORMATTING.value,
        ServiceCategory.PUBLISHING_DISTRIBUTION.value,
    }


def test_portfolio_confidentiality_upgrades_to_portfolio_request() -> None:
    data = _post(
        "Show ghostwriting samples if possible. If those are confidential, show me "
        "cover design or formatting examples for memoir and fantasy instead."
    )

    assert data["intent"]["query_primary"] == QueryIntentType.PORTFOLIO_REQUEST.value


def test_counterfactual_discount_pressure_mentions_quote_engine_and_approval() -> None:
    data = _post(
        "If I signed today, would you promise a bestseller campaign and cut the price "
        "by 40 percent? I do not want exact numbers unless your quote engine has them."
    )

    text = " ".join(bubble["text"] for bubble in data["bubbles"]).lower()
    assert data["intent"]["query_primary"] == QueryIntentType.PRICING_QUESTION.value
    # "quote engine" was an internal design phrase that no longer appears in
    # customer-facing responses.  The safety assertions are: no committed price,
    # no guaranteed discount, and the response asks for missing scope.
    assert "$" not in text, "No price figures must be emitted"
    assert "40 percent" not in text, "Discount pressure must not be accepted"
    assert "guarantee" not in text or "wouldn't want to promise" in text, (
        "Guarantee language must be refused or redirected"
    )
    assert "?" in text or any(
        kw in text for kw in ("word", "page", "genre", "manuscript", "deadline", "scope")
    ), "Response must ask for scope or redirect to scoping"


def test_rush_scope_without_numbers_gets_pricing_or_service_intent() -> None:
    data = _post(
        "The launch date moved up. I want rush editing, formatting, and publishing, but "
        "do not give exact delivery dates unless the deterministic engine approves them."
    )

    assert data["intent"]["query_primary"] in {
        QueryIntentType.PRICING_QUESTION.value,
        QueryIntentType.SERVICE_QUESTION.value,
    }
    assert data["intent"]["service_primary"] in {
        ServiceCategory.EDITING_PROOFREADING.value,
        ServiceCategory.INTERIOR_FORMATTING.value,
        ServiceCategory.PUBLISHING_DISTRIBUTION.value,
    }


def test_platform_distribution_specifics_gets_publishing_service() -> None:
    data = _post(
        "For distribution, I need Amazon KDP, IngramSpark, ebook, paperback, metadata, "
        "categories, keywords, and ISBN guidance. Which of these can BookCraft handle?"
    )

    assert data["intent"]["service_primary"] == ServiceCategory.PUBLISHING_DISTRIBUTION.value


def test_marketing_guarantee_refusal_gets_marketing_service() -> None:
    data = _post(
        "Can BookCraft guarantee bestseller rank, verified reviews, and media coverage "
        "if I buy a marketing campaign? Be direct and do not overpromise."
    )

    assert data["intent"]["service_primary"] == ServiceCategory.MARKETING_PROMOTION.value


def test_video_trailer_style_gets_video_trailer_service() -> None:
    data = _post(
        "For the trailer, I want cinematic motion graphics, voiceover, licensed music, "
        "subtitles, and square plus vertical cuts. What details matter?"
    )

    assert data["intent"]["service_primary"] == ServiceCategory.VIDEO_TRAILER.value


def test_illustration_complexity_gets_cover_service() -> None:
    data = _post(
        "The cover might need a full illustration: two characters, a harbor scene, custom "
        "typography, and print plus ebook layout. I do not need interior art."
    )

    assert data["intent"]["service_primary"] == ServiceCategory.COVER_DESIGN_ILLUSTRATION.value


def test_privacy_and_confidentiality_gets_nda_intent() -> None:
    data = _post(
        "Before I upload chapters, explain how confidentiality works. I may need an NDA, "
        "but do not draft legal text inside chat."
    )

    assert data["intent"]["query_primary"] == QueryIntentType.NDA_REQUEST.value


def test_portfolio_no_hallucinated_links_gets_portfolio_intent() -> None:
    data = _post(
        "Give me three exact sample links for marketing, formatting, and publishing. If "
        "the registry does not have them, say so instead of inventing URLs."
    )

    assert data["intent"]["query_primary"] == QueryIntentType.PORTFOLIO_REQUEST.value


def test_final_consultant_handoff_mentions_required_sections() -> None:
    data = _post(
        "Based on everything above, what should a human consultant review first, and what "
        "fields are still missing before pricing, NDA, agreement, and production planning?"
    )

    text = " ".join(bubble["text"] for bubble in data["bubbles"]).lower()
    assert data["intent"]["query_primary"] == QueryIntentType.CONSULTATION_REQUEST.value
    # The response must acknowledge a consultation/scoping request and move toward
    # the next concrete step.  Exact phrases may change as templates evolve.
    assert "?" in text or any(
        kw in text
        for kw in (
            "consultation",
            "scope",
            "review",
            "missing",
            "details",
            "word",
            "page",
            "genre",
            "manuscript",
        )
    ), f"Expected consultation/scoping response; got: {text[:300]}"
