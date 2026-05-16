from bookcraft.components.response.formatter import ResponseFormatter


def test_formatter_trims_leading_rag_sentence_fragment() -> None:
    formatter = ResponseFormatter(max_bubble_chars=500)

    bubbles = formatter.format(
        "ed on the next idea.\nEngagement Models\n\nWe offer three engagement levels."
    )

    assert bubbles
    assert not bubbles[0].text.startswith("ed on")
    assert bubbles[0].text.startswith("Engagement Models")


def test_formatter_converts_flat_markdown_table_to_bullets() -> None:
    formatter = ResponseFormatter(max_bubble_chars=500)

    bubbles = formatter.format(
        "| Model | What it means | Best for | |---|---|---| "
        "| Full Ghostwriting | Writer drafts the manuscript | Idea-stage authors | "
        "| Coaching & Writing | Hybrid collaboration | Authors who draft in bursts |"
    )

    text = "\n".join(bubble.text for bubble in bubbles)

    assert "|---|" not in text
    assert "- Full Ghostwriting:" in text
    assert "- Coaching & Writing:" in text
