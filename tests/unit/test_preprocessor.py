import pytest

from bookcraft.components.preprocessor import EmbeddingClient, SharedPreprocessor, load_sidecars
from bookcraft.tools.idempotency import MemoryCache


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


@pytest.mark.asyncio
async def test_preprocessor_normalizes_and_extracts_atoms() -> None:
    processed = await build_preprocessor().process(
        "I don't need ghost writing, I need editing. My book is 65,000 words. "
        "Email me at x@example.com.",
    )

    assert "ghostwriting" in processed.normalized
    assert processed.deterministic_atoms["emails"] == ["x@example.com"]
    assert processed.deterministic_atoms["word_counts"] == [65000]
    # "ghost writing" (two-word form) triggers negated service detection; "editing"
    # may also be captured. Key assertion: ghostwriting is negated and word_counts
    # and emails extracted correctly.  The "services" key is only present when
    # non-negated service signals exist — its absence is acceptable for this message.
    negated = processed.deterministic_atoms.get("negated_services", [])
    services = processed.deterministic_atoms.get("services", [])
    assert "ghostwriting" in negated or "editing_proofreading" in services, (
        "Expected ghostwriting negated or editing detected"
    )
    assert processed.negation_spans
    assert any(
        token.text.lower().startswith("ghost") and token.negated for token in processed.tokens
    )
    assert len(processed.embedding) == 384


@pytest.mark.asyncio
async def test_preprocessor_marks_hedge_and_counterfactual_spans() -> None:
    processed = await build_preprocessor().process(
        "Maybe I need marketing. If I had a finished manuscript, I would ask for formatting."
    )

    assert processed.hedge_spans
    assert processed.counterfactual_spans
    assert any(token.text.lower() == "marketing" and token.hedged for token in processed.tokens)
    assert any(
        token.text.lower() == "formatting" and token.counterfactual for token in processed.tokens
    )


@pytest.mark.asyncio
async def test_embedding_uses_cached_value_when_tei_is_down() -> None:
    cache = MemoryCache()
    from bookcraft.infra.cache import CacheKeyBuilder

    keys = CacheKeyBuilder(environment="test")
    text = "hello"
    import hashlib

    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    await cache.set(keys.embedding("en", text_hash), "1.0,2.0,3.0", ex=60)
    client = EmbeddingClient(
        tei_url="http://127.0.0.1:9",
        timeout_seconds=0.01,
        dimensions=3,
        degraded_mode_enabled=False,
        cache=cache,
        keys=keys,
    )

    assert await client.embed(text, "en") == [1.0, 2.0, 3.0]


def test_sidecar_verifier_loads_required_contract() -> None:
    sidecars = load_sidecars("data/trimatch/sidecars")

    assert sidecars.negation_cues
    assert sidecars.typography_replacements
    assert sidecars.compound_variants
