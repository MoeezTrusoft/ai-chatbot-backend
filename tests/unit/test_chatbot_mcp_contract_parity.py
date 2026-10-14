"""Parity between this server and the frozen TS contract (S3.6.1, S3.6.3).

`erp-contracts/src/mcp/chatbot.js` is the manifest a caller caches and an
operator grants against. The server mirrors it, so a rename, a re-pointed
permission or a retargeted tool on either side must fail one of the two suites.

The contract is read by asking Node for the parsed module (the same way the
contract suite does), and the test skips where Node is unavailable rather than
asserting a hand-copied constant.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from bookcraft.components.mcp import (
    CHATBOT_MCP_TOOLS,
    CONTRACT_VERSION,
    PERMISSION_MAP,
    PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    TOOL_NAMES,
)

CONTRACT = (
    Path(__file__).resolve().parents[3] / "erp-contracts" / "src" / "mcp" / "chatbot.js"
)

EXTRACT = """
import * as c from '%s';
const byName = Object.fromEntries(
  c.CHATBOT_MCP_TOOLS.map((t) => [
    t.name,
    {
      permission: t.permission,
      rateClass: t.rateClass,
      required: t.inputSchema.required ?? [],
      passThrough: t.inputSchema.additionalProperties === true,
      target: t.target,
      auditArgs: t.auditArgs,
    },
  ]),
);
process.stdout.write(JSON.stringify({
  version: c.CHATBOT_MCP_CONTRACT_VERSION,
  protocol: c.CHATBOT_MCP_PROTOCOL_VERSION,
  serverName: c.CHATBOT_MCP_SERVER_NAME,
  serverVersion: c.CHATBOT_MCP_SERVER_VERSION,
  tools: c.CHATBOT_MCP_TOOL_NAMES,
  byName,
}));
"""


def _contract() -> dict:
    if not CONTRACT.is_file():
        pytest.skip(f"cross-repo contract not present at {CONTRACT}")
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to read the frozen TS contract")
    completed = subprocess.run(  # noqa: S603 - fixed repo contract module under test.
        [node, "--input-type=module", "-e", EXTRACT % CONTRACT.as_uri()],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip(f"could not load the contract module: {completed.stderr.strip()[:200]}")
    return json.loads(completed.stdout)


def test_versions_and_identity_match_the_frozen_contract() -> None:
    contract = _contract()
    assert contract["version"] == CONTRACT_VERSION
    assert contract["protocol"] == PROTOCOL_VERSION
    assert contract["serverName"] == SERVER_NAME
    assert contract["serverVersion"] == SERVER_VERSION


def test_the_tool_set_matches_the_frozen_contract() -> None:
    contract = _contract()
    assert list(TOOL_NAMES) == list(contract["tools"])
    assert len(CHATBOT_MCP_TOOLS) == len(contract["tools"])


def test_permissions_rate_classes_and_required_args_match() -> None:
    contract = _contract()
    for tool in CHATBOT_MCP_TOOLS:
        declared = contract["byName"][tool.name]
        assert tool.permission == declared["permission"], tool.name
        assert PERMISSION_MAP[tool.name] == declared["permission"], tool.name
        assert tool.rate_class == declared["rateClass"], tool.name
        assert sorted(tool.required_arguments) == sorted(declared["required"]), tool.name
        # A pass-through schema is exactly one that lets the registry tool own its
        # inputs; anything else must declare its properties.
        assert (tool.input_schema.get("additionalProperties") is True) == declared[
            "passThrough"
        ], tool.name


def test_each_tool_targets_the_same_in_process_capability() -> None:
    contract = _contract()
    for tool in CHATBOT_MCP_TOOLS:
        target = contract["byName"][tool.name]["target"]
        assert target["kind"] == tool.target_kind, tool.name
        if target["kind"] == "tool_registry":
            assert target["tool"] == tool.target, tool.name
        if target["kind"] == "tool_registry_by_arg":
            assert target["argument"] == tool.target_argument, tool.name
            assert target["tools"] == dict(tool.target_map), tool.name
        if target["kind"] == "chat_service":
            assert target["operation"] == tool.target, tool.name
