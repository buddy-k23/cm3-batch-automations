# Run History DB Dual-Write Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Dual-write every suite run to both `reports/run_history.json` (existing) and Oracle tables `APP_INT.APP_RUN_HISTORY` / `APP_INT.APP_RUN_TESTS` when Oracle is configured, and read history from the DB when available.

**Architecture:** A new `RunHistoryRepository` class in `src/database/run_history.py` handles all DB I/O. `_append_run_history()` in `run_tests_command.py` writes JSON first (always), then calls the repository if `ORACLE_USER` is set — any DB exception is logged as a warning and swallowed, so JSON always wins. The `GET /api/v1/runs/history` endpoint tries the DB first, falls back to JSON if the DB is unavailable or unconfigured.

**Tech Stack:** Python 3.10+, oracledb (thin mode), FastAPI, pytest with `unittest.mock`

---

## Codebase context

- **OracleConnection** — `src/database/connection.py`. Use `OracleConnection.from_env()` to get a connection from `ORACLE_USER` / `ORACLE_PASSWORD` / `ORACLE_DSN` env vars. It is a context manager: `with conn as raw_conn: raw_conn.cursor()...`
- **Tables** — `APP_INT.APP_RUN_HISTORY` (one row per run) and `APP_INT.APP_RUN_TESTS` (one row per test within a run). DDL is in `sql/app_int/setup_app_run_history.sql`.
- **JSON shape** (what the UI expects): `{"run_id", "suite_name", "environment", "timestamp", "status", "report_url", "pass_count", "fail_count", "skip_count", "total_count", "archive_path"}`.
- **`_append_run_history()`** is in `src/commands/run_tests_command.py` around line 402. It builds the same dict and appends it to `reports/run_history.json`.
- **`get_run_history()`** is in `src/api/routers/ui.py` around line 33. It reads `reports/run_history.json` and returns the last 20 entries reversed.
- **Test patterns** — use `monkeypatch` or `unittest.mock.patch` / `MagicMock`. See `tests/unit/test_run_tests_command.py::TestArchiveIntegration` for the monkeypatch style used in this project.

---

### Task 1: RunHistoryRepository

**Files:**
- Create: `src/database/run_history.py`
- Create: `tests/unit/test_run_history_repository.py`

---

**Step 1: Write the failing tests**

Create `tests/unit/test_run_history_repository.py`:

