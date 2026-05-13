from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "data" / "build_rag_elasticsearch_index.py"


def load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "build_rag_elasticsearch_index",
        SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_chunk_text_splits_long_content() -> None:
    module = load_module()
    text = " ".join(["ghostwriting"] * 300)

    chunks = module.chunk_text(text, chunk_size=300, chunk_overlap=50)

    assert len(chunks) > 1
    assert all(chunk for chunk in chunks)


def test_mapping_contains_retriever_fields() -> None:
    module = load_module()

    mapping = module.elasticsearch_mapping(384)
    properties = mapping["mappings"]["properties"]

    for field in [
        "chunk_id",
        "content",
        "content_vector",
        "allowed_for_response",
        "service_category",
        "source_id",
        "title",
        "section",
        "checksum",
    ]:
        assert field in properties

    assert properties["content_vector"]["dims"] == 384


def test_dry_run_builds_chunks_without_externals(tmp_path: Path) -> None:
    module = load_module()

    source_dir = tmp_path / "source_markdown"
    source_dir.mkdir()
    (source_dir / "ghostwriting.md").write_text(
        """---
title: Ghostwriting
source_id: ghostwriting
service_category: ghostwriting
section: overview
content_version: v1
allowed_for_response: true
tags: [ghostwriting, overview, rag]
---

Ghostwriting helps authors turn ideas into complete manuscripts.
""",
        encoding="utf-8",
    )

    settings = module.Settings(rag_source_dir=str(source_dir))
    report = module.build_index_report(
        settings=settings,
        source_dir=source_dir,
        index_name="bookcraft_rag_test",
        chunk_size=1200,
        chunk_overlap=200,
        apply=False,
        swap_alias=False,
    )

    assert report["summary"]["valid"] is True
    assert report["summary"]["apply"] is False
    assert report["summary"]["source_file_count"] == 1
    assert report["summary"]["chunk_count"] == 1
    assert report["summary"]["indexed_count"] == 0
