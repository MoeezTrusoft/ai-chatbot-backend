from bookcraft.infra.cache import CacheKeyBuilder


def test_cache_keys_are_namespaced() -> None:
    keys = CacheKeyBuilder(environment="test")

    assert keys.thread_state("thread-id") == "bc:test:thread:thread-id:state"
    assert keys.thread_graph("thread-id") == "bc:test:thread:thread-id:graph"
    assert keys.idempotency("idem") == "bc:test:idempotency:idem"
    assert keys.embedding("en", "hash") == "bc:test:embedding:en:hash"
    assert keys.trimatch_active_state() == "bc:test:trimatch:active_state"