```python
"""Unit tests for RunHistoryRepository — all Oracle calls are mocked."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

import pytest


SAMPLE_ENTRY = {
    "run_id": "abc-123",
    "suite_name": "My Suite",
    "environment": "uat",
    "timestamp": "2026-03-02T10:00:00.000000Z",
    "status": "PASS",
    "pass_count": 3,
    "fail_count": 0,
    "skip_count": 1,
    "total_count": 4,
    "report_url": "/reports/My_Suite_abc-123_suite.html",
    "archive_path": "/reports/archive/2026/03/02/abc-123",
}

SAMPLE_RESULTS = [
    {"name": "Test A", "type": "structural", "status": "PASS",
     "rows_processed": 100, "error_count": 0, "duration_secs": 0.5, "report_path": ""},
    {"name": "Test B", "type": "api_check", "status": "FAIL",
     "rows_processed": None, "error_count": 1, "duration_secs": 0.1, "report_path": ""},
]


def _make_repo():
    """Return a RunHistoryRepository with a fully mocked OracleConnection."""
    from src.database.run_history import RunHistoryRepository
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor
    repo = RunHistoryRepository(conn=mock_conn)
    return repo, mock_conn, mock_cursor


class TestInsertRun:
    def test_executes_insert_with_correct_params(self):
        repo, mock_conn, mock_cursor = _make_repo()
        repo.insert_run(SAMPLE_ENTRY)
        assert mock_cursor.execute.called
        sql, params = mock_cursor.execute.call_args.args
        assert "APP_INT.APP_RUN_HISTORY" in sql
        assert params["run_id"] == "abc-123"
        assert params["suite_name"] == "My Suite"
        assert params["status"] == "PASS"
        assert params["pass_count"] == 3
        assert params["total_count"] == 4

    def test_timestamp_converted_to_datetime(self):
        repo, mock_conn, mock_cursor = _make_repo()
        repo.insert_run(SAMPLE_ENTRY)
        _, params = mock_cursor.execute.call_args.args
        assert isinstance(params["run_timestamp"], datetime)
        assert params["run_timestamp"].tzinfo is not None

    def test_commits_after_insert(self):
        repo, mock_conn, mock_cursor = _make_repo()
        repo.insert_run(SAMPLE_ENTRY)
        mock_conn.commit.assert_called_once()


class TestInsertTests:
    def test_executemany_called_with_all_rows(self):
        repo, mock_conn, mock_cursor = _make_repo()
        repo.insert_tests("abc-123", SAMPLE_RESULTS)
        assert mock_cursor.executemany.called
        sql, rows = mock_cursor.executemany.call_args.args
        assert "APP_INT.APP_RUN_TESTS" in sql
        assert len(rows) == 2
        assert rows[0]["run_id"] == "abc-123"
        assert rows[0]["test_name"] == "Test A"
        assert rows[1]["status"] == "FAIL"

    def test_empty_results_skips_db_call(self):
        repo, mock_conn, mock_cursor = _make_repo()
        repo.insert_tests("abc-123", [])
        mock_cursor.executemany.assert_not_called()

    def test_commits_after_insert(self):
        repo, mock_conn, mock_cursor = _make_repo()
        repo.insert_tests("abc-123", SAMPLE_RESULTS)
        mock_conn.commit.assert_called_once()


class TestFetchHistory:
    def test_returns_list_of_dicts(self):
        repo, mock_conn, mock_cursor = _make_repo()
        mock_cursor.description = [
            ("RUN_ID",), ("SUITE_NAME",), ("ENVIRONMENT",), ("TIMESTAMP",),
            ("STATUS",), ("PASS_COUNT",), ("FAIL_COUNT",), ("SKIP_COUNT",),
            ("TOTAL_COUNT",), ("REPORT_URL",), ("ARCHIVE_PATH",),
        ]
        mock_cursor.fetchall.return_value = [
            ("abc-123", "My Suite", "uat", datetime(2026, 3, 2, 10, 0, 0, tzinfo=timezone.utc),
             "PASS", 3, 0, 1, 4, "/reports/x.html", "/archive/abc-123"),
        ]
        results = repo.fetch_history(limit=20)
        assert len(results) == 1
        assert results[0]["run_id"] == "abc-123"
        assert results[0]["status"] == "PASS"

    def test_timestamp_serialized_to_iso_string(self):
        repo, mock_conn, mock_cursor = _make_repo()
        mock_cursor.description = [("RUN_ID",), ("TIMESTAMP",)]
        mock_cursor.fetchall.return_value = [
            ("abc-123", datetime(2026, 3, 2, 10, 0, 0, tzinfo=timezone.utc)),
        ]
        results = repo.fetch_history()
        assert isinstance(results[0]["timestamp"], str)
        assert "2026" in results[0]["timestamp"]

    def test_passes_limit_to_query(self):
        repo, mock_conn, mock_cursor = _make_repo()
        mock_cursor.description = []
        mock_cursor.fetchall.return_value = []
        repo.fetch_history(limit=5)
        _, params = mock_cursor.execute.call_args.args
        assert params.get("limit") == 5
```

**Step 2: Run tests to verify they fail**

```bash
cd /Users/buddy/claude-code/automations/valdo
python3 -m pytest tests/unit/test_run_history_repository.py -v --no-cov 2>&1 | tail -15
```

Expected: `ModuleNotFoundError` or `ImportError` — `src.database.run_history` doesn't exist yet.

**Step 3: Implement `src/database/run_history.py`**

