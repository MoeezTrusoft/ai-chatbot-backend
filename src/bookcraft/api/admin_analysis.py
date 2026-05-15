from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from bookcraft.components.analysis import LiveTraceStore

router = APIRouter(prefix="/api/admin/analysis", tags=["admin-analysis"])

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = PROJECT_ROOT / "data" / "trimatch"
ACTIVE_RULE_DIR = DATA_ROOT / "rules"
CANDIDATE_ROOT = DATA_ROOT / "candidates" / "rules_army_v2_filtered"
REPORT_ROOT = PROJECT_ROOT / "reports"
LIVE_TRACE_PATH = REPORT_ROOT / "live_traces" / "chat_turns.jsonl"
REVIEW_ROOT = DATA_ROOT / "reviews"
ANALYSIS_CANDIDATE_ROOT = DATA_ROOT / "candidates" / "analysis_console_rules"
ACTIVATION_LOG = DATA_ROOT / "activation_log.jsonl"
CONFIRM_PHRASE = "I_UNDERSTAND_THIS_PROMOTES_RULES_ARMY_V2"


class RuleCandidatePayload(BaseModel):
    id: str | None = None
    title: str = "Untitled rule candidate"
    status: str = "needs_review"
    dimension: str
    target: str
    layer: str
    confidence: float = Field(default=0.9, ge=0, le=1)
    shortcut_allowed: bool = False
    phrases: list[str] | None = None
    regex: str | None = None
    pattern: list[str] | None = None
    semantic_examples: list[str] | None = None
    reason: str = "Created from analysis console"
    source_message: str = ""
    reviewer: str | None = None
    review_note: str | None = None
    collision_warnings: list[dict[str, Any]] = Field(default_factory=list)
    eval_result: dict[str, Any] = Field(default_factory=lambda: {"passed": 0, "failed": 0})


class RuleCandidateUpdate(BaseModel):
    status: str
    review_note: str | None = None
    reviewer: str | None = None


class ActivationRequest(BaseModel):
    confirm_phrase: str
    force: bool = False
    mode: Literal["active", "shadow"] = "active"
    candidate: str = "rules_army_v2_filtered"


class RollbackRequest(BaseModel):
    backup_dir: str


def require_admin(authorization: str | None = Header(default=None)) -> None:
    expected = os.getenv("BOOKCRAFT_ADMIN_ANALYSIS_TOKEN")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="BOOKCRAFT_ADMIN_ANALYSIS_TOKEN is not configured.",
        )
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


@router.get("/health")
def health(_: None = Depends(require_admin)) -> dict[str, Any]:
    return {
        "ok": True,
        "app": "bookcraft-analysis-console-admin-api",
        "mode": os.getenv("APP_ENV", "unknown"),
        "timestamp": datetime.now(UTC).isoformat(),
        "paths": {
            "project_root": str(PROJECT_ROOT),
            "active_rule_dir": str(ACTIVE_RULE_DIR),
            "candidate_root": str(CANDIDATE_ROOT),
            "report_root": str(REPORT_ROOT),
        },
    }


@router.get("/reports/production")
def latest_production_report(_: None = Depends(require_admin)) -> dict[str, Any]:
    return _read_json(
        _latest_existing(
            [
                REPORT_ROOT / "production" / "production_component_performance_report.json",
                REPORT_ROOT / "production_component_performance_report.json",
            ]
        )
    )


@router.get("/reports/trimatch-context")
def trimatch_context_report(_: None = Depends(require_admin)) -> dict[str, Any]:
    return _read_json(
        _latest_existing(
            [
                REPORT_ROOT / "trimatch" / "trimatch_context_candidate_report.json",
                REPORT_ROOT / "trimatch_context_candidate_report.json",
            ]
        )
    )


@router.post("/evals/context-candidate/run")
def run_context_candidate_eval(_: None = Depends(require_admin)) -> dict[str, Any]:
    script = PROJECT_ROOT / "scripts" / "data" / "run_trimatch_context_candidate_report.py"
    if not script.exists():
        raise HTTPException(status_code=404, detail=f"Missing script: {script}")
    _run([sys.executable, str(script)])
    return trimatch_context_report(None)


