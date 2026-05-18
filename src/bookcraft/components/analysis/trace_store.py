from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bookcraft.infra.redaction import redact_value


@dataclass(slots=True)
class LiveTraceStore:
    path: Path

    def append(self, row: dict[str, Any]) -> None:
        payload = dict(row)
        recorded_at = str(payload.pop("recorded_at", datetime.now(UTC).isoformat()))
        # Preserve routing keys before redaction — UUIDs can false-positive as phone numbers.
        thread_id = str(payload.pop("thread_id", "")) or None
        customer_id = str(payload.pop("customer_id", "")) or None

        redacted = redact_value(payload)
        if not isinstance(redacted, dict):
            redacted = {"payload": redacted}

        # Keep trace metadata machine-readable. Redact user/component payloads,
        # but do not run phone-like regexes over ISO timestamps or routing UUIDs.
        redacted["recorded_at"] = recorded_at
        if thread_id:
            redacted["thread_id"] = thread_id
        if customer_id:
            redacted["customer_id"] = customer_id

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(redacted, sort_keys=True, default=str) + "\n")

    def latest(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._read_rows()
        return list(reversed(rows[-_safe_limit(limit) :]))

    def for_thread(self, thread_id: str, limit: int = 100) -> list[dict[str, Any]]:
        rows = [row for row in self._read_rows() if str(row.get("thread_id")) == thread_id]
        return list(reversed(rows[-_safe_limit(limit) :]))

    def _read_rows(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []

        rows: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)

        return rows


def _safe_limit(limit: int) -> int:
    return max(1, min(limit, 500))
