"""Security tests for SQL-injection hardening in the Oracle extractor.

S13.5-4 / #410 — the extractor previously built ``SELECT {cols} FROM {table}
WHERE {where} ... ROWNUM <= {limit}`` entirely by f-string, so any
operator-supplied table/column/where/limit value was concatenated raw into
the SQL text.  These tests prove the hardened construction:

- ``limit`` is bound as a parameter (never concatenated) and a non-positive /
  non-integer limit is rejected.
- table and column identifiers are allow-listed (strict identifier syntax);
  an injection attempt raises a clear ``ValueError`` *before* any SQL runs.
- raw ``where_clause`` is no longer accepted on the public extract path.
- ``get_table_stats`` receives the same identifier treatment.

The tests use a real in-memory SQLite database wired into the extractor via a
lightweight executor shim, so they exercise the actual SQL string that would
be sent to the driver without needing an Oracle server.
"""

from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from src.database.extractor import (
    DataExtractor,
    IdentifierValidationError,
    _validate_identifier,
)


class _SQLiteExecutorShim:
    """Stand-in for ``QueryExecutor`` backed by a real in-memory SQLite DB.

    Records every SQL string + params it is asked to run so tests can assert
    on exactly what would reach the driver, and actually executes it so a
    legitimate extract returns real rows.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self.executed: list[tuple[str, dict | None]] = []

    def execute_query(self, query: str, params: dict | None = None) -> pd.DataFrame:
        self.executed.append((query, params))
        # Oracle's ROWNUM pseudo-column does not exist in SQLite; rewrite the
        # bound-limit predicate to SQLite's LIMIT so a legitimate extract still
        # returns rows here. The binding (params) is preserved and asserted on.
        sql = query
        # The Oracle table-size query targets the user_segments catalog view,
        # which has no SQLite equivalent; return an empty frame for it (the
        # extractor already binds its :table_name param — no injection there).
        if "user_segments" in sql:
            return pd.DataFrame(columns=["SEGMENT_NAME", "SIZE_MB"])
        if ":row_limit" in sql:
            sql = sql.replace(" WHERE ROWNUM <= :row_limit", "")
            sql = f"{sql} LIMIT :row_limit"
        cur = self._conn.execute(sql, params or {})
        rows = cur.fetchall()
        # Oracle returns column names upper-cased; mirror that so the
        # extractor's ``df['ROW_COUNT']`` access path is exercised faithfully.
        cols = [d[0].upper() for d in cur.description] if cur.description else []
        return pd.DataFrame([dict(zip(cols, r)) for r in rows], columns=cols)

    def fetch_table_columns(self, table_name: str) -> list[str]:
        cur = self._conn.execute(f"PRAGMA table_info({table_name})")
        return [r[1] for r in cur.fetchall()]


@pytest.fixture()
def extractor() -> DataExtractor:
    """A DataExtractor whose executor is a real in-memory SQLite DB."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE CUSTOMERS (ID INTEGER, NAME TEXT, BALANCE REAL)")
    conn.executemany(
        "INSERT INTO CUSTOMERS VALUES (?, ?, ?)",
        [(1, "alice", 10.0), (2, "bob", 20.0), (3, "carol", 30.0)],
    )
    conn.commit()
    # DataExtractor.__init__ builds its own QueryExecutor from the connection;
    # swap it for the SQLite-backed shim.
    ext = DataExtractor.__new__(DataExtractor)
    ext.connection = None
    ext.executor = _SQLiteExecutorShim(conn)
    return ext


# ---------------------------------------------------------------------------
# Identifier validation helper
# ---------------------------------------------------------------------------

