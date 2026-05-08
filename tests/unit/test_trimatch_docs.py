from pathlib import Path


def test_d081_adr_exists() -> None:
    text = Path("docs/adr/D-081-trimatch-funnel-stage-shadow.md").read_text(encoding="utf-8")

    assert "Tri-Match now classifies three dimensions" in text
    assert "Decision Layer weight `0`" in text


def test_current_docs_do_not_contain_old_implementation_prohibition() -> None:
    implementation = Path(
        "docs/implementation/bookcraft_ai_chatbot_ultimate_implementation_guide.md"
    ).read_text(encoding="utf-8")
    architecture = Path("docs/architecture/architecture-reference.md").read_text(encoding="utf-8")

    assert "Tri-Match must **not** classify funnel stage" not in implementation
    assert "Tri-Match only query/service; Funnel Signal only stage" not in implementation
    assert "Tri-Match does NOT classify funnel stage (LLM-only)" not in architecture
