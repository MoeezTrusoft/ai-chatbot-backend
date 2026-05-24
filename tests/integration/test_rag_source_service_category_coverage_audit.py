from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "data" / "audit_rag_source_service_category_coverage.py"


def load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "audit_rag_source_service_category_coverage",
        SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_coverage_audit_passes_current_source_corpus() -> None:
    module = load_module()

    report = module.build_report(
        source_dir=ROOT / "data" / "rag-corpus" / "source_markdown",
    )

    assert report["summary"]["valid"] is True
    assert report["summary"]["coverage_passed"] is True
    assert report["summary"]["error_count"] == 0
    assert report["summary"]["source_file_count"] == 28


def test_coverage_audit_detects_wrong_service_category(tmp_path: Path) -> None:
    module = load_module()

    source = tmp_path / "ghostwriting.md"
    source.write_text(
        """---
title: Ghostwriting at BookCraft
source_id: ghostwriting
service_category: marketing_promotion
section: overview
content_version: v1
allowed_for_response: true
tags: [marketing_promotion, overview, rag]
---

Ghostwriting content.
""",
        encoding="utf-8",
    )

    report = module.build_report(source_dir=tmp_path)

    assert report["summary"]["coverage_passed"] is False
    assert any(
        issue["code"] == "service_category_mismatch" and issue["path"] == "ghostwriting.md"
        for issue in report["issues"]
    )


def test_coverage_audit_flags_unexpected_source(tmp_path: Path) -> None:
    module = load_module()

    source = tmp_path / "unknown.md"
    source.write_text(
        """---
title: Unknown
source_id: unknown
service_category: ghostwriting
section: overview
content_version: v1
---

Unknown content.
""",
        encoding="utf-8",
    )

    report = module.build_report(source_dir=tmp_path)

    assert report["summary"]["coverage_passed"] is False
    assert any(issue["code"] == "unexpected_source_file" for issue in report["issues"])