class TestValidateIdentifier:
    def test_accepts_plain_identifier(self) -> None:
        assert _validate_identifier("CUSTOMERS") == "CUSTOMERS"

    def test_accepts_schema_qualified(self) -> None:
        assert _validate_identifier("APP_INT.CUSTOMERS") == "APP_INT.CUSTOMERS"

    def test_accepts_dollar_and_underscore(self) -> None:
        assert _validate_identifier("MY_TAB$1") == "MY_TAB$1"

    @pytest.mark.parametrize(
        "evil",
        [
            "X; DROP TABLE Y",
            "CUSTOMERS WHERE 1=1",
            "CUST--comment",
            "CUST/*x*/",
            "NAME'",
            'NAME"',
            "1=1; DELETE FROM CUSTOMERS",
            "CUST OMERS",
            "",
            "1CUST",  # cannot start with a digit
        ],
    )
    def test_rejects_injection(self, evil: str) -> None:
        with pytest.raises(IdentifierValidationError):
            _validate_identifier(evil)


# ---------------------------------------------------------------------------
# extract_table
# ---------------------------------------------------------------------------

class TestExtractTableSecurity:
    def test_legitimate_extract_works(self, extractor: DataExtractor) -> None:
        df = extractor.extract_table("CUSTOMERS", columns=["ID", "NAME"])
        assert list(df.columns) == ["ID", "NAME"]
        assert len(df) == 3

    def test_limit_is_bound_not_interpolated(self, extractor: DataExtractor) -> None:
        df = extractor.extract_table("CUSTOMERS", limit=2)
        assert len(df) == 2
        sql, params = extractor.executor.executed[-1]
        # The limit must be bound as a parameter, never concatenated inline.
        assert ":row_limit" in sql
        assert "2" not in sql
        assert params == {"row_limit": 2}

    def test_malicious_table_rejected(self, extractor: DataExtractor) -> None:
        with pytest.raises(IdentifierValidationError):
            extractor.extract_table("CUSTOMERS; DROP TABLE CUSTOMERS")
        # nothing executed
        assert extractor.executor.executed == []

    def test_malicious_column_rejected(self, extractor: DataExtractor) -> None:
        with pytest.raises(IdentifierValidationError):
            extractor.extract_table("CUSTOMERS", columns=["ID", "NAME'; DROP TABLE X --"])
        assert extractor.executor.executed == []

    def test_negative_limit_rejected(self, extractor: DataExtractor) -> None:
        with pytest.raises(ValueError):
            extractor.extract_table("CUSTOMERS", limit=-5)

    def test_non_int_limit_rejected(self, extractor: DataExtractor) -> None:
        with pytest.raises(ValueError):
            extractor.extract_table("CUSTOMERS", limit="10; DROP TABLE X")  # type: ignore[arg-type]

    def test_raw_where_clause_rejected(self, extractor: DataExtractor) -> None:
        with pytest.raises(ValueError):
            extractor.extract_table("CUSTOMERS", where_clause="1=1; DELETE FROM CUSTOMERS")
        assert extractor.executor.executed == []


# ---------------------------------------------------------------------------
# extract_to_file
# ---------------------------------------------------------------------------

class TestExtractToFileSecurity:
    def test_malicious_table_rejected(self, extractor: DataExtractor, tmp_path) -> None:
        with pytest.raises(IdentifierValidationError):
            extractor.extract_to_file(
                table_name="CUSTOMERS; DROP TABLE X",
                output_file=str(tmp_path / "out.txt"),
            )

    def test_raw_where_clause_rejected(self, extractor: DataExtractor, tmp_path) -> None:
        with pytest.raises(ValueError):
            extractor.extract_to_file(
                table_name="CUSTOMERS",
                output_file=str(tmp_path / "out.txt"),
                where_clause="1=1; DROP TABLE X",
            )


# ---------------------------------------------------------------------------
# get_table_stats
# ---------------------------------------------------------------------------

class TestGetTableStatsSecurity:
    def test_legitimate_stats_work(self, extractor: DataExtractor) -> None:
        stats = extractor.get_table_stats("CUSTOMERS")
        assert stats["row_count"] == 3
        assert stats["column_count"] == 3

    def test_malicious_table_rejected(self, extractor: DataExtractor) -> None:
        with pytest.raises(IdentifierValidationError):
            extractor.get_table_stats("CUSTOMERS; DROP TABLE CUSTOMERS")
        assert extractor.executor.executed == []
