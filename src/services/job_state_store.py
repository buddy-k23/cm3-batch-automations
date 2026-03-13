from __future__ import annotations

import sqlite3
from pathlib import Path

from src.contracts.task_contracts import TaskRequest, TaskResult

DB_PATH = Path("state/job_state.db")


class JobStateStore:
    """Durable SQLite-backed job lifecycle state store."""

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
                CREATE TABLE IF NOT EXISTS job_states (
                  task_id TEXT PRIMARY KEY,
                  trace_id TEXT NOT NULL,
                  status TEXT NOT NULL,
                  intent TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  result_json TEXT NOT NULL,
                  source TEXT NOT NULL,
                  idempotency_key TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                )
                """
            )

    def create(self, request: TaskRequest, result: TaskResult) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO job_states
                (task_id, trace_id, status, intent, payload_json, result_json, source, idempotency_key, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.task_id,
                    request.trace_id,
                    result.status,
                    request.intent,
                    request.model_dump_json(),
                    result.model_dump_json(),
                    request.source,
                    request.idempotency_key,
                    request.deadline.isoformat(),
                    request.deadline.isoformat(),
                ),
            )

    def update_status(self, task_id: str, status: str, result: TaskResult) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE job_states SET status=?, result_json=?, updated_at=datetime('now') WHERE task_id=?",
                (status, result.model_dump_json(), task_id),
            )
            return cur.rowcount > 0

    def get(self, task_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT task_id, trace_id, status, intent, source, idempotency_key, result_json FROM job_states WHERE task_id=?",
                (task_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "task_id": row[0],
            "trace_id": row[1],
            "status": row[2],
            "intent": row[3],
            "source": row[4],
            "idempotency_key": row[5],
            "result_json": row[6],
        }

    def list(self, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT task_id, trace_id, status, intent, source FROM job_states ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "task_id": r[0],
                "trace_id": r[1],
                "status": r[2],
                "intent": r[3],
                "source": r[4],
            }
            for r in rows
        ]