@router.get("/traces/latest")
def latest_live_traces(
    limit: int = Query(default=50, ge=1, le=500),
    source: str | None = None,
    query_primary: str | None = None,
    service_primary: str | None = None,
    customer_id: str | None = None,
    min_latency_ms: float | None = Query(default=None, ge=0),
    has_forbid_markers: bool | None = None,
    has_negated_terms: bool | None = None,
    _: None = Depends(require_admin),
) -> dict[str, Any]:
    store = LiveTraceStore(LIVE_TRACE_PATH)
    rows = store.latest(limit=500)
    rows = _filter_trace_rows(
        rows,
        source=source,
        query_primary=query_primary,
        service_primary=service_primary,
        customer_id=customer_id,
        min_latency_ms=min_latency_ms,
        has_forbid_markers=has_forbid_markers,
        has_negated_terms=has_negated_terms,
    )
    rows = rows[:limit]
    return {
        "trace_path": str(LIVE_TRACE_PATH.relative_to(PROJECT_ROOT)),
        "count": len(rows),
        "filters": _trace_filter_payload(
            source=source,
            query_primary=query_primary,
            service_primary=service_primary,
            customer_id=customer_id,
            min_latency_ms=min_latency_ms,
            has_forbid_markers=has_forbid_markers,
            has_negated_terms=has_negated_terms,
        ),
        "traces": rows,
    }


@router.get("/traces/{thread_id}")
def thread_live_traces(
    thread_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    source: str | None = None,
    query_primary: str | None = None,
    service_primary: str | None = None,
    customer_id: str | None = None,
    min_latency_ms: float | None = Query(default=None, ge=0),
    has_forbid_markers: bool | None = None,
    has_negated_terms: bool | None = None,
    _: None = Depends(require_admin),
) -> dict[str, Any]:
    store = LiveTraceStore(LIVE_TRACE_PATH)
    rows = store.for_thread(thread_id=thread_id, limit=500)
    rows = _filter_trace_rows(
        rows,
        source=source,
        query_primary=query_primary,
        service_primary=service_primary,
        customer_id=customer_id,
        min_latency_ms=min_latency_ms,
        has_forbid_markers=has_forbid_markers,
        has_negated_terms=has_negated_terms,
    )
    rows = rows[:limit]
    return {
        "trace_path": str(LIVE_TRACE_PATH.relative_to(PROJECT_ROOT)),
        "thread_id": thread_id,
        "count": len(rows),
        "filters": _trace_filter_payload(
            source=source,
            query_primary=query_primary,
            service_primary=service_primary,
            customer_id=customer_id,
            min_latency_ms=min_latency_ms,
            has_forbid_markers=has_forbid_markers,
            has_negated_terms=has_negated_terms,
        ),
        "traces": rows,
    }


@router.get("/rules/active")
def active_rules(_: None = Depends(require_admin)) -> dict[str, Any]:
    return _load_rule_dir(ACTIVE_RULE_DIR)


@router.get("/rules/candidates")
def list_rule_candidates(_: None = Depends(require_admin)) -> dict[str, Any]:
    return {"candidates": _load_candidates()}


@router.post("/rules/candidates")
def create_rule_candidate(
    payload: RuleCandidatePayload, _: None = Depends(require_admin)
) -> dict[str, Any]:
    candidates = _load_candidates()
    item = payload.model_dump()
    item["id"] = item.get("id") or f"AC-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    item["created_at"] = datetime.now(UTC).isoformat()
    candidates = [candidate for candidate in candidates if candidate.get("id") != item["id"]]
    candidates.append(item)
    _save_candidates(candidates)
    _append_audit("rule_candidate_created", item)
    return item


@router.patch("/rules/candidates/{candidate_id}")
def update_rule_candidate(
    candidate_id: str, payload: RuleCandidateUpdate, _: None = Depends(require_admin)
) -> dict[str, Any]:
    candidates = _load_candidates()
    for candidate in candidates:
        if candidate.get("id") == candidate_id:
            candidate.update(payload.model_dump(exclude_none=True))
            candidate["reviewed_at"] = datetime.now(UTC).isoformat()
            _save_candidates(candidates)
            _append_audit("rule_candidate_updated", candidate)
            return candidate
    raise HTTPException(status_code=404, detail="Rule candidate not found")