```python
"""Oracle-backed persistence for test suite run history."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from .connection import OracleConnection

logger = logging.getLogger(__name__)

_INSERT_RUN = """
INSERT INTO APP_INT.APP_RUN_HISTORY
  (RUN_ID, SUITE_NAME, ENVIRONMENT, RUN_TIMESTAMP, STATUS,
   PASS_COUNT, FAIL_COUNT, SKIP_COUNT, TOTAL_COUNT, REPORT_URL, ARCHIVE_PATH)
VALUES
  (:run_id, :suite_name, :environment, :run_timestamp, :status,
   :pass_count, :fail_count, :skip_count, :total_count, :report_url, :archive_path)
"""

_INSERT_TEST = """
INSERT INTO APP_INT.APP_RUN_TESTS
  (RUN_ID, TEST_NAME, TEST_TYPE, STATUS, ROW_COUNT, ERROR_COUNT, DURATION_SECS, REPORT_PATH)
VALUES
  (:run_id, :test_name, :test_type, :status, :row_count, :error_count, :duration_secs, :report_path)
"""

_FETCH_HISTORY = """
SELECT RUN_ID, SUITE_NAME, ENVIRONMENT, RUN_TIMESTAMP, STATUS,
       PASS_COUNT, FAIL_COUNT, SKIP_COUNT, TOTAL_COUNT, REPORT_URL, ARCHIVE_PATH
FROM APP_INT.APP_RUN_HISTORY
ORDER BY RUN_TIMESTAMP DESC
FETCH FIRST :limit ROWS ONLY
"""


def _parse_ts(ts_str: str) -> datetime:
    """Parse ISO-8601 UTC string (with or without trailing Z) to datetime."""
    return datetime.fromisoformat(ts_str.rstrip("Z")).replace(tzinfo=timezone.utc)


def _ts_to_iso(value: Any) -> Any:
    """Convert a datetime to ISO-8601 string; pass non-datetime values through."""
    if isinstance(value, datetime):
        ts = value.astimezone(timezone.utc)
        return ts.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    return value


class RunHistoryRepository:
    """Reads and writes suite run history to/from Oracle.

    Args:
        conn: Optional OracleConnection to use. Defaults to ``OracleConnection.from_env()``.

    Example::

        repo = RunHistoryRepository()
        repo.insert_run(entry)
        repo.insert_tests(run_id, results)
        history = repo.fetch_history(limit=20)
    """

    def __init__(self, conn: OracleConnection | None = None) -> None:
        self._conn = conn if conn is not None else OracleConnection.from_env()

    def insert_run(self, entry: dict[str, Any]) -> None:
        """Insert one run summary row into ``APP_INT.APP_RUN_HISTORY``.

        Args:
            entry: Dict with keys matching ``run_history.json`` entries.
        """
        with self._conn as conn:
            cursor = conn.cursor()
            cursor.execute(_INSERT_RUN, {
                "run_id":       entry["run_id"],
                "suite_name":   entry["suite_name"],
                "environment":  entry["environment"],
                "run_timestamp": _parse_ts(entry["timestamp"]),
                "status":       entry["status"],
                "pass_count":   entry.get("pass_count", 0),
                "fail_count":   entry.get("fail_count", 0),
                "skip_count":   entry.get("skip_count", 0),
                "total_count":  entry.get("total_count", 0),
                "report_url":   entry.get("report_url", ""),
                "archive_path": entry.get("archive_path", ""),
            })
            conn.commit()

    def insert_tests(self, run_id: str, results: list[dict[str, Any]]) -> None:
        """Insert one row per test into ``APP_INT.APP_RUN_TESTS``.

        Args:
            run_id: The parent run UUID.
            results: List of per-test result dicts from ``_run_single_test``.
        """
        if not results:
            return
        rows = [
            {
                "run_id":        run_id,
                "test_name":     r.get("name", ""),
                "test_type":     r.get("type", ""),
                "status":        r.get("status", ""),
                "row_count":     r.get("rows_processed"),
                "error_count":   r.get("error_count", 0),
                "duration_secs": r.get("duration_secs"),
                "report_path":   r.get("report_path", ""),
            }
            for r in results
        ]
        with self._conn as conn:
            cursor = conn.cursor()
            cursor.executemany(_INSERT_TEST, rows)
            conn.commit()

    def fetch_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return the most recent run summaries from ``APP_INT.APP_RUN_HISTORY``.

        Args:
            limit: Maximum number of rows to return (default 20).

        Returns:
            List of dicts with the same keys as ``run_history.json`` entries,
            ordered newest-first.
        """
        with self._conn as conn:
            cursor = conn.cursor()
            cursor.execute(_FETCH_HISTORY, {"limit": limit})
            cols = [d[0].lower() for d in cursor.description]
            rows = cursor.fetchall()

        result = []
        for row in rows:
            d = dict(zip(cols, row))
            # Normalize RUN_TIMESTAMP (Oracle returns datetime) → ISO string
            if "run_timestamp" in d:
                d["timestamp"] = _ts_to_iso(d.pop("run_timestamp"))
            result.append(d)
        return result
```

**Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/unit/test_run_history_repository.py -v --no-cov 2>&1 | tail -20
```

Expected: all tests PASS.

**Step 5: Commit**

```bash
git add src/database/run_history.py tests/unit/test_run_history_repository.py
git commit -m "feat(database): add RunHistoryRepository for Oracle-backed run history"
```

---

### Task 2: Dual-write in `_append_run_history`

**Files:**
- Modify: `src/commands/run_tests_command.py` (function `_append_run_history`, around line 402)
- Modify: `tests/unit/test_run_tests_command.py` (class `TestArchiveIntegration`)

---

**Step 1: Write the failing tests**

Add a new test class at the bottom of `tests/unit/test_run_tests_command.py`:

```python
class TestRunHistoryDbWrite:
    """Verify _append_run_history dual-writes to Oracle when ORACLE_USER is set."""

    def _run_suite(self, tmp_path, monkeypatch, env_vars=None):
        """Helper: run a minimal api_check suite and return the output_dir."""
        import yaml
        from src.commands.run_tests_command import run_suite_from_path
        import src.utils.archive as archive_mod

        monkeypatch.setattr(
            archive_mod.ArchiveManager, "archive_run",
            lambda self_inner, **kwargs: tmp_path,
        )
        for k, v in (env_vars or {}).items():
            monkeypatch.setenv(k, v)

        suite_yaml = tmp_path / "suite.yaml"
        suite_yaml.write_text(
            yaml.dump({
                "name": "DB Test Suite",
                "environment": "dev",
                "tests": [{"name": "ping", "type": "api_check", "url": "http://localhost:9999/nope"}],
            }),
            encoding="utf-8",
        )
        output_dir = tmp_path / "reports"
        output_dir.mkdir()
        run_suite_from_path(suite_path=str(suite_yaml), params={}, env="dev", output_dir=str(output_dir))
        return output_dir

    def test_db_write_called_when_oracle_user_set(self, tmp_path, monkeypatch):
        """RunHistoryRepository.insert_run is called when ORACLE_USER is set."""
        from unittest.mock import MagicMock, patch

        mock_repo = MagicMock()
        with patch("src.commands.run_tests_command.RunHistoryRepository", return_value=mock_repo):
            self._run_suite(tmp_path, monkeypatch, env_vars={"ORACLE_USER": "APP_INT"})

        mock_repo.insert_run.assert_called_once()
        mock_repo.insert_tests.assert_called_once()

    def test_db_write_skipped_when_oracle_user_not_set(self, tmp_path, monkeypatch):
        """RunHistoryRepository is NOT instantiated when ORACLE_USER is absent."""
        monkeypatch.delenv("ORACLE_USER", raising=False)
        from unittest.mock import patch

        with patch("src.commands.run_tests_command.RunHistoryRepository") as mock_cls:
            self._run_suite(tmp_path, monkeypatch)

        mock_cls.assert_not_called()

    def test_json_written_even_if_db_raises(self, tmp_path, monkeypatch):
        """JSON history must be written even if the DB insert raises."""
        import json, glob
        from pathlib import Path
        from unittest.mock import MagicMock, patch

        mock_repo = MagicMock()
        mock_repo.insert_run.side_effect = RuntimeError("ORA-12170: connection timeout")

        with patch("src.commands.run_tests_command.RunHistoryRepository", return_value=mock_repo):
            output_dir = self._run_suite(tmp_path, monkeypatch, env_vars={"ORACLE_USER": "APP_INT"})

        found = glob.glob(str(tmp_path / "**" / "run_history.json"), recursive=True)
        assert found, "run_history.json must be written even after DB failure"
        history = json.loads(Path(found[0]).read_text(encoding="utf-8"))
        assert len(history) == 1
```

**Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/unit/test_run_tests_command.py::TestRunHistoryDbWrite -v --no-cov 2>&1 | tail -15
```

Expected: FAIL — `RunHistoryRepository` is not imported in `run_tests_command.py` yet.

**Step 3: Implement the dual-write in `_append_run_history`**

In `src/commands/run_tests_command.py`, find `_append_run_history`. After the line `history_path.write_text(...)` (the JSON write), add:

