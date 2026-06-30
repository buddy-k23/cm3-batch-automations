"""DuckDB-backend wiring for ``db-compare`` (S25-5) — written BEFORE implementation (TDD).

S25-5 threads comparison-backend selection through
:func:`~src.services.db_file_compare_service.compare_db_to_file` so that, when the
active backend is DuckDB (``COMPARISON_BACKEND=duckdb`` or an explicit
``backend="duckdb"``), the DB extract is registered **directly** into DuckDB and
diffed there — skipping the extract → temp-file → re-read hop the benchmark
flagged.  When the backend is native (the default), behaviour is **unchanged**:
the temp file is still written and the native engine reads it back.

These tests prove three things end-to-end against a real on-disk SQLite DB
(``DB_ADAPTER=sqlite``, seeded under ``tmp_path`` — never a tracked fixture):

1. **PARITY** (the core assertion): the result dict produced under
   ``backend="duckdb"`` is **identical** to the one produced under
   ``backend="native"`` on the same data (matching / only-in / differences /
   field_statistics), across the all-match, value-diff, and only-in cases.
2. **TEMP-FILE SKIP**: :func:`_df_to_temp_file` is **NOT** called when DuckDB is
   active (spied via monkeypatch) and **IS** called on the native path.
3. **DUCKDB-MISSING SAFETY**: with the ``duckdb`` import mocked absent, the
   service still works (falls back to native, temp file, no error).

The duckdb-active cases are gated with ``skipif`` on the optional ``duckdb``
package being importable.
"""

from __future__ import annotations

import sqlite3

import pytest

import src.services.db_file_compare_service as dbsvc
from src.services.db_file_compare_service import compare_db_to_file

try:  # optional dependency gate
    import duckdb  # noqa: F401

    _HAS_DUCKDB = True
except ImportError:  # pragma: no cover - exercised only on duckdb-less runners
    _HAS_DUCKDB = False

requires_duckdb = pytest.mark.skipif(
    not _HAS_DUCKDB, reason="duckdb not installed (pip install duckdb to run)"
)


# ---------------------------------------------------------------------------
# Fixtures — a real SQLite DB + matching flat files, all under tmp_path
# ---------------------------------------------------------------------------


@pytest.fixture()
def seeded_db(tmp_path):
    """Create and seed a real on-disk SQLite database under ``tmp_path``."""
    db_path = tmp_path / "db_duckdb_parity.db"
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
    return {
        "name": "accounts",
        "fields": [{"name": "ID"}, {"name": "NAME"}, {"name": "BALANCE"}],
    }


def _write_file(path, rows):
    """Write a pipe-delimited file with an ID|NAME|BALANCE header."""
    lines = ["ID|NAME|BALANCE"]
    lines.extend("|".join(str(c) for c in r) for r in rows)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def _run(actual, db_path, backend, monkeypatch):
    """Run compare_db_to_file with DB_ADAPTER=sqlite and an explicit backend."""
    monkeypatch.setenv("DB_ADAPTER", "sqlite")
    monkeypatch.setenv("DB_PATH", db_path)
    return compare_db_to_file(
        query_or_table="ACCOUNTS",
        mapping_config=_mapping(),
        actual_file=actual,
        key_columns=["ID"],
        backend=backend,
    )


def _normalize_compare(compare: dict) -> dict:
    """Normalise a compare dict so native and duckdb shapes compare equal.

    ``only_in_file*`` is a pandas DataFrame on both paths; convert to sorted
    record dicts.  ``differences`` is a list of dicts; sort by a stable key.
    Everything else is scalar / dict and compares directly.
    """
    import pandas as pd

    out = dict(compare)

    def _records(val):
        if isinstance(val, pd.DataFrame):
            recs = val.to_dict(orient="records")
            return sorted(recs, key=lambda r: tuple(sorted((str(k), str(v)) for k, v in r.items())))
        return val

    out["only_in_file1"] = _records(compare.get("only_in_file1"))
    out["only_in_file2"] = _records(compare.get("only_in_file2"))

    diffs = compare.get("differences")
    if isinstance(diffs, list):
        out["differences"] = sorted(
            diffs,
            key=lambda d: tuple(sorted((str(k), str(v)) for k, v in d.get("keys", {}).items())),
        )
    return out


# ---------------------------------------------------------------------------
# (1) PARITY — native == duckdb on identical data
# ---------------------------------------------------------------------------


