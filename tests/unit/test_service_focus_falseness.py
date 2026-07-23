"""Regression coverage for MED-tier false service-focus extractions.

The deterministic service-focus detector (SERVICE_KEYWORDS + `_service_mentions`
in the preprocessor, surfaced downstream as the `deterministic_service_focus.v1`
extraction) used to scope a service on a bare ambiguous keyword sitting in
unrelated prose:

    "I bought a book on Amazon"       -> publishing_distribution
    "planning a rocket launch party"  -> marketing_promotion
    "it's set in a trailer park"      -> video_trailer
    "I found you via your website"    -> author_website
    "the narration is first person"   -> audiobook_production
    "the layout of my argument"       -> interior_formatting

Each is now suppressed while the genuine service request is preserved. These
tests exercise the real preprocessor path (the same `services` atom that
`_explicit_services_from_processed` reads).
"""

import pytest

from bookcraft.components.preprocessor import (
    EmbeddingClient,
    SharedPreprocessor,
    load_sidecars,
)


def build_preprocessor() -> SharedPreprocessor:
    return SharedPreprocessor(
        sidecars=load_sidecars("data/trimatch/sidecars"),
        embedding_client=EmbeddingClient(
            tei_url="http://127.0.0.1:9",
            timeout_seconds=0.01,
            dimensions=384,
            degraded_mode_enabled=True,
        ),
    )


async def _services(text: str) -> list[str]:
    processed = await build_preprocessor().process(text)
    return list(processed.deterministic_atoms.get("services", []))


# ---------------------------------------------------------------------------
# False positives — the ambiguous keyword must NOT scope the service.
# ---------------------------------------------------------------------------

FALSE_POSITIVES = [
    ("I bought a book on Amazon", "publishing_distribution"),
    ("planning a rocket launch party", "marketing_promotion"),
    ("it's set in a trailer park", "video_trailer"),
    ("I found you via your website", "author_website"),
    ("the narration is first person", "audiobook_production"),
    ("the layout of my argument", "interior_formatting"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("text", "service"), FALSE_POSITIVES)
async def test_ambiguous_keyword_does_not_mis_scope_service(text: str, service: str) -> None:
    services = await _services(text)
    assert service not in services, (
        f"{text!r} should not scope {service!r}; got {services}"
    )


# ---------------------------------------------------------------------------
# True positives — a genuine service request must STILL scope correctly.
# ---------------------------------------------------------------------------

TRUE_POSITIVES = [
    ("I need a book trailer", "video_trailer"),
    ("can you help with marketing / a book launch campaign", "marketing_promotion"),
    ("I want to publish and distribute on Amazon and Kobo", "publishing_distribution"),
    ("I need an author website", "author_website"),
    ("I want an audiobook / a narrator", "audiobook_production"),
    ("I need interior formatting / layout for my book", "interior_formatting"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("text", "service"), TRUE_POSITIVES)
async def test_genuine_service_request_is_preserved(text: str, service: str) -> None:
    services = await _services(text)
    assert service in services, (
        f"{text!r} should still scope {service!r}; got {services}"
    )


@pytest.mark.asyncio
async def test_book_launch_preparation_still_scopes_marketing() -> None:
    # Guards suppress physical launches ("rocket launch party") but must keep a
    # genuine book "launch preparation" as marketing.
    services = await _services(
        "I need a professional cover, publishing setup, and some basic launch preparation."
    )
    assert "marketing_promotion" in services
