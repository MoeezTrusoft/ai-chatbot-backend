from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "data" / "verify_rag_source_metadata.py"


def load_verifier_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "verify_rag_source_metadata",
        SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_rag_source_metadata_verifier_accepts_valid_source(tmp_path: Path) -> None:
    verifier = load_verifier_module()

    source = tmp_path / "ghostwriting.md"
    source.write_text(
        """---
title: Ghostwriting FAQ
source_id: ghostwriting_faq
service_category: ghostwriting
section: faq
content_version: v1
allowed_for_response: true
tags: [ghostwriting, faq]
---

Ghostwriting helps authors turn ideas into structured manuscripts.
""",
        encoding="utf-8",
    )

    report = verifier.build_report(source_dir=tmp_path)
    summary = report["summary"]

    assert summary["valid"] is True
    assert summary["ready_for_indexing"] is True
    assert summary["source_file_count"] == 1
    assert summary["error_count"] == 0


def test_rag_source_metadata_verifier_reports_missing_fields(tmp_path: Path) -> None:
    verifier = load_verifier_module()

    source = tmp_path / "bad.md"
    source.write_text(
        """---
title:
service_category: wrong_service
---

""",
        encoding="utf-8",
    )

    report = verifier.build_report(source_dir=tmp_path)
    summary = report["summary"]
    codes = {issue["code"] for issue in report["issues"]}

    assert summary["valid"] is True
    assert summary["ready_for_indexing"] is False
    assert summary["error_count"] >= 1
    assert "required_field_missing" in codes
    assert "invalid_service_category" in codes
    assert "empty_content" in codes


def test_rag_source_metadata_verifier_reports_duplicate_source_id(
    tmp_path: Path,
) -> None:
    verifier = load_verifier_module()

    for name in ["one.md", "two.md"]:
        (tmp_path / name).write_text(
            """---
title: Duplicate
source_id: duplicate_id
service_category: ghostwriting
section: faq
content_version: v1
---

Content.
""",
            encoding="utf-8",
        )

    report = verifier.build_report(source_dir=tmp_path)
    codes = {issue["code"] for issue in report["issues"]}

    assert report["summary"]["ready_for_indexing"] is False
    assert "duplicate_source_id" in codes
