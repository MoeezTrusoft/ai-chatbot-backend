"""Parity between this repo's Sales MCP client and the frozen TS contract.

The wire contract lives in ``erp-contracts/src/mcp/sales-chatbot.js``. The client
mirrors it in Python, so a rename or a re-pointed permission on either side must
fail one of the two suites rather than silently shipping a bot that cannot
authenticate or that asks for a permission Sales never granted (S3.1.3, S3.5.1).
"""

import re
from pathlib import Path

import pytest

from bookcraft.infra.sales_mcp import (
    ACTION_TOOL_MAP,
    CHATBOT_SALES_STAGES,
    CODE_REACTIONS,
    CONSULTATION_TIMEOUT_TOOLS,
    TOOL_PERMISSIONS,
    TOOL_RATE_CLASSES,
)

CONTRACT = (
    Path(__file__).resolve().parents[3] / "erp-contracts" / "src" / "mcp" / "sales-chatbot.js"
)


def _contract_source() -> str:
    if not CONTRACT.is_file():
        pytest.skip(f"cross-repo contract not present at {CONTRACT}")
    return CONTRACT.read_text(encoding="utf-8")


def _block(source: str, opener: str) -> str:
    start = source.index(opener)
    end = source.index("]);", start)
    return source[start:end]


def test_tool_grants_match_the_frozen_contract() -> None:
    block = _block(_contract_source(), "BOT_TO_SALES_GRANTS = Object.freeze([")
    grants = dict(
        re.findall(r"name:\s*'([a-z0-9_]+)',\s*permission:\s*'([a-z0-9_.]+)'", block)
    )
    assert grants, "no grants parsed from the contract"
    assert grants == TOOL_PERMISSIONS


def test_rate_classes_match_the_frozen_contract() -> None:
    block = _block(_contract_source(), "BOT_TO_SALES_GRANTS = Object.freeze([")
    declared = {
        name: rate_class
        for name, rate_class in re.findall(
            r"name:\s*'([a-z0-9_]+)',\s*permission:\s*'[a-z0-9_.]+',\s*rateClass:\s*'(\w+)'",
            block,
        )
        if rate_class == "read"
    }
    assert declared == TOOL_RATE_CLASSES


def test_action_map_matches_the_frozen_contract() -> None:
    source = _contract_source()
    start = source.index("BOT_ACTION_TO_SALES_TOOL = Object.freeze({")
    block = source[start : source.index("});", start)]
    actions = dict(re.findall(r"(\w+):\s*'([a-z0-9_]+)'", block))
    assert actions == ACTION_TOOL_MAP


def test_error_reactions_match_the_frozen_contract() -> None:
    source = _contract_source()
    start = source.index("SALES_CODE_BOT_REACTION = Object.freeze({")
    block = source[start : source.index("});", start)]
    reactions = {
        code: (action, retryable == "true", bot_code)
        for code, action, retryable, bot_code in re.findall(
            r"'(SALES-\d{4})':\s*Object\.freeze\(\{\s*action:\s*'(\w+)',\s*retryable:\s*(\w+),"
            r"\s*code:\s*'(\w+)'\s*\}\)",
            block,
        )
    }
    assert reactions, "no reactions parsed from the contract"
    assert reactions == CODE_REACTIONS


def test_the_stage_vocabulary_matches_the_frozen_map() -> None:
    # S6.2.1: the bot sends its OWN funnel stage on `sales_lead_create` and Sales
    # translates it with the frozen `SALES_STAGE_MAP`. A stage the map does not key
    # would throw inside Sales, so the two sets have to be identical — not merely
    # overlapping.
    source = _contract_source()
    start = source.index("SALES_STAGE_MAP = Object.freeze({")
    block = source[start : source.index("});", start)]
    keys = set(re.findall(r"(\w+):\s*'[A-Za-z]+'", block))
    assert keys, "no stage map parsed from the contract"
    assert keys == set(CHATBOT_SALES_STAGES)


def test_the_consultation_timeout_is_only_used_for_granted_write_tools() -> None:
    # The longer booking timeout must never be applied to a read (it would let a
    # stalled lookup delay the turn) and every tool using it must be callable.
    assert CONSULTATION_TIMEOUT_TOOLS <= set(TOOL_PERMISSIONS)
    assert not CONSULTATION_TIMEOUT_TOOLS & set(TOOL_RATE_CLASSES)
