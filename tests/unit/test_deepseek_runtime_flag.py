import pytest

from bookcraft.api.main import build_intent_classifier
from bookcraft.infra.config import Settings


def _provider_names(settings: Settings) -> list[str]:
    classifier = build_intent_classifier(settings)
    return [provider.name for provider in classifier.providers]


def test_deepseek_intent_provider_is_disabled_by_default_in_live_mode() -> None:
    names = _provider_names(
        Settings(
            app_env="production",
            llm_provider_mode="live",
            anthropic_api_key="test-anthropic-key",
            openai_api_key="test-openai-key",
        )
    )

    assert names == ["claude_haiku", "openai_gpt_5_4_mini"]


def test_deepseek_intent_provider_can_be_enabled_explicitly() -> None:
    names = _provider_names(
        Settings(
            app_env="production",
            llm_provider_mode="live",
            anthropic_api_key="test-anthropic-key",
            openai_api_key="test-openai-key",
            deepseek_api_key="test-deepseek-key",
            deepseek_intent_enabled=True,
        )
    )

    assert names == ["claude_haiku", "openai_gpt_5_4_mini", "deepseek_v3"]


def test_deepseek_enabled_requires_api_key() -> None:
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        build_intent_classifier(
            Settings(
                app_env="production",
                llm_provider_mode="live",
                anthropic_api_key="test-anthropic-key",
                openai_api_key="test-openai-key",
                deepseek_api_key=None,
                deepseek_intent_enabled=True,
            )
        )
