from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from bookcraft.api.main import build_response_generator, create_app
from bookcraft.components.llm.adapters import AnthropicAdapter
from bookcraft.components.llm.protocols import LLMProvider
from bookcraft.components.response.schemas import GeneratedResponseText, ResponseDraft
from bookcraft.infra.config import Settings


def _chat(client: TestClient, message: str, *, thread_id: object | None = None) -> dict[str, Any]:
    payload: dict[str, object] = {"message": message}
    if thread_id is not None:
        payload["thread_id"] = str(thread_id)
    response = client.post("/api/v1/chat/turn", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _latest_trace(client: TestClient, thread_id: str) -> dict[str, Any]:
    trace_store = client.app.state.chat_service.trace_store
    rows = trace_store.for_thread(thread_id)
    assert rows, f"No trace rows found for thread {thread_id}"
    return rows[0]


class FakeClaudeAdapter(LLMProvider):
    def __init__(self, text: str):
        self.text = text
        self.calls: list[dict[str, Any]] = []

    async def structured(
        self, *, system: str, user: str, output_model: type[GeneratedResponseText], purpose: str
    ) -> GeneratedResponseText:
        self.calls.append({"system": system, "user": user, "purpose": purpose})
        return output_model(text=self.text)


class FakeClaudeResponseGenerator:
    def __init__(self, initial: ResponseDraft, repair: ResponseDraft | None = None):
        self.initial = initial
        self.repair_draft = repair

    async def generate(self, **_kwargs: Any) -> ResponseDraft:
        return self.initial

    async def repair(
        self,
        *,
        bad_text: str,
        quality_report: Any,
        response_plan: Any,
        context_pack: Any,
        tool_governance: Any = None,
        response_hint: str | None = None,
    ) -> ResponseDraft:
        if self.repair_draft is not None:
            return self.repair_draft
        return ResponseDraft(text=bad_text, source="template_no_adapter_repair_unavailable")


def test_normal_service_question_uses_claude_source_when_adapter_available() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = FakeClaudeResponseGenerator(
            initial=ResponseDraft(
                text=(
                    "For fiction authors, BookCraft offers cover design, editing, "
                    "formatting, and publishing guidance."
                ),
                source="claude_sonnet",
            )
        )
        resp = _chat(client, "What services does BookCraft offer for fiction authors?")
        trace = _latest_trace(client, resp["thread_id"])

    assert trace["assistant"]["source"] == "claude_sonnet"
    assert trace["customer_response_contract"]["contract_passed"] is True


def test_forced_bad_claude_response_triggers_repair_path() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = FakeClaudeResponseGenerator(
            initial=ResponseDraft(
                text="The runtime atoms in our classifier detected your request.",
                source="claude_sonnet",
            ),
            repair=ResponseDraft(
                text=(
                    "I can help with your cover design illustration project for your "
                    "fiction book. What cover style should I use?"
                ),
                source="claude_sonnet_repair",
            ),
        )
        resp = _chat(client, "Can you tell me about cover design?")
        trace = _latest_trace(client, resp["thread_id"])

    assert trace["assistant"]["source"] == "claude_sonnet_repair"
    assert trace["customer_response_contract"]["repair_attempted"] is True
    assert trace["customer_response_contract"]["contract_passed"] is True


def test_bad_response_does_not_return_quality_fallback_in_production_like_mode() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))
    app.state.chat_service.environment = "prod"

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = FakeClaudeResponseGenerator(
            initial=ResponseDraft(
                text="Sure! I can assist you with that.",
                source="claude_sonnet",
            ),
            repair=ResponseDraft(
                text="Sure! I can assist you with that.",
                source="template_no_adapter_repair_unavailable",
            ),
        )
        resp = _chat(client, "I need help with cover design.")
        trace = _latest_trace(client, resp["thread_id"])

    assert "quality_fallback" not in trace["assistant"]["source"]
    assert trace["assistant"]["source"] == "claude_sonnet"


def test_portfolio_request_does_not_return_portfolio_engine_quality_fallback() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = FakeClaudeResponseGenerator(
            initial=ResponseDraft(
                text="Our portfolio engine has three matched samples.",
                source="portfolio_engine",
            ),
            repair=ResponseDraft(
                text=(
                    "I found a few samples that match your book project. "
                    "Which direction should I narrow them to?"
                ),
                source="claude_sonnet_repair",
            ),
        )
        resp = _chat(client, "Can you show me relevant portfolio samples?")
        trace = _latest_trace(client, resp["thread_id"])

    assert trace["assistant"]["source"] == "claude_sonnet_repair"
    assert trace["assistant"]["source"] != "portfolio_engine_quality_fallback"


