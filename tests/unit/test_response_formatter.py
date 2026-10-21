from bookcraft.components.response.formatter import ResponseFormatter, strip_dashes


def test_strip_dashes_replaces_em_dash_with_comma() -> None:
    assert strip_dashes("Our rates \u2014 and the timeline \u2014 are flexible.") == (
        "Our rates, and the timeline, are flexible."
    )


def test_strip_dashes_keeps_numeric_ranges_hyphenated() -> None:
    assert strip_dashes("Reply in 1\u20132 sentences.") == "Reply in 1-2 sentences."


def test_strip_dashes_tidies_double_separators() -> None:
    assert strip_dashes("Yes \u2014, that works.") == "Yes, that works."


def test_formatter_never_emits_an_em_dash() -> None:
    formatter = ResponseFormatter(max_bubble_chars=500)

    bubbles = formatter.format(
        "Ghostwriting \u2014 our most popular service \u2014 starts at two weeks. "
        "We reply within 1\u20132 days."
    )

    text = "".join(bubble.text for bubble in bubbles)

    assert "\u2014" not in text
    assert "\u2013" not in text
    assert "1-2 days" in text


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
