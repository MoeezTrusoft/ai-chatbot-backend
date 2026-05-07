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