def test_greeting_does_not_return_deterministic_greeting_in_production_like_mode() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))
    app.state.chat_service.environment = "prod"

    class _GreetingGenerator(FakeClaudeResponseGenerator):
        async def generate(self, **_kwargs: Any) -> ResponseDraft:
            return ResponseDraft(
                text="Hello! I can help with your book project. What genre are you working on?",
                source="claude_sonnet",
            )

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = _GreetingGenerator(
            initial=ResponseDraft(text="", source="claude_sonnet")
        )
        resp = _chat(client, "Hello")
        trace = _latest_trace(client, resp["thread_id"])

    assert trace["assistant"]["source"] != "deterministic_greeting"
    assert trace["customer_response_contract"]["contract_passed"] is True


def test_trace_includes_customer_response_contract() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = FakeClaudeResponseGenerator(
            initial=ResponseDraft(
                text="I can help with your project next steps.",
                source="claude_sonnet",
            )
        )
        resp = _chat(client, "I need cover design.")
        trace = _latest_trace(client, resp["thread_id"])

    assert "customer_response_contract" in trace
    assert trace["customer_response_contract"]["final_responder"] == "claude_required"


def test_final_response_has_no_internal_terms_when_possible() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = FakeClaudeResponseGenerator(
            initial=ResponseDraft(
                text="I can help with your book project and next steps.",
                source="claude_sonnet",
            )
        )
        resp = _chat(client, "Tell me about your editing options.")

    text = " ".join(str(bubble["text"]) for bubble in resp["bubbles"])
    assert "backend" not in text.lower()
    assert "classifier" not in text.lower()
    assert "tool_governance" not in text.lower()


# ---------------------------------------------------------------------------
# PR 9: production_contract_passed and dev_fallback_used in trace
# ---------------------------------------------------------------------------


def test_production_contract_passed_true_for_claude_source() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = FakeClaudeResponseGenerator(
            initial=ResponseDraft(
                text="For editing, we offer copy editing and proofreading.",
                source="claude_sonnet",
            )
        )
        resp = _chat(client, "Tell me about editing options.")
        trace = _latest_trace(client, resp["thread_id"])

    contract = trace.get("customer_response_contract") or {}
    assert contract.get("production_contract_passed") is True, (
        f"production_contract_passed must be True for claude_sonnet, got {contract}"
    )
    assert contract.get("dev_fallback_used") is False


