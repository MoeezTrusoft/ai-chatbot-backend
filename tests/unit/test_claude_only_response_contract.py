from bookcraft.components.context.schemas import ContextPack
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.response.contracts import (
    CustomerResponseContract,
    is_deterministic_source,
    is_production_compliant_source,
    is_production_like,
)
from bookcraft.components.response.planner import ResponsePlan
from bookcraft.components.response.quality_gate import ResponseQualityGate
from bookcraft.components.response.style_policy import ResponseStylePolicy
from bookcraft.domain.enums import QueryIntentType
from bookcraft.domain.state import ThreadState


def test_contract_allows_claude_sonnet() -> None:
    contract = CustomerResponseContract()
    assert contract.is_allowed_final_source("claude_sonnet", app_env="prod")


def test_contract_allows_claude_sonnet_repair() -> None:
    contract = CustomerResponseContract()
    assert contract.is_allowed_final_source("claude_sonnet_repair", app_env="prod")


def test_contract_blocks_template_final_source_in_production() -> None:
    contract = CustomerResponseContract()
    assert not contract.is_allowed_final_source("template_no_adapter", app_env="prod")


def test_contract_blocks_deterministic_greeting_in_production() -> None:
    contract = CustomerResponseContract()
    assert not contract.is_allowed_final_source("deterministic_greeting", app_env="prod")


def test_contract_allows_dev_fallback_only_in_test_env() -> None:
    contract = CustomerResponseContract()
    assert contract.is_allowed_final_source("template_no_adapter", app_env="test")
    assert contract.is_allowed_final_source("deterministic_greeting", app_env="test")


def test_quality_failure_requests_claude_repair_context_not_hardcoded_fallback() -> None:
    quality_gate = ResponseQualityGate(style_policy=ResponseStylePolicy.default())
    quality_report = quality_gate.evaluate(
        text="The runtime atoms in our classifier detected your request.",
        intent=IntentVote(
            query_primary=QueryIntentType.SERVICE_QUESTION,
            service_primary=None,
            funnel_stage=None,
            confidence=1.0,
            needs_clarification=False,
            rationale="test",
            evidence=["test"],
        ),
        state=ThreadState(),
        context_pack=ContextPack(
            known_facts=[],
            missing_facts=[],
            forbidden_reasks=["genre"],
            allowed_next_questions=["cover_style"],
            active_service="cover_design_illustration",
            active_genre="fiction",
            manuscript_status="finished",
        ),
        response_plan=ResponsePlan(
            acknowledge_facts=["service:cover_design_illustration"],
            next_question="What cover style should I use?",
        ),
    )

    assert not quality_report.passed
    assert quality_report.safe_repair_context is not None
    assert quality_report.safe_repair_context["repair_goal"]
    assert "must_not_ask" in quality_report.safe_repair_context
    assert "next_question" in quality_report.safe_repair_context
    assert isinstance(quality_report.safe_repair_context["must_not_ask"], list)


# ---------------------------------------------------------------------------
# PR 9: Hardened contract tests
# ---------------------------------------------------------------------------


def test_contract_blocks_template_quality_fallback_in_production() -> None:
    contract = CustomerResponseContract()
    assert not contract.is_allowed_final_source(
        "template_no_adapter_quality_fallback", app_env="prod"
    )


def test_contract_blocks_any_non_claude_quality_fallback() -> None:
    contract = CustomerResponseContract()
    for source in (
        "template_no_adapter_quality_fallback",
        "deterministic_mixed_request_guard_quality_fallback",
        "clarification_quality_fallback",
        "portfolio_engine_quality_fallback",
    ):
        assert not contract.is_allowed_final_source(source, app_env="prod"), (
            f"{source} must be blocked in production"
        )


def test_contract_marks_dev_fallback_not_production_compliant() -> None:
    contract = CustomerResponseContract()
    # Allowed in test env but NOT production-compliant
    assert contract.is_allowed_final_source("template_no_adapter_quality_fallback", app_env="test")
    assert not contract.is_production_compliant_source("template_no_adapter_quality_fallback")


def test_contract_passes_claude_repair() -> None:
    contract = CustomerResponseContract()
    assert contract.is_allowed_final_source("claude_sonnet_repair", app_env="prod")
    assert contract.is_production_compliant_source("claude_sonnet_repair")


def test_is_deterministic_source_template() -> None:
    assert is_deterministic_source("template_no_adapter") is True
    assert is_deterministic_source("template_no_adapter_quality_fallback") is True


def test_is_deterministic_source_deterministic_prefix() -> None:
    assert is_deterministic_source("deterministic_mixed_request_guard") is True
    assert is_deterministic_source("deterministic_greeting") is True


def test_is_deterministic_source_portfolio_engine() -> None:
    assert is_deterministic_source("portfolio_engine_quality_fallback") is True


def test_is_deterministic_source_quality_fallback() -> None:
    assert is_deterministic_source("some_other_source_quality_fallback") is True
    # Claude repair quality fallback is NOT deterministic
    assert is_deterministic_source("claude_sonnet_repair") is False


def test_is_production_compliant_source() -> None:
    assert is_production_compliant_source("claude_sonnet") is True
    assert is_production_compliant_source("claude_sonnet_repair") is True
    assert is_production_compliant_source("template_no_adapter") is False
    assert is_production_compliant_source("template_no_adapter_quality_fallback") is False


def test_is_production_like() -> None:
    assert is_production_like(None) is True
    assert is_production_like("prod") is True
    assert is_production_like("staging") is True
    assert is_production_like("test") is False
    assert is_production_like("dev") is False
    assert is_production_like("local") is False
