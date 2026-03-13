from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

DB_PATH = Path("state/compare_jobs.db")


class CompareJobStore:
    """Durable SQLite-backed store for async compare jobs."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS compare_jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )

    def create(self, job_id: str, status: str = "queued") -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO compare_jobs (job_id, status, updated_at) VALUES (?, ?, datetime('now'))",
                (job_id, status),
            )

    def update(self, job_id: str, *, status: str, result: dict[str, Any] | None = None, error: str | None = None) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE compare_jobs
                SET status=?, result_json=?, error=?, updated_at=datetime('now')
                WHERE job_id=?
                """,
                (status, json.dumps(result) if result is not None else None, error, job_id),
            )
            return cur.rowcount > 0

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT job_id, status, result_json, error FROM compare_jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()

        if not row:
            return None

        result_json = row[2]
        result = json.loads(result_json) if result_json else None
        return {"job_id": row[0], "status": row[1], "result": result, "error": row[3]}
