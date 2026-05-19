from bookcraft.components.context.schemas import ContextPack
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.response.contracts import CustomerResponseContract
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