@requires_duckdb
class TestNativeDuckdbParity:
    def test_all_match_parity(self, seeded_db, tmp_path, monkeypatch) -> None:
        """All-match case: native and duckdb result dicts are identical."""
        actual = _write_file(
            tmp_path / "match.txt",
            [(1, "alice", "100"), (2, "bob", "200"), (3, "carol", "300")],
        )
        native = _run(actual, seeded_db, "native", monkeypatch)
        duck = _run(actual, seeded_db, "duckdb", monkeypatch)

        assert native["workflow"]["status"] == duck["workflow"]["status"] == "passed"
        assert _normalize_compare(native["compare"]) == _normalize_compare(duck["compare"])

    def test_value_difference_parity(self, seeded_db, tmp_path, monkeypatch) -> None:
        """A seeded value diff yields identical native/duckdb diff payloads."""
        actual = _write_file(
            tmp_path / "diff.txt",
            [(1, "alice", "100"), (2, "bob", "999"), (3, "carol", "300")],
        )
        native = _run(actual, seeded_db, "native", monkeypatch)
        duck = _run(actual, seeded_db, "duckdb", monkeypatch)

        assert native["workflow"]["status"] == duck["workflow"]["status"] == "failed"
        assert _normalize_compare(native["compare"]) == _normalize_compare(duck["compare"])

    def test_only_in_each_side_parity(self, seeded_db, tmp_path, monkeypatch) -> None:
        """Rows only-in-DB and only-in-file land identically on both backends."""
        # Drop carol (only-in-DB), add a row id=9 (only-in-file).
        actual = _write_file(
            tmp_path / "onlyin.txt",
            [(1, "alice", "100"), (2, "bob", "200"), (9, "dave", "900")],
        )
        native = _run(actual, seeded_db, "native", monkeypatch)
        duck = _run(actual, seeded_db, "duckdb", monkeypatch)

        assert _normalize_compare(native["compare"]) == _normalize_compare(duck["compare"])

    def test_env_var_selects_duckdb_parity(self, seeded_db, tmp_path, monkeypatch) -> None:
        """COMPARISON_BACKEND=duckdb (no explicit arg) also reaches parity."""
        actual = _write_file(
            tmp_path / "envmatch.txt",
            [(1, "alice", "100"), (2, "bob", "200"), (3, "carol", "300")],
        )
        native = _run(actual, seeded_db, "native", monkeypatch)

        monkeypatch.setenv("COMPARISON_BACKEND", "duckdb")
        duck = _run(actual, seeded_db, None, monkeypatch)  # backend=None -> env

        assert _normalize_compare(native["compare"]) == _normalize_compare(duck["compare"])


# ---------------------------------------------------------------------------
# (2) TEMP-FILE SKIP — proven via spy
# ---------------------------------------------------------------------------


class TestTempFileSkip:
    @requires_duckdb
    def test_temp_file_skipped_under_duckdb(self, seeded_db, tmp_path, monkeypatch) -> None:
        """_df_to_temp_file is NOT called when duckdb is the active backend."""
        calls = []
        real = dbsvc._df_to_temp_file

        def _spy(df, delimiter="|"):
            calls.append(1)
            return real(df, delimiter)

        monkeypatch.setattr(dbsvc, "_df_to_temp_file", _spy)

        actual = _write_file(
            tmp_path / "skip.txt",
            [(1, "alice", "100"), (2, "bob", "200"), (3, "carol", "300")],
        )
        result = _run(actual, seeded_db, "duckdb", monkeypatch)

        assert result["workflow"]["status"] == "passed"
        assert calls == [], "duckdb path must not write a temp file"

    def test_temp_file_used_under_native(self, seeded_db, tmp_path, monkeypatch) -> None:
        """_df_to_temp_file IS called on the native path (unchanged behaviour)."""
        calls = []
        real = dbsvc._df_to_temp_file

        def _spy(df, delimiter="|"):
            calls.append(1)
            return real(df, delimiter)

        monkeypatch.setattr(dbsvc, "_df_to_temp_file", _spy)

        actual = _write_file(
            tmp_path / "native_temp.txt",
            [(1, "alice", "100"), (2, "bob", "200"), (3, "carol", "300")],
        )
        result = _run(actual, seeded_db, "native", monkeypatch)

        assert result["workflow"]["status"] == "passed"
        assert calls == [1], "native path must write exactly one temp file"


# ---------------------------------------------------------------------------
# (3) DUCKDB-MISSING SAFETY — service still works (native), no error
# ---------------------------------------------------------------------------


class TestDuckdbMissingSafety:
    def test_auto_backend_without_duckdb_uses_native(
        self, seeded_db, tmp_path, monkeypatch
    ) -> None:
        """With duckdb un-importable, COMPARISON_BACKEND=auto resolves native.

        ``find_spec`` is patched to report duckdb absent so the auto resolver
        never imports it — the service runs the native temp-file path with no
        error regardless of whether duckdb is physically installed.
        """
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

        monkeypatch.setattr(dbsvc, "_df_to_temp_file", _spy)
        monkeypatch.setenv("COMPARISON_BACKEND", "auto")

        actual = _write_file(
            tmp_path / "auto.txt",
            [(1, "alice", "100"), (2, "bob", "200"), (3, "carol", "300")],
        )
        result = _run(actual, seeded_db, None, monkeypatch)

        assert result["workflow"]["status"] == "passed"
        # auto -> native (duckdb reported absent), so the temp file IS written.
        assert calls == [1]
