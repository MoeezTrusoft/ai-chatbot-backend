from bookcraft.components.response import ResponseFormatter


def test_formatter_strips_unsupported_markdown_and_json() -> None:
    bubbles = ResponseFormatter(max_bubble_chars=80).format("# Title\n\n**Hello** there")

    assert bubbles[0].text == "Title"
    assert bubbles[1].text == "Hello there"

    json_bubble = ResponseFormatter().format('{"raw": true}')[0]
    assert "plain language" in json_bubble.text
