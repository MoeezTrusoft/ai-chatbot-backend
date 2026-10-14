"""The public chat surface may not drift as a side effect of the CRM work (S11.7.1).

The Sales/website integration reaches into the chatbot through the chat API. That
invites edits to the schemas a widget parses, or to the prompts and policies that
decide what the assistant may say. Both were previously protected by review only.

Two guards live here:

* **Additive-only schemas.** Every field the frozen request/response and bubble
  schemas expose must still exist with the same required/optional status, and a
  newly added field must be optional. Removing a field, tightening a default or
  making a new field mandatory is a breaking change for Sales and the widget.
* **Byte-frozen prompts and policies.** The files that carry the persona, the
  style policy, the quality gate, the response contract and the input guard are
  hashed into ``tests/fixtures/public-chat-surface.json``. A change fails this
  test until the digest is regenerated on purpose::

      UPDATE_PUBLIC_SURFACE=1 .venv/bin/python -m pytest \\
          tests/unit/test_public_chat_surface_freeze.py

  Regenerating is a deliberate act, so the digest update shows up as its own
  reviewed diff instead of hiding inside an unrelated change.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from pydantic import BaseModel

from bookcraft.components.response.chat_schemas import ChatTurnRequest, ChatTurnResponse
from bookcraft.components.response.schemas import FormattedBubble

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "tests" / "fixtures" / "public-chat-surface.json"

# The whole surface: nothing may be added to it or dropped from it without this
# list changing too, so a new prompt file cannot slip past the digest guard.
FROZEN_FILES = (
    "src/bookcraft/components/persona/representative.py",
    "src/bookcraft/components/response/chat_schemas.py",
    "src/bookcraft/components/response/contracts.py",
    "src/bookcraft/components/response/generator.py",
    "src/bookcraft/components/response/quality_gate.py",
    "src/bookcraft/components/response/schemas.py",
    "src/bookcraft/components/response/style_policy.py",
    "src/bookcraft/components/safety/input_guard.py",
)

# field name → required (no default). Frozen: see the module docstring.
FROZEN_REQUEST_FIELDS = {
    "thread_id": False,
    "customer_id": False,
    "tenant_id": False,
    "site_id": False,
    "message": False,
    "correlation_id": False,
    "attachments": False,
    "landing_page": False,
    "landing_keyword": False,
    "turn_token": False,
}
FROZEN_RESPONSE_FIELDS = {
    "thread_id": True,
    "bubbles": True,
    "intent": True,
    "language_status": True,
    "debug_event_ids": False,
    "blocked": False,
    "input_disabled": False,
    "system_message": False,
    "action_events": False,
}
FROZEN_BUBBLE_FIELDS = {
    "text": True,
    "bubble_index": True,
    "rich_segments": False,
}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest() -> dict[str, str]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["files"]


def _regenerate(files: dict[str, str]) -> None:
    payload = {
        "note": "Digests of the public chat surface (S11.7.1). A change here must be deliberate: "
        "the Sales/website integration is additive-only, so these files are expected to stay "
        "byte-identical. Regenerate with UPDATE_PUBLIC_SURFACE=1 when a change is intended and "
        "reviewed.",
        "files": dict(sorted(files.items())),
    }
    MANIFEST.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _assert_fields_frozen(model: type[BaseModel], frozen: dict[str, bool]) -> None:
    fields = model.model_fields
    assert fields, f"{model.__name__} exposes no fields"
    for name, required in frozen.items():
        assert name in fields, (
            f"{model.__name__}.{name} was removed. The chat API is a published surface: "
            "the integration may only ADD optional fields."
        )
        assert fields[name].is_required() is required, (
            f"{model.__name__}.{name} changed its required/optional status. Sales and the widget "
            "depend on the published shape; make the new behaviour additive instead."
        )
    # Additive only: a field nobody has agreed on yet must be optional, so an
    # existing caller keeps working without changes.
    added = sorted(set(fields) - set(frozen))
    for name in added:
        assert not fields[name].is_required(), (
            f"{model.__name__}.{name} is a new REQUIRED field, which breaks every existing "
            "caller. Give it a default or make it optional."
        )


def test_public_chat_schemas_only_gain_optional_fields() -> None:
    _assert_fields_frozen(ChatTurnRequest, FROZEN_REQUEST_FIELDS)
    _assert_fields_frozen(ChatTurnResponse, FROZEN_RESPONSE_FIELDS)
    _assert_fields_frozen(FormattedBubble, FROZEN_BUBBLE_FIELDS)


def test_public_schemas_still_reject_unknown_input() -> None:
    # `extra="forbid"` is what stops a caller from smuggling fields into the chat
    # API; dropping it for the integration would silently widen the contract.
    for model in (ChatTurnRequest, ChatTurnResponse, FormattedBubble):
        assert model.model_config.get("extra") == "forbid", f"{model.__name__} accepts extra fields"


def test_the_manifest_covers_the_whole_frozen_surface() -> None:
    manifest = _manifest()
    assert sorted(manifest) == sorted(FROZEN_FILES)
    for relative in FROZEN_FILES:
        assert (REPO_ROOT / relative).is_file(), f"frozen file is missing: {relative}"


def test_prompts_and_policies_are_byte_identical() -> None:
    manifest = _manifest()
    if os.environ.get("UPDATE_PUBLIC_SURFACE") == "1":
        _regenerate({relative: _digest(REPO_ROOT / relative) for relative in FROZEN_FILES})
        pytest.skip("public chat surface manifest regenerated")

    changed = [
        relative
        for relative, digest in manifest.items()
        if _digest(REPO_ROOT / relative) != digest
    ]
    assert not changed, (
        "the public chat surface changed:\n  "
        + "\n  ".join(changed)
        + "\nIf the change is deliberate and reviewed, regenerate the manifest with "
        "UPDATE_PUBLIC_SURFACE=1 and commit it together with the new content."
    )
