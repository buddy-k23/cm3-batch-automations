"""End-to-end proof that ``db-compare`` honors ``DB_ADAPTER`` (S15-2, #405).

These tests prove ADR 0022 §5: the DB side of ``valdo db-compare`` is no longer
Oracle-locked.  :func:`~src.services.db_file_compare_service.compare_db_to_file`
now builds its database adapter via
:func:`~src.database.adapters.factory.get_database_adapter` (honouring
``DB_ADAPTER`` and any ``connection_override``) instead of falling back to
``OracleConnection.from_env()``.

The headline test runs the *whole* ``compare_db_to_file`` workflow against a
**real** on-disk SQLite database (created in pytest's ``tmp_path`` — never a
tracked fixture DB) with ``DB_ADAPTER=sqlite``, compares the extracted rows
against a matching file, and asserts both the all-match case and a seeded
difference.  No Oracle, no mocks on the DB side.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.services.db_file_compare_service import compare_db_to_file


# ---------------------------------------------------------------------------
# Fixtures — a real SQLite DB + a matching flat file, both under tmp_path
# ---------------------------------------------------------------------------

@pytest.fixture()
def seeded_db(tmp_path):
    """Create and seed a real on-disk SQLite database under ``tmp_path``.

    Returns the absolute path to the database file.  The DB lives in pytest's
    temp dir (outside the repo), so no fixture DB is ever tracked.
    """
    db_path = tmp_path / "db_compare_proof.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE ACCOUNTS (ID INTEGER, NAME TEXT, BALANCE TEXT)")
    conn.executemany(
        "INSERT INTO ACCOUNTS VALUES (?, ?, ?)",
        [
            (1, "alice", "100"),
            (2, "bob", "200"),
            (3, "carol", "300"),
        ],
    )
    conn.commit()
    conn.close()
    return str(db_path)


def _mapping():
    return {"name": "accounts", "fields": [{"name": "ID"}, {"name": "NAME"}, {"name": "BALANCE"}]}


def _write_file(path, rows):
    """Write a pipe-delimited file with an ID|NAME|BALANCE header."""
    lines = ["ID|NAME|BALANCE"]
    lines.extend("|".join(str(c) for c in r) for r in rows)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# Headline proof — db-compare on SQLite via DB_ADAPTER
# ---------------------------------------------------------------------------

class TestDbCompareOnSQLite:
    def test_all_rows_match(self, seeded_db, tmp_path, monkeypatch) -> None:
        """DB extract (SQLite) matching the file → workflow status 'passed'."""
        monkeypatch.setenv("DB_ADAPTER", "sqlite")
        monkeypatch.setenv("DB_PATH", seeded_db)

        actual = _write_file(
            tmp_path / "actual_match.txt",
            [(1, "alice", "100"), (2, "bob", "200"), (3, "carol", "300")],
        )

        result = compare_db_to_file(
            query_or_table="ACCOUNTS",
            mapping_config=_mapping(),
            actual_file=actual,
            key_columns=["ID"],
        )

        assert result["workflow"]["status"] == "passed"
        assert result["workflow"]["db_rows_extracted"] == 3
        assert result["compare"]["matching_rows"] == 3

    def test_seeded_difference_detected(self, seeded_db, tmp_path, monkeypatch) -> None:
        """A row that differs between DB and file → workflow status 'failed'."""
        monkeypatch.setenv("DB_ADAPTER", "sqlite")
        monkeypatch.setenv("DB_PATH", seeded_db)

        # bob's balance differs (200 in DB, 999 in file) — seeded difference.
        actual = _write_file(
            tmp_path / "actual_diff.txt",
            [(1, "alice", "100"), (2, "bob", "999"), (3, "carol", "300")],
        )

        result = compare_db_to_file(
            query_or_table="ACCOUNTS",
            mapping_config=_mapping(),
            actual_file=actual,
            key_columns=["ID"],
        )

        assert result["workflow"]["status"] == "failed"
        rows_with_diffs = result["compare"].get(
            "rows_with_differences", result["compare"].get("differences", 0)
        )
        try:
            assert len(rows_with_diffs) >= 1
        except TypeError:
            assert int(rows_with_diffs) >= 1

    def test_sql_query_path_on_sqlite(self, seeded_db, tmp_path, monkeypatch) -> None:
        """A SQL SELECT (not a bare table) also runs on the SQLite adapter."""
        monkeypatch.setenv("DB_ADAPTER", "sqlite")
        monkeypatch.setenv("DB_PATH", seeded_db)

        actual = _write_file(
            tmp_path / "actual_query.txt",
            [(2, "bob", "200"), (3, "carol", "300")],
        )

        result = compare_db_to_file(
            query_or_table="SELECT ID, NAME, BALANCE FROM ACCOUNTS WHERE ID >= 2",
            mapping_config=_mapping(),
            actual_file=actual,
            key_columns=["ID"],
        )

        assert result["workflow"]["db_rows_extracted"] == 2
        assert result["workflow"]["status"] == "passed"


# ---------------------------------------------------------------------------
# connection_override drives the adapter (named-connection / API path)
# ---------------------------------------------------------------------------

class TestConnectionOverrideOnSQLite:
    def test_override_selects_sqlite_adapter(self, seeded_db, tmp_path, monkeypatch) -> None:
        """connection_override with db_adapter=sqlite + db_path runs on SQLite.

        The env DB_ADAPTER is deliberately left at the Oracle default to prove
        the override (not the env) selects the backend.
        """
        monkeypatch.delenv("DB_ADAPTER", raising=False)
        monkeypatch.delenv("DB_PATH", raising=False)

        actual = _write_file(
            tmp_path / "actual_override.txt",
            [(1, "alice", "100"), (2, "bob", "200"), (3, "carol", "300")],
        )

        result = compare_db_to_file(
            query_or_table="ACCOUNTS",
            mapping_config=_mapping(),
            actual_file=actual,
            key_columns=["ID"],
            connection_override={"db_adapter": "sqlite", "db_path": seeded_db},
        )

        assert result["workflow"]["status"] == "passed"
        assert result["workflow"]["db_rows_extracted"] == 3
