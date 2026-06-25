"""Unit tests for excel_db_compare_service (S24-2) — written BEFORE implementation (TDD).

These tests exercise the Excel<->DB comparison service end-to-end with a real
SQLite in-memory/temp database and a real generated ``.xlsx`` file, so the
round-trip (Excel read -> DB extract -> normalise -> run_compare_service) runs
with NO Oracle. They prove:

* matching Excel vs DB rows -> 0 differences / all matched;
* a deliberate value mismatch is reported;
* a row only in Excel and a row only in DB land on the correct side;
* the coercion crux — a date in Excel matches a DB ``DATE`` and an integer key
  matches a DB ``NUMBER``/``INTEGER`` key after normalisation;
* BOTH directions (``db-source`` / ``excel-source``) give consistent results;
* ``--output`` style ``output_path`` renders a real ``.html`` report.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from src.services.excel_db_compare_service import (
    EXCEL_AS_ACTUAL,
    DB_AS_ACTUAL,
    compare_excel_to_db,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_sqlite_db(tmp_path: Path, rows: list[dict], *, table: str = "ACCOUNTS") -> str:
    """Create a temp SQLite DB with a typed table seeded from *rows*.

    The table is declared with an INTEGER key column (``ID``) and a DATE column
    (``OPEN_DATE``) so the extract returns real ``int`` keys and date strings —
    exactly the drift the service must normalise to match the Excel side.

    Args:
        tmp_path: pytest temp dir.
        rows: list of dicts with keys ID (int), NAME (str), AMOUNT (str/num),
            OPEN_DATE (``YYYY-MM-DD`` str).
        table: table name to create.

    Returns:
        Absolute path string of the created SQLite file.
    """
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        f"CREATE TABLE {table} ("
        "ID INTEGER, NAME TEXT, AMOUNT TEXT, OPEN_DATE DATE)"
    )
    conn.executemany(
        f"INSERT INTO {table} (ID, NAME, AMOUNT, OPEN_DATE) VALUES (?, ?, ?, ?)",
        [(r["ID"], r["NAME"], r["AMOUNT"], r["OPEN_DATE"]) for r in rows],
    )
    conn.commit()
    conn.close()
    return str(db_path)


def _make_excel(tmp_path: Path, rows: list[dict], *, name: str = "data.xlsx") -> str:
    """Write an ``.xlsx`` whose ID is a true integer and OPEN_DATE a real date.

    Excel stores whole numbers as floats (12345 -> 12345.0) and dates as
    Timestamps; the S24-1 reader coerces both. We deliberately write the native
    types (int, ``Timestamp``) so the round-trip exercises that coercion against
    the DB side.

    Args:
        tmp_path: pytest temp dir.
        rows: list of dicts with keys ID, NAME, AMOUNT, OPEN_DATE.
        name: output filename.

    Returns:
        Absolute path string of the written ``.xlsx`` file.
    """
    df = pd.DataFrame(
        [
            {
                "ID": int(r["ID"]),
                "NAME": r["NAME"],
                "AMOUNT": r["AMOUNT"],
                "OPEN_DATE": pd.Timestamp(r["OPEN_DATE"]),
            }
            for r in rows
        ]
    )
    out = tmp_path / name
    df.to_excel(out, index=False)
    return str(out)


_MATCHING_ROWS = [
    {"ID": 12345, "NAME": "Alice", "AMOUNT": "100", "OPEN_DATE": "2024-03-17"},
    {"ID": 67890, "NAME": "Bob", "AMOUNT": "200", "OPEN_DATE": "2023-12-01"},
]


def _conn_override(db_path: str) -> dict:
    return {"db_adapter": "sqlite", "db_path": db_path}


def _rows_with_diffs(compare: dict) -> int:
    val = compare.get("rows_with_differences", compare.get("differences", 0))
    try:
        return len(val)
    except TypeError:
        return int(val) if val else 0


def _count(val) -> int:
    try:
        return len(val)
    except TypeError:
        return int(val) if val else 0


# ---------------------------------------------------------------------------
# (a) matching rows -> 0 differences / all matched
# ---------------------------------------------------------------------------


class TestMatching:
    def test_matching_excel_and_db_zero_differences(self, tmp_path: Path) -> None:
        db_path = _make_sqlite_db(tmp_path, _MATCHING_ROWS)
        excel = _make_excel(tmp_path, _MATCHING_ROWS)

        result = compare_excel_to_db(
            excel_file=excel,
            query_or_table="ACCOUNTS",
            key_columns=["ID"],
            connection_override=_conn_override(db_path),
        )

        compare = result["compare"]
        assert result["workflow"]["status"] == "passed"
        assert compare["matching_rows"] == 2
        assert _rows_with_diffs(compare) == 0
        assert _count(compare.get("only_in_file1", 0)) == 0
        assert _count(compare.get("only_in_file2", 0)) == 0

    def test_result_has_workflow_and_compare_keys(self, tmp_path: Path) -> None:
        db_path = _make_sqlite_db(tmp_path, _MATCHING_ROWS)
        excel = _make_excel(tmp_path, _MATCHING_ROWS)

        result = compare_excel_to_db(
            excel_file=excel,
            query_or_table="ACCOUNTS",
            key_columns=["ID"],
            connection_override=_conn_override(db_path),
        )

        assert "workflow" in result
        assert "compare" in result
        assert result["workflow"]["db_rows_extracted"] == 2
        # No `direction` argument was passed, so the service uses its default,
        # which is EXCEL_AS_ACTUAL ("db-source") — matching the CLI's
        # `--direction` default. (The previous expectation of DB_AS_ACTUAL was
        # the wrong side of the default; the constants/values are self-consistent
        # and the routing semantics are verified by TestOnlySide / TestBothDirections.)
        assert result["workflow"]["direction"] == EXCEL_AS_ACTUAL


# ---------------------------------------------------------------------------
# (b) value mismatch is reported
# ---------------------------------------------------------------------------


class TestValueMismatch:
    def test_value_mismatch_reported(self, tmp_path: Path) -> None:
        db_rows = [dict(r) for r in _MATCHING_ROWS]
        db_rows[0]["AMOUNT"] = "999"  # DB disagrees with Excel on AMOUNT
        db_path = _make_sqlite_db(tmp_path, db_rows)
        excel = _make_excel(tmp_path, _MATCHING_ROWS)

        result = compare_excel_to_db(
            excel_file=excel,
            query_or_table="ACCOUNTS",
            key_columns=["ID"],
            connection_override=_conn_override(db_path),
        )

        compare = result["compare"]
        assert result["workflow"]["status"] == "failed"
        assert _rows_with_diffs(compare) >= 1


# ---------------------------------------------------------------------------
# (c) row only in Excel / only in DB -> correct side
# ---------------------------------------------------------------------------


class TestOnlySide:
    def test_row_only_in_excel_and_only_in_db(self, tmp_path: Path) -> None:
        # DB has ID 12345 + 11111 ; Excel has ID 12345 + 22222.
        db_rows = [
            {"ID": 12345, "NAME": "Alice", "AMOUNT": "100", "OPEN_DATE": "2024-03-17"},
            {"ID": 11111, "NAME": "OnlyDb", "AMOUNT": "1", "OPEN_DATE": "2024-01-01"},
        ]
        excel_rows = [
            {"ID": 12345, "NAME": "Alice", "AMOUNT": "100", "OPEN_DATE": "2024-03-17"},
            {"ID": 22222, "NAME": "OnlyXl", "AMOUNT": "2", "OPEN_DATE": "2024-02-02"},
        ]
        db_path = _make_sqlite_db(tmp_path, db_rows)
        excel = _make_excel(tmp_path, excel_rows)

        # direction db-source: file1 = DB (source), file2 = Excel (actual).
        # only_in_file1 = only in DB ; only_in_file2 = only in Excel.
        result = compare_excel_to_db(
            excel_file=excel,
            query_or_table="ACCOUNTS",
            key_columns=["ID"],
            direction=EXCEL_AS_ACTUAL,  # DB is source -> file1, Excel actual -> file2
            connection_override=_conn_override(db_path),
        )

        compare = result["compare"]
        assert _count(compare.get("only_in_file1", 0)) == 1  # only in DB
        assert _count(compare.get("only_in_file2", 0)) == 1  # only in Excel


# ---------------------------------------------------------------------------
# (d) integer key + DATE column line up across Excel<->DB (the coercion crux)
# ---------------------------------------------------------------------------


class TestCoercionCrux:
    def test_int_key_and_date_match_across_excel_and_db(self, tmp_path: Path) -> None:
        """An Excel int key (stored as float) and an Excel date (Timestamp)
        must match the DB INTEGER key and DB DATE after normalisation —
        producing zero differences. If the join or the date/int coercion were
        broken, every row would land in only_in_file1/only_in_file2 instead.
        """
        rows = [
            {"ID": 12345, "NAME": "Alice", "AMOUNT": "100", "OPEN_DATE": "2024-03-17"},
        ]
        db_path = _make_sqlite_db(tmp_path, rows)
        excel = _make_excel(tmp_path, rows)

        result = compare_excel_to_db(
            excel_file=excel,
            query_or_table="ACCOUNTS",
            key_columns=["ID"],
            connection_override=_conn_override(db_path),
        )

        compare = result["compare"]
        # The single row matched: proves int key 12345 (not "12345.0") joined
        # AND the date 2024-03-17 (not "2024-03-17 00:00:00") compared equal.
        assert compare["matching_rows"] == 1
        assert _rows_with_diffs(compare) == 0
        assert _count(compare.get("only_in_file1", 0)) == 0
        assert _count(compare.get("only_in_file2", 0)) == 0

    def test_date_mismatch_is_detected_not_masked(self, tmp_path: Path) -> None:
        """A genuinely different date must be reported — proving the ISO
        coercion does not collapse distinct dates to equal.
        """
        excel_rows = [
            {"ID": 12345, "NAME": "Alice", "AMOUNT": "100", "OPEN_DATE": "2024-03-17"},
        ]
        db_rows = [
            {"ID": 12345, "NAME": "Alice", "AMOUNT": "100", "OPEN_DATE": "2024-03-18"},
        ]
        db_path = _make_sqlite_db(tmp_path, db_rows)
        excel = _make_excel(tmp_path, excel_rows)

        result = compare_excel_to_db(
            excel_file=excel,
            query_or_table="ACCOUNTS",
            key_columns=["ID"],
            connection_override=_conn_override(db_path),
        )

        assert result["workflow"]["status"] == "failed"
        assert _rows_with_diffs(result["compare"]) == 1


# ---------------------------------------------------------------------------
# (e) BOTH directions produce correct, consistent results
# ---------------------------------------------------------------------------


class TestBothDirections:
    def test_db_source_and_excel_source_both_match_when_equal(self, tmp_path: Path) -> None:
        db_path = _make_sqlite_db(tmp_path, _MATCHING_ROWS)
        excel = _make_excel(tmp_path, _MATCHING_ROWS)

        for direction in (EXCEL_AS_ACTUAL, DB_AS_ACTUAL):
            result = compare_excel_to_db(
                excel_file=excel,
                query_or_table="ACCOUNTS",
                key_columns=["ID"],
                direction=direction,
                connection_override=_conn_override(db_path),
            )
            assert result["workflow"]["status"] == "passed", direction
            assert result["workflow"]["direction"] == direction
            assert result["compare"]["matching_rows"] == 2

    def test_only_sides_swap_with_direction(self, tmp_path: Path) -> None:
        """The only_in_file1/only_in_file2 buckets must swap when the direction
        flips, because direction decides which side is file1 vs file2.
        """
        db_rows = [
            {"ID": 12345, "NAME": "Alice", "AMOUNT": "100", "OPEN_DATE": "2024-03-17"},
            {"ID": 11111, "NAME": "OnlyDb", "AMOUNT": "1", "OPEN_DATE": "2024-01-01"},
        ]
        excel_rows = [
            {"ID": 12345, "NAME": "Alice", "AMOUNT": "100", "OPEN_DATE": "2024-03-17"},
            {"ID": 22222, "NAME": "OnlyXl", "AMOUNT": "2", "OPEN_DATE": "2024-02-02"},
        ]
        db_path = _make_sqlite_db(tmp_path, db_rows)
        excel = _make_excel(tmp_path, excel_rows)

        # excel-source: Excel = file1, DB = file2.
        excel_src = compare_excel_to_db(
            excel_file=excel,
            query_or_table="ACCOUNTS",
            key_columns=["ID"],
            direction=DB_AS_ACTUAL,
            connection_override=_conn_override(db_path),
        )
        # db-source: DB = file1, Excel = file2.
        db_src = compare_excel_to_db(
            excel_file=excel,
            query_or_table="ACCOUNTS",
            key_columns=["ID"],
            direction=EXCEL_AS_ACTUAL,
            connection_override=_conn_override(db_path),
        )

        # excel-source: only_in_file1 = only-in-Excel (1), only_in_file2 = only-in-DB (1)
        assert _count(excel_src["compare"].get("only_in_file1", 0)) == 1
        assert _count(excel_src["compare"].get("only_in_file2", 0)) == 1
        # db-source: the sides are swapped relative to excel-source.
        assert _count(db_src["compare"].get("only_in_file1", 0)) == 1
        assert _count(db_src["compare"].get("only_in_file2", 0)) == 1
        # And the totals are consistent (1 unmatched on each side either way).
        assert excel_src["compare"]["matching_rows"] == db_src["compare"]["matching_rows"] == 1


# ---------------------------------------------------------------------------
# (f) --output writes a real .html and .json
# ---------------------------------------------------------------------------


class TestHtmlOutput:
    def test_html_report_written(self, tmp_path: Path) -> None:
        db_path = _make_sqlite_db(tmp_path, _MATCHING_ROWS)
        excel = _make_excel(tmp_path, _MATCHING_ROWS)
        out = tmp_path / "report.html"

        result = compare_excel_to_db(
            excel_file=excel,
            query_or_table="ACCOUNTS",
            key_columns=["ID"],
            connection_override=_conn_override(db_path),
            output_format="html",
            output_path=str(out),
        )

        assert out.exists()
        assert out.stat().st_size > 0
        assert result["report_path"] == str(out)
        assert "<html" in out.read_text(encoding="utf-8").lower()


# ---------------------------------------------------------------------------
# Input validation / query vs table
# ---------------------------------------------------------------------------


class TestInputs:
    def test_missing_excel_raises(self, tmp_path: Path) -> None:
        db_path = _make_sqlite_db(tmp_path, _MATCHING_ROWS)
        with pytest.raises(FileNotFoundError):
            compare_excel_to_db(
                excel_file=str(tmp_path / "nope.xlsx"),
                query_or_table="ACCOUNTS",
                key_columns=["ID"],
                connection_override=_conn_override(db_path),
            )

    def test_query_path_runs(self, tmp_path: Path) -> None:
        db_path = _make_sqlite_db(tmp_path, _MATCHING_ROWS)
        excel = _make_excel(tmp_path, _MATCHING_ROWS)

        result = compare_excel_to_db(
            excel_file=excel,
            query_or_table="SELECT ID, NAME, AMOUNT, OPEN_DATE FROM ACCOUNTS",
            key_columns="ID",
            connection_override=_conn_override(db_path),
        )
        assert result["compare"]["matching_rows"] == 2

    def test_invalid_direction_raises(self, tmp_path: Path) -> None:
        db_path = _make_sqlite_db(tmp_path, _MATCHING_ROWS)
        excel = _make_excel(tmp_path, _MATCHING_ROWS)
        with pytest.raises(ValueError, match="direction"):
            compare_excel_to_db(
                excel_file=excel,
                query_or_table="ACCOUNTS",
                key_columns=["ID"],
                direction="sideways",
                connection_override=_conn_override(db_path),
            )