@router.post("/rules-army-v2/preflight")
def rules_army_preflight(_: None = Depends(require_admin)) -> dict[str, Any]:
    candidate_rule_dir = CANDIDATE_ROOT / "rules"
    candidate_eval_dir = CANDIDATE_ROOT / "eval"
    warnings: list[str] = []
    errors: list[str] = []

    candidate_exists = candidate_rule_dir.exists()
    if not candidate_exists:
        errors.append(f"Missing candidate rule dir: {candidate_rule_dir}")

    verifier_valid = False
    verifier_output = ""
    if candidate_exists:
        command = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "data" / "verify_trimatch_rules.py"),
        ]
        env = os.environ.copy()
        env["TRIMATCH_RULE_DIR"] = str(candidate_rule_dir)
        env["TRIMATCH_EVAL_DIR"] = str(candidate_eval_dir)
        proc = subprocess.run(  # noqa: S603
            command,
            cwd=PROJECT_ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        verifier_valid = proc.returncode == 0
        verifier_output = (proc.stdout + "\n" + proc.stderr).strip()[-6000:]
        if not verifier_valid:
            warnings.append(
                "Formal verifier is red for this candidate. Active promotion requires force=true."
            )

    context_valid = False
    context_summary: dict[str, Any] = {}
    context_path = REPORT_ROOT / "trimatch" / "trimatch_context_candidate_report.json"
    if context_path.exists():
        context = _read_json(context_path)
        context_summary = (
            dict(context.get("summary", {})) if isinstance(context.get("summary"), dict) else {}
        )
        context_valid = int(context_summary.get("failed_count", -1)) == 0
    else:
        warnings.append("Context candidate report not found. Run context eval before promotion.")

    return {
        "candidate": "rules_army_v2_filtered",
        "candidate_exists": candidate_exists,
        "active_rule_count": _count_rules(ACTIVE_RULE_DIR),
        "candidate_rule_count": _count_rules(candidate_rule_dir) if candidate_exists else 0,
        "verifier_valid": verifier_valid,
        "context_report_valid": context_valid,
        "context_summary": context_summary,
        "warnings": warnings,
        "errors": errors,
        "verifier_output": verifier_output,
    }


@router.post("/rules-army-v2/activate")
def activate_rules_army(
    request: ActivationRequest, _: None = Depends(require_admin)
) -> dict[str, Any]:
    if request.confirm_phrase != CONFIRM_PHRASE:
        raise HTTPException(
            status_code=400, detail=f"Confirm phrase must be exactly {CONFIRM_PHRASE}"
        )

    preflight = rules_army_preflight(None)
    if preflight["errors"]:
        raise HTTPException(status_code=400, detail={"preflight": preflight})
    if request.mode == "active" and not preflight["verifier_valid"] and not request.force:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Verifier is red. Pass force=true only after manual approval.",
                "preflight": preflight,
            },
        )

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    if request.mode == "shadow":
        payload = {
            "activated": False,
            "mode": "shadow",
            "message": "Shadow activation recorded only; active rules unchanged.",
            "preflight": preflight,
        }
        _append_audit("rules_army_shadow_recorded", payload)
        return payload

    backup_dir = DATA_ROOT / "backups" / f"{timestamp}_rules"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for path in ACTIVE_RULE_DIR.glob("*.json"):
        shutil.copy2(path, backup_dir / path.name)
        path.unlink()

    copied: list[str] = []
    for path in sorted((CANDIDATE_ROOT / "rules").glob("*.json")):
        target = ACTIVE_RULE_DIR / path.name.replace(".filtered", ".active")
        shutil.copy2(path, target)
        copied.append(str(target.relative_to(PROJECT_ROOT)))

    payload = {
        "activated": True,
        "mode": "active",
        "backup_dir": str(backup_dir.relative_to(PROJECT_ROOT)),
        "copied_files": copied,
        "verifier_valid": preflight["verifier_valid"],
        "message": "Rules Army v2 candidate copied into active rule directory.",
        "preflight": preflight,
    }
    _append_audit("rules_army_activated", payload)
    return payload


@router.post("/rules-army-v2/rollback")
def rollback_rules(request: RollbackRequest, _: None = Depends(require_admin)) -> dict[str, Any]:
    backup = _safe_project_path(request.backup_dir)
    if not backup.exists() or not backup.is_dir():
        raise HTTPException(status_code=404, detail="Backup directory not found")
    for path in ACTIVE_RULE_DIR.glob("*.json"):
        path.unlink()
    copied: list[str] = []
    for path in sorted(backup.glob("*.json")):
        target = ACTIVE_RULE_DIR / path.name
        shutil.copy2(path, target)
        copied.append(str(target.relative_to(PROJECT_ROOT)))
    payload = {
        "activated": True,
        "mode": "rollback",
        "backup_dir": str(backup.relative_to(PROJECT_ROOT)),
        "copied_files": copied,
    }
    _append_audit("rules_army_rollback", payload)
    return payload


