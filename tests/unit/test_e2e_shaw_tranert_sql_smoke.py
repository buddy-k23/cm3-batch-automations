"""Parse-smoke tests for the SHAW TRANERT L2b SQL artifacts (commit 6, #18).

This module walks every ``.sql`` file under
``config/e2e/sources/SHAW/sql/tranert/`` and asserts two things:

* **Offline (always runs):** the bootstrap statement splitter
  (``sql_bootstrap._split_statements``) parses every file into one or more
  statements, and every statement classifies as ``is_safe`` under the
  ``shaw_tranert_smoke`` whitelist classifier (Policy A). This is the
  fast, network-free gate that catches a malformed or
  non-whitelist-compliant statement the moment it lands in the tree.

* **Live (gated, skipped when SIT is unreachable):** every materialized
  helper / expected table body actually compiles against the real SIT
  Oracle instance, by reusing the ``validate`` subcommand. This is the
  authoritative "the SQL is valid Oracle against the SHAW schema" proof.
  It is skipped (never failed) when no Oracle connection can be opened,
  so the suite stays green on a developer laptop without SIT access,
  per AGENTS.md (integration tests that hit Oracle must be gated and
  skipped when the DB is unavailable).

No production code under ``src/`` is touched; this is harness test code
only.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.e2e_lib.shaw_tranert_smoke import (  # noqa: E402
    SmokeRunnerError,
    _TRANERT_DIR,
    _split_file,
    classify_statement,
)


def _all_sql_files() -> List[Path]:
    """Return every ``.sql`` file under the tranert tree, sorted.

    Sorted so pytest parametrize IDs are stable across runs and platforms.
    """
    return sorted(_TRANERT_DIR.rglob("*.sql"))


def _sql_file_ids() -> List[str]:
    """Repo-relative POSIX paths for use as parametrize IDs."""
    return [p.relative_to(_TRANERT_DIR).as_posix() for p in _all_sql_files()]


# --------------------------------------------------------------------------- #
# Offline half — always runs, no network.
# --------------------------------------------------------------------------- #


class TestSqlFilesParseOffline:
    """Every tranert .sql file splits cleanly and classifies is_safe."""

    def test_tree_is_not_empty(self) -> None:
        # Guard against a path/glob regression silently turning the whole
        # parametrized suite into zero test cases (which would pass
        # vacuously).
        files = _all_sql_files()
        assert files, f"no .sql files found under {_TRANERT_DIR}"
        # Sanity floor: the bootstrap + load + query files that must exist.
        assert len(files) >= 15, f"unexpectedly few SQL files: {len(files)}"

    @pytest.mark.parametrize("rel_path", _sql_file_ids())
    def test_file_splits_into_statements(self, rel_path: str) -> None:
        path = _TRANERT_DIR / rel_path
        statements = _split_file(path)
        assert statements, f"{rel_path} split into zero statements"
        # No statement may be blank/whitespace-only after splitting.
        for i, stmt in enumerate(statements):
            assert stmt.strip(), f"{rel_path} statement #{i} is blank"

    @pytest.mark.parametrize("rel_path", _sql_file_ids())
    def test_every_statement_classifies_safe(self, rel_path: str) -> None:
        path = _TRANERT_DIR / rel_path
        statements = _split_file(path)
        for i, stmt in enumerate(statements):
            c = classify_statement(stmt)
            assert c.is_safe is True, (
                f"{rel_path} statement #{i} ({c.kind}) refused: {c.reason}\n"
                f"  first 80 chars: {stmt.strip()[:80]!r}"
            )


# --------------------------------------------------------------------------- #
# Live half — gated; skipped when SIT is unreachable.
# --------------------------------------------------------------------------- #


def _sit_connection_or_skip() -> None:
    """Open + close a SIT app_int connection, or ``pytest.skip`` on failure.

    Credentials are resolved via SecretResolver (``.env`` / environment).
    Any failure to connect — missing creds, no driver, no network — results
    in a skip rather than a failure, so the offline gate remains the
    mandatory one and the live gate only runs where SIT is actually
    reachable.
    """
    try:
        from scripts.e2e_lib.shaw_tranert_smoke import _connect_sit
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"cannot import _connect_sit: {exc}")

    try:
        conn = _connect_sit(as_app_int=True)
    except SmokeRunnerError as exc:
        pytest.skip(f"SIT (app_int) not reachable: {exc}")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"SIT (app_int) connection raised: {exc}")
    else:
        conn.close()


@pytest.mark.integration
class TestExpectedTablesCompileLive:
    """Live SIT proof that every helper/expected table body compiles.

    Reuses the ``validate`` subcommand (SELECT 1 row from each CTAS table),
    which is the existing implementation of the live-SIT half. Skipped when
    SIT is unreachable.
    """

    def test_validate_all_expected_tables(self) -> None:
        _sit_connection_or_skip()

        from scripts.e2e_lib.shaw_tranert_smoke import _new_audit_log, run_validate

        audit = _new_audit_log()
        results = run_validate(audit, as_app_int=True)
        assert results, "validate returned no probed tables"
        failures = {name: status for name, status in results.items() if status != "ok"}
        assert not failures, f"expected-table validation failures: {failures}"