```python
    # Dual-write to Oracle when ORACLE_USER is configured.
    if os.getenv("ORACLE_USER"):
        try:
            from src.database.run_history import RunHistoryRepository
            repo = RunHistoryRepository()
            repo.insert_run(entry)
            repo.insert_tests(run_id, results)
        except Exception as exc:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(
                "run_history DB write failed (JSON fallback still written): %s", exc
            )
```

The full `_append_run_history` function after the edit should end:

```python
    existing.append(entry)
    history_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")

    # Dual-write to Oracle when ORACLE_USER is configured.
    if os.getenv("ORACLE_USER"):
        try:
            from src.database.run_history import RunHistoryRepository
            repo = RunHistoryRepository()
            repo.insert_run(entry)
            repo.insert_tests(run_id, results)
        except Exception as exc:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(
                "run_history DB write failed (JSON fallback still written): %s", exc
            )
```

Also verify `import os` is already at the top of `run_tests_command.py` — it is (used in `os.makedirs`).

**Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/unit/test_run_tests_command.py::TestRunHistoryDbWrite -v --no-cov 2>&1 | tail -15
```

Expected: all 3 new tests PASS. Then run the full unit suite to confirm nothing regressed:

```bash
python3 -m pytest tests/unit/ \
  --ignore=tests/unit/test_contracts_pipeline.py \
  --ignore=tests/unit/test_pipeline_runner.py \
  --ignore=tests/unit/test_workflow_wrapper_parity.py \
  --no-cov -q 2>&1 | tail -5
```

Expected: all tests pass.

**Step 5: Commit**

```bash
git add src/commands/run_tests_command.py tests/unit/test_run_tests_command.py
git commit -m "feat(runs): dual-write run history to Oracle when ORACLE_USER is set"
```

---

### Task 3: DB-first read in `GET /api/v1/runs/history`

**Files:**
- Modify: `src/api/routers/ui.py` (function `get_run_history`)
- Modify: `tests/integration/test_api_system.py` (add test for DB fallback path)

---

**Step 1: Write the failing tests**

Add to `tests/integration/test_api_system.py`:

```python
def test_run_history_falls_back_to_json_when_oracle_user_unset(monkeypatch):
    """When ORACLE_USER is not set, history is read from JSON (not DB)."""
    import os
    monkeypatch.delenv("ORACLE_USER", raising=False)

    from unittest.mock import patch
    with patch("src.api.routers.ui.RunHistoryRepository") as mock_cls:
        response = client.get("/api/v1/runs/history")

    assert response.status_code == 200
    mock_cls.assert_not_called()


def test_run_history_uses_db_when_oracle_user_set(monkeypatch):
    """When ORACLE_USER is set, history is read from RunHistoryRepository."""
    monkeypatch.setenv("ORACLE_USER", "APP_INT")

    from unittest.mock import MagicMock, patch
    mock_repo = MagicMock()
    mock_repo.fetch_history.return_value = [
        {
            "run_id": "test-001", "suite_name": "DB Suite",
            "environment": "uat", "timestamp": "2026-03-02T10:00:00.000000Z",
            "status": "PASS", "pass_count": 1, "fail_count": 0,
            "skip_count": 0, "total_count": 1,
            "report_url": "/reports/x.html", "archive_path": "",
        }
    ]
    with patch("src.api.routers.ui.RunHistoryRepository", return_value=mock_repo):
        response = client.get("/api/v1/runs/history")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["suite_name"] == "DB Suite"
    mock_repo.fetch_history.assert_called_once_with(limit=20)


def test_run_history_falls_back_to_json_when_db_raises(monkeypatch):
    """When DB raises, endpoint falls back to JSON and returns 200."""
    monkeypatch.setenv("ORACLE_USER", "APP_INT")

    from unittest.mock import MagicMock, patch
    mock_repo = MagicMock()
    mock_repo.fetch_history.side_effect = RuntimeError("ORA-12170")

    with patch("src.api.routers.ui.RunHistoryRepository", return_value=mock_repo):
        response = client.get("/api/v1/runs/history")

    assert response.status_code == 200
    assert isinstance(response.json(), list)
```

**Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/integration/test_api_system.py::test_run_history_uses_db_when_oracle_user_set \
  tests/integration/test_api_system.py::test_run_history_falls_back_to_json_when_db_raises \
  -v --no-cov 2>&1 | tail -15
```

