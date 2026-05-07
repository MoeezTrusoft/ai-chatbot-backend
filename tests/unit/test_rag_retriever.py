from bookcraft.components.rag.retriever import reciprocal_rank_fusion


def test_rrf_combines_result_order_deterministically() -> None:
    bm25 = {"a": {}, "b": {}, "c": {}}
    vector = {"b": {}, "d": {}, "a": {}}

    ranked = reciprocal_rank_fusion([bm25, vector], top_k=3)

    assert [item[0] for item in ranked] == ["b", "a", "d"]

