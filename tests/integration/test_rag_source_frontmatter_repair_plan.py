from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "data" / "build_rag_source_frontmatter_repair_plan.py"


def load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "build_rag_source_frontmatter_repair_plan",
        SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_repair_plan_proposes_front_matter_for_missing_metadata(tmp_path: Path) -> None:
    module = load_module()

    source = tmp_path / "ghostwriting-guide.md"
    source.write_text(
        "# Ghostwriting Guide\n\nGhostwriting helps authors create manuscripts.",
        encoding="utf-8",
    )

    report = module.build_report(source_dir=tmp_path)

    assert report["summary"]["valid"] is True
    assert report["summary"]["repair_item_count"] == 1

    item = report["repair_items"][0]
    proposed = item["proposed_front_matter"]

    assert proposed["title"] == "Ghostwriting Guide"
    assert proposed["source_id"] == "ghostwriting_guide"
    assert proposed["service_category"] == "ghostwriting"
    assert proposed["section"] == "overview"
    assert proposed["content_version"] == "v1"
    assert proposed["allowed_for_response"] == "true"


def test_repair_plan_marks_unknown_service_low_confidence(tmp_path: Path) -> None:
    module = load_module()

    source = tmp_path / "general.md"
    source.write_text("General company information.", encoding="utf-8")

    report = module.build_report(source_dir=tmp_path)

    item = report["repair_items"][0]

    assert item["confidence"] == "low"
    assert "manual review required" in " ".join(item["notes"])


def test_repair_plan_preserves_existing_metadata(tmp_path: Path) -> None:
    module = load_module()

    source = tmp_path / "editing.md"
    source.write_text(
        """---
title: Editing Help
source_id: editing_help
service_category: editing_proofreading
section: faq
content_version: v2
---

Editing and proofreading improve manuscript quality.
""",
        encoding="utf-8",
    )

    report = module.build_report(source_dir=tmp_path)

    proposed = report["repair_items"][0]["proposed_front_matter"]

    assert proposed["title"] == "Editing Help"
    assert proposed["source_id"] == "editing_help"
    assert proposed["service_category"] == "editing_proofreading"
    assert proposed["section"] == "faq"
    assert proposed["content_version"] == "v2"
