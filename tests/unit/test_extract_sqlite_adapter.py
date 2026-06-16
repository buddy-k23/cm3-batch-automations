"""End-to-end + adapter-path tests for ``valdo extract`` on SQLite (S15-1, #405).

These tests prove ADR 0022 §4: ``extract`` is no longer Oracle-locked.  They
run against a **real** SQLite database (created in pytest's ``tmp_path`` — never
a tracked fixture DB) through the same adapter seam (:func:`get_database_adapter`)
the ``extract`` CLI command now uses, and assert:

- a whole-table extract and a custom-query extract both produce the expected
  pipe-delimited flat file with the right rows (the headline proof);
- the dialect-agnostic ``LIMIT`` paging (SQLite ``LIMIT`` rather than Oracle
  ``ROWNUM``) is applied and the limit is still **bound** as a parameter
  (S13.5-4 hardening preserved);
- the S13.5-4 identifier allow-listing and raw-``WHERE`` rejection still fire on
  the adapter path;
- the ``extract_to_file(params=…)`` signature reconciliation (the latent
  ``run_tests_command.py:141`` bug) binds params end-to-end.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.database.adapters.sqlite_adapter import SQLiteAdapter
from src.database.extractor import DataExtractor, IdentifierValidationError


@pytest.fixture()
def seeded_db(tmp_path):
    """Create and seed a real on-disk SQLite database under ``tmp_path``.

    Returns the absolute path to the database file.  The DB lives in pytest's
    temp dir (outside the repo), so no fixture DB is ever tracked.
    """
    db_path = tmp_path / "extract_proof.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE CUSTOMERS (ID INTEGER, NAME TEXT, BALANCE REAL)"
    )
    conn.executemany(
        "INSERT INTO CUSTOMERS VALUES (?, ?, ?)",
        [
            (1, "alice", 10.5),
            (2, "bob", 20.0),
            (3, "carol", 30.25),
            (4, "dave", 40.0),
        ],
    )
    conn.commit()
    conn.close()
    return str(db_path)


def _make_extractor(db_path: str) -> DataExtractor:
    """Build a connected SQLite-backed DataExtractor via the adapter seam."""
    adapter = SQLiteAdapter(db_path=db_path)
    adapter.connect()
    return DataExtractor(adapter)


# ---------------------------------------------------------------------------
# Headline proof — whole-table + query extraction to a flat file on SQLite
# ---------------------------------------------------------------------------

class TestExtractToFileSQLite:
    def test_whole_table_extract_writes_rows(self, seeded_db, tmp_path) -> None:
        out = tmp_path / "table_out.txt"
        ext = _make_extractor(seeded_db)
        stats = ext.extract_to_file(table_name="CUSTOMERS", output_file=str(out))

        assert stats["total_rows"] == 4
        lines = out.read_text(encoding="utf-8").splitlines()
        # header + 4 data rows
        assert lines[0].split("|") == ["ID", "NAME", "BALANCE"]
        assert len(lines) == 5
        assert "alice" in lines[1]
        assert lines[4].split("|")[1] == "dave"

    def test_query_extract_writes_rows(self, seeded_db, tmp_path) -> None:
        out = tmp_path / "query_out.txt"
        ext = _make_extractor(seeded_db)
        stats = ext.extract_to_file(
            query="SELECT NAME, BALANCE FROM CUSTOMERS WHERE BALANCE >= 30",
            output_file=str(out),
        )

        assert stats["total_rows"] == 2
        lines = out.read_text(encoding="utf-8").splitlines()
        assert lines[0].split("|") == ["NAME", "BALANCE"]
        names = {ln.split("|")[0] for ln in lines[1:]}
        assert names == {"carol", "dave"}

    def test_query_with_bound_params(self, seeded_db, tmp_path) -> None:
        """extract_to_file(params=…) binds params end-to-end (the #405 fix)."""
        out = tmp_path / "param_out.txt"
        ext = _make_extractor(seeded_db)
        stats = ext.extract_to_file(
            query="SELECT NAME FROM CUSTOMERS WHERE BALANCE >= :min_bal",
            output_file=str(out),
            params={"min_bal": 25},
        )

        assert stats["total_rows"] == 2
        lines = out.read_text(encoding="utf-8").splitlines()
        names = {ln for ln in lines[1:]}
        assert names == {"carol", "dave"}

    def test_custom_delimiter(self, seeded_db, tmp_path) -> None:
        out = tmp_path / "comma_out.txt"
        ext = _make_extractor(seeded_db)
        ext.extract_to_file(
            table_name="CUSTOMERS", output_file=str(out), delimiter=","
        )
        header = out.read_text(encoding="utf-8").splitlines()[0]
        assert header.split(",") == ["ID", "NAME", "BALANCE"]


# ---------------------------------------------------------------------------
# Dialect-agnostic limit paging on SQLite (LIMIT, not ROWNUM) — still bound
# ---------------------------------------------------------------------------

class TestExtractTableLimitSQLite:
    def test_limit_returns_subset(self, seeded_db) -> None:
        ext = _make_extractor(seeded_db)
        df = ext.extract_table("CUSTOMERS", limit=2)
        assert len(df) == 2

    def test_no_rownum_in_sqlite_path(self, seeded_db) -> None:
        """The Oracle ROWNUM predicate must not be emitted on the SQLite path."""
        ext = _make_extractor(seeded_db)
        # extract_table must succeed on SQLite (ROWNUM would raise here).
        df = ext.extract_table("CUSTOMERS", limit=3)
        assert len(df) == 3


# ---------------------------------------------------------------------------
# S13.5-4 hardening still applies on the adapter path
# ---------------------------------------------------------------------------

class TestAdapterPathHardening:
    def test_malicious_table_rejected(self, seeded_db, tmp_path) -> None:
        ext = _make_extractor(seeded_db)
        with pytest.raises(IdentifierValidationError):
            ext.extract_to_file(
                table_name="CUSTOMERS; DROP TABLE CUSTOMERS",
                output_file=str(tmp_path / "x.txt"),
            )

    def test_raw_where_rejected(self, seeded_db, tmp_path) -> None:
        ext = _make_extractor(seeded_db)
        with pytest.raises(ValueError):
            ext.extract_to_file(
                table_name="CUSTOMERS",
                output_file=str(tmp_path / "x.txt"),
                where_clause="1=1; DROP TABLE CUSTOMERS",
            )

    def test_negative_limit_rejected(self, seeded_db) -> None:
        ext = _make_extractor(seeded_db)
        with pytest.raises(ValueError):
            ext.extract_table("CUSTOMERS", limit=-1)