Expected: FAIL — `RunHistoryRepository` is not imported in `ui.py` yet.

**Step 3: Implement DB-first read in `src/api/routers/ui.py`**

Replace the entire `get_run_history` function:

```python
@router.get("/api/v1/runs/history")
async def get_run_history() -> JSONResponse:
    """Return the last 20 suite run history entries.

    Reads from ``APP_INT.APP_RUN_HISTORY`` when ``ORACLE_USER`` is configured,
    with automatic fallback to ``reports/run_history.json`` if the DB is
    unavailable or unconfigured.

    Returns:
        JSONResponse: A JSON array of run result dicts (most recent first,
        max 20 entries).
    """
    import os

    if os.getenv("ORACLE_USER"):
        try:
            from src.database.run_history import RunHistoryRepository
            repo = RunHistoryRepository()
            return JSONResponse(content=repo.fetch_history(limit=20))
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "run_history DB read failed, falling back to JSON: %s", exc
            )

    # JSON fallback (default when Oracle is not configured or DB read fails)
    if not _RUN_HISTORY_PATH.exists():
        return JSONResponse(content=[])
    try:
        entries = json.loads(_RUN_HISTORY_PATH.read_text(encoding="utf-8"))
        return JSONResponse(content=entries[-20:][::-1])
    except Exception:
        return JSONResponse(content=[])
```

**Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/integration/test_api_system.py -v --no-cov 2>&1 | tail -15
```

Expected: all tests PASS including the 3 new ones.

Then run the full unit suite:

```bash
python3 -m pytest tests/unit/ \
  --ignore=tests/unit/test_contracts_pipeline.py \
  --ignore=tests/unit/test_pipeline_runner.py \
  --ignore=tests/unit/test_workflow_wrapper_parity.py \
  --no-cov -q 2>&1 | tail -5
```

Expected: all tests pass.

**Step 5: Commit**

```bash
git add src/api/routers/ui.py tests/integration/test_api_system.py
git commit -m "feat(api): run history reads from Oracle DB with JSON fallback"
```

---

### Task 4: Docs + final verification

**Files:**
- Modify: `docs/INSTALL.md` (update Database Setup section)
- Modify: `docs/USAGE_GUIDE.md` (add note to run-tests and Recent Runs sections)

---

**Step 1: Update `docs/INSTALL.md` — Database Setup section**

In the "Run history tables (optional)" sub-section, replace the "Note" block:

Old text:
```
> **Note:** The tool does not yet write to these tables automatically. They are provided as the target schema for a future database-backed history feature. In the meantime, you can load data from `reports/run_history.json` using a SQL\*Loader or Python ETL script.
```

New text:
```
> **Enabling DB run history:** Once the tables are created, set `ORACLE_USER`, `ORACLE_PASSWORD`, and `ORACLE_DSN` in your `.env` file. The tool will automatically dual-write every suite run to both `reports/run_history.json` (always) and the Oracle tables (when Oracle is configured). The Recent Runs UI and `GET /api/v1/runs/history` will read from the DB when available, with automatic fallback to JSON if the DB is unreachable.
```

**Step 2: Update `docs/USAGE_GUIDE.md`**

Find the `run-tests` CLI section. Add after the basic usage example:

```markdown
> **Run history storage:** Results are always written to `reports/run_history.json`. When `ORACLE_USER` is set in `.env`, each run is also persisted to `APP_INT.APP_RUN_HISTORY` and `APP_INT.APP_RUN_TESTS` in Oracle. The Recent Runs tab in the Web UI reads from the DB when available, falling back to the JSON file. See [Database Setup](INSTALL.md#database-setup) in the install guide for table setup instructions.
```

**Step 3: Run the full test suite one final time**

```bash
python3 -m pytest tests/unit/ \
  --ignore=tests/unit/test_contracts_pipeline.py \
  --ignore=tests/unit/test_pipeline_runner.py \
  --ignore=tests/unit/test_workflow_wrapper_parity.py \
  --cov=src --cov-report=term-missing -q 2>&1 | tail -10
```

Expected: all tests pass, coverage ≥ 80%.

**Step 4: Commit and push**

```bash
git add docs/INSTALL.md docs/USAGE_GUIDE.md
git commit -m "docs(runs): document Oracle dual-write run history feature"
git push origin main
```
