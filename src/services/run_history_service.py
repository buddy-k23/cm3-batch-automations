"""Service layer for persisting run history to Oracle DB."""
from __future__ import annotations

from typing import Any


def write_run_to_db(
    entry: dict[str, Any],
    run_id: str,
    results: list[dict[str, Any]],
) -> None:
    """Persist run summary and per-test rows to Oracle.

    Thin service wrapper so the commands layer does not import the
    database layer directly (preserving the CLI/API → Commands →
    Services → DB layering from the architecture principles).

    Args:
        entry: Run summary dict with keys matching run_history.json entries.
        run_id: UUID of the run (used as foreign key for test rows).
        results: List of per-test result dicts from ``_run_single_test``.
    """
    from src.database.run_history import RunHistoryRepository

    repo = RunHistoryRepository()
    repo.insert_run(entry)
    repo.insert_tests(run_id, results)


def fetch_history_from_db(limit: int = 20) -> list[dict[str, Any]]:
    """Return the most recent run summaries from Oracle.

    Args:
        limit: Maximum number of rows to return (default 20).

    Returns:
        List of run summary dicts, newest first.
    """
    from src.database.run_history import RunHistoryRepository

    repo = RunHistoryRepository()
    return repo.fetch_history(limit=limit)
