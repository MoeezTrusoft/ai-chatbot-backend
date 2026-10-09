"""Bounded indexed trace storage; SQLite WAL supports local worker processes."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bookcraft.infra.redaction import redact_value


@dataclass(slots=True)
class LiveTraceStore:
    path: Path
    retention_days: int = 30

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        database = self.path.with_suffix(".sqlite3")
        database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database, timeout=5)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA secure_delete=ON")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS traces (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "thread_id TEXT, recorded_at TEXT NOT NULL, payload TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS traces_thread ON traces(thread_id, id DESC)"
            )
            connection.execute("CREATE INDEX IF NOT EXISTS traces_time ON traces(recorded_at)")
            # Import a legacy file once, under the same cross-process writer lock.
            # Commit before unlinking: a crash can leave the file, never duplicate rows.
            connection.execute(
                "CREATE TABLE IF NOT EXISTS trace_migrations (name TEXT PRIMARY KEY)"
            )
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                migrated = connection.execute(
                    "SELECT 1 FROM trace_migrations WHERE name='jsonl'"
                ).fetchone()
                if not migrated:
                    if self.path.suffix == ".jsonl" and self.path.exists():
                        with self.path.open(encoding="utf-8") as source:
                            for line in source:
                                try:
                                    row = json.loads(line)
                                except json.JSONDecodeError:
                                    continue
                                if not isinstance(row, dict):
                                    continue
                                payload = redact_value(row)
                                if isinstance(payload, dict):
                                    for key in ("thread_id", "customer_id", "recorded_at"):
                                        if key in row:
                                            payload[key] = row[key]
                                    connection.execute(
                                        "INSERT INTO traces(thread_id, recorded_at, payload) "
                                        "VALUES (?, ?, ?)",
                                        (
                                            row.get("thread_id"),
                                            row.get("recorded_at", ""),
                                            json.dumps(payload, default=str),
                                        ),
                                    )
                    connection.execute("INSERT INTO trace_migrations(name) VALUES ('jsonl')")
            if self.path.suffix == ".jsonl":
                self.path.unlink(missing_ok=True)
            with connection:
                yield connection
        finally:
            connection.close()

    def append(self, row: dict[str, Any]) -> None:
        payload = dict(row)
        recorded_at = str(payload.pop("recorded_at", datetime.now(UTC).isoformat()))
        thread_id = str(payload.pop("thread_id", "")) or None
        customer_id = str(payload.pop("customer_id", "")) or None
        redacted = redact_value(payload)
        if not isinstance(redacted, dict):
            redacted = {"payload": redacted}
        redacted["recorded_at"] = recorded_at
        if thread_id:
            redacted["thread_id"] = thread_id
        if customer_id:
            redacted["customer_id"] = customer_id
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO traces(thread_id, recorded_at, payload) VALUES (?, ?, ?)",
                (thread_id, recorded_at, json.dumps(redacted, sort_keys=True, default=str)),
            )

    def latest(self, limit: int = 50, *, before_id: int | None = None) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id, payload FROM traces WHERE id < ? ORDER BY id DESC LIMIT ?",
                (before_id if before_id is not None else 9223372036854775807, _safe_limit(limit)),
            ).fetchall()
        return [{**json.loads(payload), "trace_id": key} for key, payload in rows]

    def for_thread(
        self, thread_id: str, limit: int = 100, *, before_id: int | None = None
    ) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id, payload FROM traces WHERE thread_id = ? AND id < ? "
                "ORDER BY id DESC LIMIT ?",
                (
                    thread_id,
                    before_id if before_id is not None else 9223372036854775807,
                    _safe_limit(limit),
                ),
            ).fetchall()
        return [{**json.loads(payload), "trace_id": key} for key, payload in rows]

    def delete_thread(self, thread_id: str) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM traces WHERE thread_id = ?", (thread_id,))

    def prune(self) -> int:
        cutoff = (datetime.now(UTC) - timedelta(days=self.retention_days)).isoformat()
        with self._connection() as connection:
            count = connection.execute(
                "DELETE FROM traces WHERE recorded_at < ?", (cutoff,)
            ).rowcount
        return count


def _safe_limit(limit: int) -> int:
    return max(1, min(limit, 500))