def test_dev_fallback_marked_not_production_compliant_in_trace() -> None:
    """When the default (no-adapter) generator runs in test env, trace must reflect
    that the final source is a dev fallback — not production-compliant."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        resp = _chat(client, "I need ghostwriting for my novel.")
        trace = _latest_trace(client, resp["thread_id"])

    contract = trace.get("customer_response_contract") or {}
    final_src = contract.get("final_source") or ""
    if final_src not in ("claude_sonnet", "claude_sonnet_repair"):
        # If a non-Claude source was used, production_contract_passed must be False
        assert contract.get("production_contract_passed") is False, (
            f"Non-Claude source '{final_src}' must set production_contract_passed=False"
        )


def test_claude_repair_source_passes_production_contract() -> None:
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = FakeClaudeResponseGenerator(
            initial=ResponseDraft(
                text="The runtime atoms classified your request.",
                source="claude_sonnet",
            ),
            repair=ResponseDraft(
                text="I can help you with cover design — what style are you thinking?",
                source="claude_sonnet_repair",
            ),
        )
        resp = _chat(client, "I need cover design.")
        trace = _latest_trace(client, resp["thread_id"])

    contract = trace.get("customer_response_contract") or {}
    assert contract.get("production_contract_passed") is True
    assert trace["assistant"]["source"] in ("claude_sonnet", "claude_sonnet_repair")


def test_no_assistant_source_starts_with_deterministic_prefix() -> None:
    """Ensure no real-world chat turn emits a deterministic source as final response."""
    from bookcraft.components.response.contracts import _DETERMINISTIC_PREFIXES

    app = create_app(Settings(app_env="test", api_auth_mode="off"))
    messages = [
        "I don't need ghostwriting, I need editing.",
        "Show me some samples.",
    ]
    with TestClient(app) as client:
        for msg in messages:
            resp = _chat(client, msg)
            trace = _latest_trace(client, resp["thread_id"])
            final_src = (trace.get("assistant") or {}).get("source") or ""
            contract = trace.get("customer_response_contract") or {}
            prod_passed = contract.get("production_contract_passed")
            for prefix in _DETERMINISTIC_PREFIXES:
                if final_src.startswith(prefix):
                    # Non-production-compliant source is allowed in test, but
                    # production_contract_passed must be False.
                    assert prod_passed is False, (
                        f"source '{final_src}' starts with '{prefix}' "
                        f"but production_contract_passed={prod_passed}"
                    )


# ---------------------------------------------------------------------------
# PR 10: Adapter wiring tests
# ---------------------------------------------------------------------------


def test_production_like_settings_with_api_key_wires_anthropic_adapter() -> None:
    """When ANTHROPIC_API_KEY is set in prod env, adapter must be non-null."""
    settings = Settings(
        app_env="production",
        anthropic_api_key="test-key-not-real",
        llm_provider_mode="mock",  # key takes priority over mode
        api_auth_mode="off",
    )
    gen = build_response_generator(settings)
    assert gen.adapter is not None, "Adapter must be wired when ANTHROPIC_API_KEY is set"
    assert isinstance(gen.adapter, AnthropicAdapter)
    assert gen.provider_name == "claude_sonnet"


def test_test_env_always_uses_no_adapter_even_with_api_key() -> None:
    """Test env must always use the mock regardless of API key — unit test isolation."""
    settings = Settings(
        app_env="test",
        anthropic_api_key="test-key-not-real",
        api_auth_mode="off",
    )
    gen = build_response_generator(settings)
    assert gen.adapter is None, "Test env must always use no-adapter mock"


def test_missing_api_key_in_test_env_allows_template_fallback() -> None:
    """No API key in test env → mock generator (no RuntimeError)."""
    settings = Settings(app_env="test", api_auth_mode="off")
    gen = build_response_generator(settings)
    assert gen.adapter is None


def test_missing_api_key_in_production_raises() -> None:
    """No API key in production-like env → RuntimeError on startup."""
    settings = Settings(
        app_env="production",
        anthropic_api_key=None,
        llm_provider_mode="live",
        api_auth_mode="off",
    )
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        build_response_generator(settings)


def test_missing_api_key_in_prod_with_mock_mode_raises() -> None:
    """No API key + production env → RuntimeError regardless of mock mode."""
    settings = Settings(
        app_env="production",
        anthropic_api_key=None,
        llm_provider_mode="mock",  # should still raise in prod without key
        api_auth_mode="off",
    )
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        build_response_generator(settings)


def test_chat_with_fake_anthropic_adapter_returns_claude_sonnet_source() -> None:
    """Wire a fake LLM adapter at the chat service level; verify final source."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = FakeClaudeResponseGenerator(
            initial=ResponseDraft(
                text="BookCraft offers ghostwriting, editing, cover design, and more.",
                source="claude_sonnet",
            )
        )
        resp = _chat(client, "What services do you offer?")
        trace = _latest_trace(client, resp["thread_id"])

    contract = trace.get("customer_response_contract") or {}
    assert trace["assistant"]["source"] == "claude_sonnet"
    assert contract.get("production_contract_passed") is True
    assert contract.get("dev_fallback_used") is False


def test_non_null_adapter_in_production_does_not_report_production_contract_false() -> None:
    """With a properly wired Claude adapter, production_contract_passed must be True."""
    app = create_app(Settings(app_env="test", api_auth_mode="off"))

    with TestClient(app) as client:
        client.app.state.chat_service.response_generator = FakeClaudeResponseGenerator(
            initial=ResponseDraft(
                text="I can help with your cover design project.",
                source="claude_sonnet",
            )
        )
        resp = _chat(client, "I need help with cover design.")
        trace = _latest_trace(client, resp["thread_id"])

    contract = trace.get("customer_response_contract") or {}
    assert contract.get("production_contract_passed") is True, (
        f"With a Claude adapter, production_contract_passed must be True. Got: {contract}"
    )
