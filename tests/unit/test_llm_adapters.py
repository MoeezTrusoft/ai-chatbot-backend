import pytest
from pydantic import BaseModel

from bookcraft.components.llm import (
    AnthropicAdapter,
    DeepSeekAdapter,
    MockLLMAdapter,
    OpenAIAdapter,
)


class EmptyOutput(BaseModel):
    pass


class NameOutput(BaseModel):
    name: str


@pytest.mark.asyncio
async def test_mock_llm_adapter_validates_structured_output() -> None:
    result = await MockLLMAdapter().structured(
        system="system",
        user="user",
        output_model=EmptyOutput,
        purpose="intent",
    )

    assert isinstance(result, EmptyOutput)


def test_live_adapters_are_constructible_without_network_calls() -> None:
    assert AnthropicAdapter(api_key="key", base_url="https://example.com", timeout_seconds=1).name
    assert OpenAIAdapter(api_key="key", base_url="https://example.com", timeout_seconds=1).name
    assert DeepSeekAdapter(api_key="key", base_url="https://example.com", timeout_seconds=1).name


def test_anthropic_structured_response_parses_content_text() -> None:
    from bookcraft.components.llm.adapters import _parse_structured_response

    raw = '{"content":[{"type":"text","text":"{\\"name\\":\\"avery\\"}"}]}'

    result = _parse_structured_response(raw, NameOutput)

    assert result == NameOutput(name="avery")


def test_anthropic_structured_response_extracts_json_from_text_wrapper() -> None:
    from bookcraft.components.llm.adapters import _parse_structured_response

    raw = (
        '{"content":[{"type":"text","text":"Here is the JSON you requested:\\n'
        '{\\"name\\":\\"avery\\"}"}]}'
    )

    result = _parse_structured_response(raw, NameOutput)

    assert result == NameOutput(name="avery")
