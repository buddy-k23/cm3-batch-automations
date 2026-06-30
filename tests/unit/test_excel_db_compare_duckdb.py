"""DuckDB-backend wiring for ``excel-compare`` (S25-5) — written BEFORE implementation (TDD).

S25-5 threads comparison-backend selection through
:func:`~src.services.excel_db_compare_service.compare_excel_to_db` so that, when
the active backend is DuckDB (``COMPARISON_BACKEND=duckdb`` or an explicit
``backend="duckdb"``), the already-in-memory Excel frame and the (normalised) DB
extract are registered **directly** into DuckDB and diffed there — skipping the
two ``_df_to_temp_file`` writes the temp-file path performs.  When the backend is
native (the default), behaviour is **unchanged**: both sides are written to temp
files and the native engine reads them back.

Proven here against a real SQLite DB + a real generated ``.xlsx`` (no Oracle):

1. **PARITY**: the result dict under ``backend="duckdb"`` is identical to the one
   under ``backend="native"`` on the same data — all-match, value-diff, and
   only-in cases, across BOTH directions.
2. **TEMP-FILE SKIP**: :func:`_df_to_temp_file` is NOT called under duckdb and IS
   called (twice — one per side) under native.
3. **DUCKDB-MISSING SAFETY**: with duckdb reported absent, the service still
   works via native (temp files), no error.

The duckdb-active cases are gated with ``skipif`` on the optional ``duckdb``
package being importable.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

import src.services.db_file_compare_service as dbsvc
from src.services.excel_db_compare_service import (
    DB_AS_ACTUAL,
    EXCEL_AS_ACTUAL,
    compare_excel_to_db,
)

try:  # optional dependency gate
    import duckdb  # noqa: F401

    _HAS_DUCKDB = True
except ImportError:  # pragma: no cover - exercised only on duckdb-less runners
    _HAS_DUCKDB = False

requires_duckdb = pytest.mark.skipif(
    not _HAS_DUCKDB, reason="duckdb not installed (pip install duckdb to run)"
)


# ---------------------------------------------------------------------------
# Fixtures / helpers (mirrors test_excel_db_compare_service.py)
# ---------------------------------------------------------------------------


def _make_sqlite_db(tmp_path: Path, rows: list[dict], *, table: str = "ACCOUNTS") -> str:
    db_path = tmp_path / "excel_duck.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        f"CREATE TABLE {table} (ID INTEGER, NAME TEXT, AMOUNT TEXT, OPEN_DATE DATE)"
    )
    conn.executemany(
        f"INSERT INTO {table} (ID, NAME, AMOUNT, OPEN_DATE) VALUES (?, ?, ?, ?)",
        [(r["ID"], r["NAME"], r["AMOUNT"], r["OPEN_DATE"]) for r in rows],
    )
    conn.commit()
    conn.close()
    return str(db_path)


def _make_excel(tmp_path: Path, rows: list[dict], *, name: str = "data.xlsx") -> str:
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


def _normalize_compare(compare: dict) -> dict:
    out = dict(compare)

    def _records(val):
        if isinstance(val, pd.DataFrame):
            recs = val.to_dict(orient="records")
            return sorted(
                recs,
                key=lambda r: tuple(sorted((str(k), str(v)) for k, v in r.items())),
            )
        return val

    out["only_in_file1"] = _records(compare.get("only_in_file1"))
    out["only_in_file2"] = _records(compare.get("only_in_file2"))
    diffs = compare.get("differences")
    if isinstance(diffs, list):
        out["differences"] = sorted(
            diffs,
            key=lambda d: tuple(
                sorted((str(k), str(v)) for k, v in d.get("keys", {}).items())
            ),
        )
    return out


def _run(excel, db_path, backend, *, direction=EXCEL_AS_ACTUAL):
    return compare_excel_to_db(
        excel_file=excel,
        query_or_table="ACCOUNTS",
        key_columns=["ID"],
        direction=direction,
        connection_override=_conn_override(db_path),
        backend=backend,
    )


# ---------------------------------------------------------------------------
# (1) PARITY — native == duckdb (both directions)
# ---------------------------------------------------------------------------


@requires_duckdb
class TestExcelDbParity:
    @pytest.mark.parametrize("direction", [EXCEL_AS_ACTUAL, DB_AS_ACTUAL])
    def test_all_match_parity(self, tmp_path, direction) -> None:
        db_path = _make_sqlite_db(tmp_path, _MATCHING_ROWS)
        excel = _make_excel(tmp_path, _MATCHING_ROWS)

        native = _run(excel, db_path, "native", direction=direction)
        duck = _run(excel, db_path, "duckdb", direction=direction)

        assert native["workflow"]["status"] == duck["workflow"]["status"] == "passed"
        assert _normalize_compare(native["compare"]) == _normalize_compare(duck["compare"])

    def test_value_mismatch_parity(self, tmp_path) -> None:
        db_rows = [dict(r) for r in _MATCHING_ROWS]
        db_rows[0]["AMOUNT"] = "999"
        db_path = _make_sqlite_db(tmp_path, db_rows)
        excel = _make_excel(tmp_path, _MATCHING_ROWS)

        native = _run(excel, db_path, "native")
        duck = _run(excel, db_path, "duckdb")

        assert native["workflow"]["status"] == duck["workflow"]["status"] == "failed"
        assert _normalize_compare(native["compare"]) == _normalize_compare(duck["compare"])

    def test_only_in_each_side_parity(self, tmp_path) -> None:
        # DB has an extra row (id 99999), Excel has an extra row (id 11111).
        db_rows = [dict(r) for r in _MATCHING_ROWS] + [
            {"ID": 99999, "NAME": "Carol", "AMOUNT": "300", "OPEN_DATE": "2022-01-01"}
        ]
        excel_rows = [dict(r) for r in _MATCHING_ROWS] + [
            {"ID": 11111, "NAME": "Dave", "AMOUNT": "400", "OPEN_DATE": "2021-06-15"}
        ]
        db_path = _make_sqlite_db(tmp_path, db_rows)
        excel = _make_excel(tmp_path, excel_rows)

        native = _run(excel, db_path, "native")
        duck = _run(excel, db_path, "duckdb")

        assert _normalize_compare(native["compare"]) == _normalize_compare(duck["compare"])

    def test_env_var_selects_duckdb_parity(self, tmp_path, monkeypatch) -> None:
        db_path = _make_sqlite_db(tmp_path, _MATCHING_ROWS)
        excel = _make_excel(tmp_path, _MATCHING_ROWS)

        native = _run(excel, db_path, "native")

        monkeypatch.setenv("COMPARISON_BACKEND", "duckdb")
        duck = _run(excel, db_path, None)  # backend=None -> env var

        assert _normalize_compare(native["compare"]) == _normalize_compare(duck["compare"])


# ---------------------------------------------------------------------------
# (2) TEMP-FILE SKIP — proven via spy
# ---------------------------------------------------------------------------


class TestExcelTempFileSkip:
    @requires_duckdb
    def test_temp_file_skipped_under_duckdb(self, tmp_path, monkeypatch) -> None:
        calls = []
        real = dbsvc._df_to_temp_file

        def _spy(df, delimiter="|"):
            calls.append(1)
            return real(df, delimiter)

        monkeypatch.setattr(dbsvc, "_df_to_temp_file", _spy)
        # excel_db_compare_service imports _df_to_temp_file by name — patch there too.
        import src.services.excel_db_compare_service as exsvc

        monkeypatch.setattr(exsvc, "_df_to_temp_file", _spy)

        db_path = _make_sqlite_db(tmp_path, _MATCHING_ROWS)
        excel = _make_excel(tmp_path, _MATCHING_ROWS)
        result = _run(excel, db_path, "duckdb")

        assert result["workflow"]["status"] == "passed"
        assert calls == [], "duckdb path must not write temp files"

    def test_temp_files_used_under_native(self, tmp_path, monkeypatch) -> None:
        calls = []
        real = dbsvc._df_to_temp_file

        def _spy(df, delimiter="|"):
            calls.append(1)
            return real(df, delimiter)

        import src.services.excel_db_compare_service as exsvc

        monkeypatch.setattr(exsvc, "_df_to_temp_file", _spy)

        db_path = _make_sqlite_db(tmp_path, _MATCHING_ROWS)
        excel = _make_excel(tmp_path, _MATCHING_ROWS)
        result = _run(excel, db_path, "native")

        assert result["workflow"]["status"] == "passed"
        # Native writes BOTH sides to temp files (one per side).
        assert calls == [1, 1]


# ---------------------------------------------------------------------------
# (3) DUCKDB-MISSING SAFETY
# ---------------------------------------------------------------------------


class TestExcelDuckdbMissingSafety:
    def test_auto_without_duckdb_uses_native(self, tmp_path, monkeypatch) -> None:
        import importlib.util as _ilu

        real_find_spec = _ilu.find_spec

        def _no_duckdb(name, *args, **kwargs):
            if name == "duckdb":
                return None
            return real_find_spec(name, *args, **kwargs)

        monkeypatch.setattr(
            "src.comparators.backends.factory.importlib.util.find_spec", _no_duckdb
        )

        calls = []
        real = dbsvc._df_to_temp_file

        def _spy(df, delimiter="|"):
            calls.append(1)
            return real(df, delimiter)

        import src.services.excel_db_compare_service as exsvc

        monkeypatch.setattr(exsvc, "_df_to_temp_file", _spy)
        monkeypatch.setenv("COMPARISON_BACKEND", "auto")

        db_path = _make_sqlite_db(tmp_path, _MATCHING_ROWS)
        excel = _make_excel(tmp_path, _MATCHING_ROWS)
        result = _run(excel, db_path, None)

        assert result["workflow"]["status"] == "passed"
        assert calls == [1, 1]  # auto -> native -> two temp files
