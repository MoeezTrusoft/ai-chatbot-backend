from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "data" / "apply_rag_source_frontmatter_repairs.py"


def load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "apply_rag_source_frontmatter_repairs",
        SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_repair_content_prepends_front_matter() -> None:
    module = load_module()
    proposed = """---
title: Ghostwriting
source_id: ghostwriting
service_category: ghostwriting
section: overview
content_version: v1
allowed_for_response: true
tags: [ghostwriting, overview, rag]
---"""

    repaired, status = module.repair_content(
        "# Ghostwriting\n\nContent.",
        proposed,
    )

    assert status == "changed"
    assert repaired.startswith(proposed)
    assert "# Ghostwriting" in repaired


def test_repair_content_replaces_existing_front_matter() -> None:
    module = load_module()
    proposed = """---
title: New
source_id: new
service_category: ghostwriting
section: overview
content_version: v1
allowed_for_response: true
tags: [ghostwriting, overview, rag]
---"""

    repaired, status = module.repair_content(
        """---
title: Old
---

Body.
""",
        proposed,
    )

    assert status == "changed"
    assert "title: New" in repaired
    assert "title: Old" not in repaired
    assert "Body." in repaired


def test_repair_content_is_idempotent_when_block_matches() -> None:
    module = load_module()
    proposed = """---
title: Same
source_id: same
service_category: ghostwriting
section: overview
content_version: v1
allowed_for_response: true
tags: [ghostwriting, overview, rag]
---"""

    raw = proposed + "\n\nBody.\n"

    repaired, status = module.repair_content(raw, proposed)

    assert status == "already_ok"
    assert repaired == raw
