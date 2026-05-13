from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from bookcraft.infra.config import Settings

ROOT = Path(__file__).resolve().parents[2]
PLAN_SCRIPT = ROOT / "scripts" / "data" / "build_rag_source_frontmatter_repair_plan.py"


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply reviewed RAG source front matter repairs.")
    parser.add_argument("--source-dir", default=None)
    parser.add_argument("--output-dir", default="reports/rag")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually modify source markdown files. Without this flag, dry-run only.",
    )
    args = parser.parse_args()

    settings = Settings()
    source_dir = Path(args.source_dir or settings.rag_source_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    plan_module = load_plan_module()
    plan = plan_module.build_report(source_dir=source_dir)
    report = apply_plan(
        plan=plan,
        source_dir=source_dir,
        apply=args.apply,
    )

    json_path = output_dir / "rag_source_frontmatter_repair_apply_report.json"
    md_path = output_dir / "rag_source_frontmatter_repair_apply_report.md"

    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(markdown(report), encoding="utf-8")

    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(f"json_report={json_path}")
    print(f"markdown_report={md_path}")

    return 0


def load_plan_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "build_rag_source_frontmatter_repair_plan",
        PLAN_SCRIPT,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load repair plan script: {PLAN_SCRIPT}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def apply_plan(
    *,
    plan: dict[str, Any],
    source_dir: Path,
    apply: bool,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    changed_count = 0
    skipped_count = 0

    for item in plan.get("repair_items", []):
        path = source_dir / str(item["path"])
        proposed_block = str(item["proposed_block"]).strip()
        raw = path.read_text(encoding="utf-8")
        repaired, status = repair_content(raw, proposed_block)

        if status == "changed":
            changed_count += 1
            if apply:
                path.write_text(repaired, encoding="utf-8")
        else:
            skipped_count += 1

        items.append(
            {
                "path": str(item["path"]),
                "status": status if apply else f"dry_run_{status}",
                "confidence": item["confidence"],
                "service_category": item["proposed_front_matter"]["service_category"],
                "section": item["proposed_front_matter"]["section"],
                "source_id": item["proposed_front_matter"]["source_id"],
            }
        )

    summary = {
        "valid": True,
        "generated_at": datetime.now(UTC).isoformat(),
        "apply": apply,
        "source_dir": str(source_dir),
        "repair_item_count": len(items),
        "changed_count": changed_count,
        "skipped_count": skipped_count,
        "safety_note": (
            "This tool only edits RAG source markdown front matter. It does not "
            "create Elasticsearch indices, embed content, bulk index documents, "
            "change aliases, or enable production RAG."
        ),
    }

    return {
        "schema_version": 1,
        "summary": summary,
        "items": items,
    }


def repair_content(raw: str, proposed_block: str) -> tuple[str, str]:
    lines = raw.splitlines()
    if lines and lines[0].strip() == "---":
        end_index = None
        for index, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                end_index = index
                break

        if end_index is None:
            body = "\n".join(lines[1:]).strip()
            repaired = proposed_block + "\n\n" + body + "\n"
            return repaired, "changed"

        existing_block = "\n".join(lines[: end_index + 1]).strip()
        body = "\n".join(lines[end_index + 1 :]).lstrip()
        if existing_block == proposed_block:
            return raw if raw.endswith("\n") else raw + "\n", "already_ok"

        repaired = proposed_block + "\n\n" + body.rstrip() + "\n"
        return repaired, "changed"

    repaired = proposed_block + "\n\n" + raw.lstrip().rstrip() + "\n"
    return repaired, "changed"


def markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# RAG Source Front Matter Repair Apply Report",
        "",
        "## Summary",
        "",
        f"- Generated at: `{summary['generated_at']}`",
        f"- Valid: `{summary['valid']}`",
        f"- Apply: `{summary['apply']}`",
        f"- Source dir: `{summary['source_dir']}`",
        f"- Repair items: `{summary['repair_item_count']}`",
        f"- Changed count: `{summary['changed_count']}`",
        f"- Skipped count: `{summary['skipped_count']}`",
        "",
        "## Safety Note",
        "",
        summary["safety_note"],
        "",
        "## Items",
        "",
        "| File | Status | Source ID | Service | Section | Confidence |",
        "|---|---|---|---|---|---|",
    ]

    for item in report["items"]:
        lines.append(
            "| `{path}` | `{status}` | `{source_id}` | `{service}` | `{section}` | "
            "`{confidence}` |".format(
                path=item["path"],
                status=item["status"],
                source_id=item["source_id"],
                service=item["service_category"],
                section=item["section"],
                confidence=item["confidence"],
            )
        )

    if not report["items"]:
        lines.append("| _none_ |  |  |  |  |  |")

    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