def _filter_trace_rows(
    rows: list[dict[str, Any]],
    *,
    source: str | None,
    query_primary: str | None,
    service_primary: str | None,
    customer_id: str | None,
    min_latency_ms: float | None,
    has_forbid_markers: bool | None,
    has_negated_terms: bool | None,
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []

    for row in rows:
        if source and _lower(_nested(row, "assistant", "source")) != source.casefold():
            continue

        if customer_id and str(row.get("customer_id")) != customer_id:
            continue

        if query_primary and _trace_query_primary(row) != query_primary:
            continue

        if service_primary and _trace_service_primary(row) != service_primary:
            continue

        if min_latency_ms is not None:
            elapsed = row.get("elapsed_ms")
            if not isinstance(elapsed, int | float) or float(elapsed) < min_latency_ms:
                continue

        atoms = row.get("runtime_atoms")
        if not isinstance(atoms, dict):
            atoms = {}

        forbid_markers = atoms.get("forbid_markers")
        negated_terms = atoms.get("negated_terms")

        if has_forbid_markers is not None:
            present = isinstance(forbid_markers, list) and len(forbid_markers) > 0
            if present != has_forbid_markers:
                continue

        if has_negated_terms is not None:
            present = isinstance(negated_terms, list) and len(negated_terms) > 0
            if present != has_negated_terms:
                continue

        filtered.append(row)

    return filtered


def _trace_filter_payload(**filters: Any) -> dict[str, Any]:
    return {key: value for key, value in filters.items() if value is not None}


def _trace_query_primary(row: dict[str, Any]) -> str | None:
    value = _nested(row, "intent", "query_primary")
    if isinstance(value, str):
        return value

    value = _nested(row, "decision", "final_vote", "query_primary")
    return value if isinstance(value, str) else None


def _trace_service_primary(row: dict[str, Any]) -> str | None:
    value = _nested(row, "intent", "service_primary")
    if isinstance(value, str):
        return value

    value = _nested(row, "decision", "final_vote", "service_primary")
    return value if isinstance(value, str) else None


def _nested(row: dict[str, Any], *keys: str) -> Any:
    value: Any = row
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _lower(value: Any) -> str | None:
    return value.casefold() if isinstance(value, str) else None


def _safe_project_path(value: str) -> Path:
    path = (PROJECT_ROOT / value).resolve()
    if PROJECT_ROOT not in [path, *path.parents]:
        raise HTTPException(status_code=400, detail="Path escapes project root")
    return path


def _latest_existing(candidates: list[Path]) -> Path:
    existing = [path for path in candidates if path.exists()]
    if not existing:
        raise HTTPException(status_code=404, detail="Report not found")
    return max(existing, key=lambda item: item.stat().st_mtime)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail=f"JSON file must contain object: {path}")
    return payload


def _run(command: list[str]) -> None:
    proc = subprocess.run(  # noqa: S603
        command,
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise HTTPException(
            status_code=500, detail={"stdout": proc.stdout[-4000:], "stderr": proc.stderr[-4000:]}
        )


def _load_rule_dir(directory: Path) -> dict[str, Any]:
    return {
        "directory": str(directory.relative_to(PROJECT_ROOT)),
        "files": [_read_json(path) for path in sorted(directory.glob("*.json"))],
    }


def _count_rules(directory: Path) -> int:
    total = 0
    for path in directory.glob("*.json"):
        data = _read_json(path)
        rules = data.get("rules", [])
        total += len(rules) if isinstance(rules, list) else 0
    return total


def _candidate_file() -> Path:
    ANALYSIS_CANDIDATE_ROOT.mkdir(parents=True, exist_ok=True)
    return ANALYSIS_CANDIDATE_ROOT / "candidates.json"


def _load_candidates() -> list[dict[str, Any]]:
    path = _candidate_file()
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, list) else []


def _save_candidates(candidates: list[dict[str, Any]]) -> None:
    path = _candidate_file()
    path.write_text(json.dumps(candidates, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _append_audit(event: str, payload: dict[str, Any]) -> None:
    ACTIVATION_LOG.parent.mkdir(parents=True, exist_ok=True)
    row = {"event": event, "timestamp": datetime.now(UTC).isoformat(), "payload": payload}
    with ACTIVATION_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
