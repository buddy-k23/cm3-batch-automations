"""Tests for the DuckDB comparison backend (S25-2).

These tests pin the **full materialized result contract** the DuckDB backend
must reproduce — byte-for-byte identical to what
:class:`~src.comparators.backends.native_backend.NativeComparisonBackend`
returns for the same delimited inputs.  The strongest assertions run *both*
backends on the same files and assert the result dicts are equal (a preview of
the exhaustive S25-3 parity matrix).

Tests that exercise the DuckDB engine itself are gated with ``skipif`` on the
optional ``duckdb`` package being importable.  Two tests are *not* gated: they
assert the module imports cleanly without duckdb installed and that selecting
the backend without duckdb raises a clear, actionable install error (simulated
by monkeypatching the import machinery).
"""

from __future__ import annotations

import builtins
import importlib
from pathlib import Path

import pytest

from src.comparators.backends.native_backend import NativeComparisonBackend

try:  # optional dependency gate
    import duckdb  # noqa: F401

    _HAS_DUCKDB = True
except ImportError:  # pragma: no cover - exercised only on duckdb-less runners
    _HAS_DUCKDB = False

requires_duckdb = pytest.mark.skipif(
    not _HAS_DUCKDB, reason="duckdb not installed (pip install duckdb to run)"
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------
HEADER = "customer_id|name|balance"


def _write(path: Path, rows: list[str]) -> str:
    """Write newline-joined *rows* to *path* and return its string path."""
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return str(path)


@pytest.fixture()
def clean_match(tmp_path):
    """Two byte-identical 3-row keyed files (no diffs, no only-in)."""
    rows = [HEADER, "1|alice|100", "2|bob|200", "3|carol|300"]
    f1 = _write(tmp_path / "a.txt", rows)
    f2 = _write(tmp_path / "b.txt", rows)
    return f1, f2


@pytest.fixture()
def value_diff(tmp_path):
    """Same keys; row 2 has a changed ``balance`` value in file2."""
    f1 = _write(tmp_path / "a.txt", [HEADER, "1|alice|100", "2|bob|200", "3|carol|300"])
    f2 = _write(tmp_path / "b.txt", [HEADER, "1|alice|100", "2|bob|999", "3|carol|300"])
    return f1, f2


@pytest.fixture()
def only_in_each(tmp_path):
    """Asymmetric key sets: key 3 only in file1, key 4 only in file2."""
    f1 = _write(tmp_path / "a.txt", [HEADER, "1|alice|100", "2|bob|200", "3|carol|300"])
    f2 = _write(tmp_path / "b.txt", [HEADER, "1|alice|100", "2|bob|200", "4|dave|400"])
    return f1, f2


@pytest.fixture()
def mixed(tmp_path):
    """Diffs, only-in-each, empty-string and whitespace edge cases together."""
    f1 = _write(
        tmp_path / "a.txt",
        [HEADER, "1||100", "2|bob| 200", "3|carol|300", "5|eve|500"],
    )
    f2 = _write(
        tmp_path / "b.txt",
        [HEADER, "1|x|100", "2|bob|200", "3|carol|300", "6|frank|600"],
    )
    return f1, f2


def _normalize(result: dict) -> dict:
    """Return a comparison result with DataFrame only-in payloads materialized.

    The native in-memory path returns ``only_in_file1`` / ``only_in_file2`` as
    pandas DataFrames; the DuckDB backend reproduces the *same* DataFrames.  To
    compare two results structurally we convert those DataFrames to sorted lists
    of record dicts.  List ordering of ``only_in`` rows is not contractually
    guaranteed across engines, so we sort by the full record for a stable,
    order-independent comparison (documented in the backend module).
    """
    out = dict(result)
    for col in ("only_in_file1", "only_in_file2"):
        val = out.get(col)
        if hasattr(val, "to_dict"):
            records = val.to_dict("records")
            out[col] = sorted(records, key=lambda r: tuple(sorted(r.items())))
    # differences may also be ordered differently; sort by key tuple for compare.
    diffs = out.get("differences")
    if isinstance(diffs, list):
        out["differences"] = sorted(
            diffs, key=lambda d: tuple(sorted(d.get("keys", {}).items()))
        )
    return out


# ---------------------------------------------------------------------------
# Import-safety / missing-duckdb behaviour (NOT gated — run duckdb-less too)
# ---------------------------------------------------------------------------
def test_module_imports_without_duckdb_installed(monkeypatch):
    """The backend module imports fine even when duckdb is absent.

    The ``import duckdb`` must be lazy (inside the method), so importing the
    module never fails on a duckdb-less interpreter.  We simulate absence by
    making ``import duckdb`` raise and re-importing the module.
    """
    real_import = builtins.__import__

    def _no_duckdb(name, *args, **kwargs):
        if name == "duckdb":
            raise ImportError("No module named 'duckdb'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_duckdb)
    mod = importlib.import_module("src.comparators.backends.duckdb_backend")
    importlib.reload(mod)
    # The class is defined and instantiable without duckdb present.
    backend = mod.DuckDBComparisonBackend()
    assert backend is not None


def test_compare_without_duckdb_raises_clear_install_error(monkeypatch, value_diff):
    """Selecting the backend without duckdb raises an actionable install error."""
    from src.comparators.backends.duckdb_backend import DuckDBComparisonBackend

    real_import = builtins.__import__

    def _no_duckdb(name, *args, **kwargs):
        if name == "duckdb":
            raise ImportError("No module named 'duckdb'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_duckdb)

    f1, f2 = value_diff
    with pytest.raises(ImportError) as exc:
        DuckDBComparisonBackend().compare(f1, f2, ["customer_id"])
    msg = str(exc.value)
    assert "duckdb" in msg.lower()
    assert "pip install duckdb" in msg


# ---------------------------------------------------------------------------
# Factory wiring
# ---------------------------------------------------------------------------
@requires_duckdb
def test_factory_returns_duckdb_backend():
    from src.comparators.backends import get_comparison_backend
    from src.comparators.backends.duckdb_backend import DuckDBComparisonBackend

    backend = get_comparison_backend("duckdb")
    assert isinstance(backend, DuckDBComparisonBackend)


@requires_duckdb
def test_factory_duckdb_env_var(monkeypatch):
    from src.comparators.backends import get_comparison_backend
    from src.comparators.backends.duckdb_backend import DuckDBComparisonBackend

    monkeypatch.setenv("COMPARISON_BACKEND", "duckdb")
    assert isinstance(get_comparison_backend(), DuckDBComparisonBackend)


# ---------------------------------------------------------------------------
# Full-contract assertions on the DuckDB backend directly
# ---------------------------------------------------------------------------
@requires_duckdb
def test_duckdb_requires_keys(value_diff):
    """No key columns raises the SAME ValueError the native engine would.

    The native FileComparator falls back to row-by-row when keys are None; the
    DuckDB backend defers row-by-row to native, so passing keys is required for
    the DuckDB-native keyed path — assert the error type is ValueError.
    """
    from src.comparators.backends.duckdb_backend import DuckDBComparisonBackend

    f1, f2 = value_diff
    with pytest.raises(ValueError):
        DuckDBComparisonBackend().compare(f1, f2, None, use_chunked=False)


@requires_duckdb
def test_duckdb_clean_match_full_contract(clean_match):
    from src.comparators.backends.duckdb_backend import DuckDBComparisonBackend

    f1, f2 = clean_match
    r = DuckDBComparisonBackend().compare(f1, f2, ["customer_id"], detailed=True)

    assert r["structure_compatible"] is True
    assert r["total_rows_file1"] == 3
    assert r["total_rows_file2"] == 3
    assert r["matching_rows"] == 3
    assert r["rows_with_differences"] == 0
    assert r["differences"] == []
    assert list(r["only_in_file1"].to_dict("records")) == []
    assert list(r["only_in_file2"].to_dict("records")) == []
    assert r["field_statistics"] == {
        "fields_with_differences": 0,
        "field_difference_counts": {},
        "field_difference_types": {},
        "most_different_field": None,
    }


@requires_duckdb
def test_duckdb_value_diff_full_contract(value_diff):
    from src.comparators.backends.duckdb_backend import DuckDBComparisonBackend

    f1, f2 = value_diff
    r = DuckDBComparisonBackend().compare(f1, f2, ["customer_id"], detailed=True)

    assert r["structure_compatible"] is True
    assert r["rows_with_differences"] == 1
    assert r["matching_rows"] == 2
    assert len(r["differences"]) == 1

    diff = r["differences"][0]
    assert diff["keys"] == {"customer_id": "2"}
    assert diff["difference_count"] == 1
    assert diff["source_row_file1"] == 2
    assert diff["source_row_file2"] == 2
    bal = diff["differences"]["balance"]
    assert bal["file1"] == "200"
    assert bal["file2"] == "999"
    assert bal["type"] == "value_difference"
    assert bal["string_analysis"] == {
        "length_diff": 0,
        "case_only": False,
        "whitespace_diff": False,
        "length_file1": 3,
        "length_file2": 3,
    }

    stats = r["field_statistics"]
    assert stats["fields_with_differences"] == 1
    assert stats["field_difference_counts"] == {"balance": 1}
    assert stats["field_difference_types"] == {"balance": {"value_difference": 1}}
    assert stats["most_different_field"] == "balance"


@requires_duckdb
def test_duckdb_only_in_each_full_contract(only_in_each):
    from src.comparators.backends.duckdb_backend import DuckDBComparisonBackend

    f1, f2 = only_in_each
    r = DuckDBComparisonBackend().compare(f1, f2, ["customer_id"], detailed=True)

    assert r["differences"] == []
    assert r["matching_rows"] == 2
    o1 = r["only_in_file1"].to_dict("records")
    o2 = r["only_in_file2"].to_dict("records")
    assert o1 == [{"customer_id": "3", "name": "carol", "balance": "300"}]
    assert o2 == [{"customer_id": "4", "name": "dave", "balance": "400"}]


@requires_duckdb
def test_duckdb_detailed_false_skips_analysis(value_diff):
    """detailed=False omits string_analysis / type / difference_count / stats."""
    from src.comparators.backends.duckdb_backend import DuckDBComparisonBackend

    f1, f2 = value_diff
    r = DuckDBComparisonBackend().compare(f1, f2, ["customer_id"], detailed=False)
    diff = r["differences"][0]
    assert diff["differences"]["balance"] == {"file1": "200", "file2": "999"}
    assert "string_analysis" not in diff["differences"]["balance"]
    assert "difference_count" not in diff
    assert r["field_statistics"] == {}


# ---------------------------------------------------------------------------
# Native-vs-DuckDB parity (preview of S25-3 matrix)
# ---------------------------------------------------------------------------
@requires_duckdb
@pytest.mark.parametrize("fixture_name", ["clean_match", "value_diff", "only_in_each", "mixed"])
def test_duckdb_equals_native(request, fixture_name):
    """Both backends produce structurally equal result dicts for the same input."""
    from src.comparators.backends.duckdb_backend import DuckDBComparisonBackend

    f1, f2 = request.getfixturevalue(fixture_name)
    native = NativeComparisonBackend().compare(f1, f2, ["customer_id"], detailed=True)
    duck = DuckDBComparisonBackend().compare(f1, f2, ["customer_id"], detailed=True)

    n, d = _normalize(native), _normalize(duck)
    assert d["structure_compatible"] == n["structure_compatible"]
    assert d["total_rows_file1"] == n["total_rows_file1"]
    assert d["total_rows_file2"] == n["total_rows_file2"]
    assert d["matching_rows"] == n["matching_rows"]
    assert d["rows_with_differences"] == n["rows_with_differences"]
    assert d["only_in_file1"] == n["only_in_file1"]
    assert d["only_in_file2"] == n["only_in_file2"]
    assert d["differences"] == n["differences"]
    assert d["field_statistics"] == n["field_statistics"]


@requires_duckdb
def test_duckdb_equals_native_detailed_false(mixed):
    from src.comparators.backends.duckdb_backend import DuckDBComparisonBackend

    f1, f2 = mixed
    native = NativeComparisonBackend().compare(f1, f2, ["customer_id"], detailed=False)
    duck = DuckDBComparisonBackend().compare(f1, f2, ["customer_id"], detailed=False)
    n, d = _normalize(native), _normalize(duck)
    assert d["differences"] == n["differences"]
    assert d["matching_rows"] == n["matching_rows"]
    assert d["only_in_file1"] == n["only_in_file1"]
    assert d["only_in_file2"] == n["only_in_file2"]


@requires_duckdb
def test_duckdb_csv_keyed_matches_native_behavior(tmp_path):
    """A header-keyed ``.csv`` compare reproduces native's behavior exactly.

    The native delimited path resolves header key columns by re-reading with a
    *pipe* separator, which mis-parses a comma ``.csv`` into a single column and
    raises during the pandas merge.  Per the drop-in contract ("replicate its
    behavior, don't improve on it"), the DuckDB backend defers this unresolved
    case to native and therefore raises the SAME error — it does not silently
    "fix" the comma parse.  See the S25-3 flag in the story return.
    """
    from src.comparators.backends.duckdb_backend import DuckDBComparisonBackend

    rows1 = ["customer_id,name,balance", "1,alice,100", "2,bob,200"]
    rows2 = ["customer_id,name,balance", "1,alice,100", "2,bob,999"]
    f1 = _write(tmp_path / "a.csv", rows1)
    f2 = _write(tmp_path / "b.csv", rows2)

    native_exc = None
    try:
        NativeComparisonBackend().compare(f1, f2, ["customer_id"], detailed=True)
    except Exception as e:  # noqa: BLE001 - capturing for parity assertion
        native_exc = type(e)

    duck_exc = None
    try:
        DuckDBComparisonBackend().compare(f1, f2, ["customer_id"], detailed=True)
    except Exception as e:  # noqa: BLE001 - capturing for parity assertion
        duck_exc = type(e)

    assert duck_exc == native_exc


# ---------------------------------------------------------------------------
# Deferral cases — DuckDB cleanly hands off to native for parity
# ---------------------------------------------------------------------------
@requires_duckdb
def test_duckdb_structure_incompatible_matches_native(tmp_path):
    """Structure-incompatible inputs return the SAME early-return dict as native."""
    from src.comparators.backends.duckdb_backend import DuckDBComparisonBackend

    f1 = _write(tmp_path / "a.txt", ["customer_id|name|balance", "1|alice|100"])
    f2 = _write(tmp_path / "b.txt", ["customer_id|name", "1|alice"])
    native = NativeComparisonBackend().compare(f1, f2, ["customer_id"], detailed=True)
    duck = DuckDBComparisonBackend().compare(f1, f2, ["customer_id"], detailed=True)
    assert duck["structure_compatible"] is False
    assert duck == native


@requires_duckdb
def test_duckdb_duplicate_keys_parity_except_source_row(tmp_path):
    """Duplicate keys: counts/keys/field-diffs match native; source_row may differ.

    With a duplicate key both engines emit the same cartesian set of difference
    rows (same count, same ``keys``, same per-field ``file1``/``file2`` values)
    and the same ``matching_rows``.  Only the ``source_row_file*`` *index* on
    the cartesian rows can differ, because pandas' inner-merge enumeration order
    and DuckDB's join order differ for repeated keys.  This is a documented
    edge case flagged for S25-3 to nail down (native's per-row source index for
    duplicate keys is itself ambiguous).
    """
    from src.comparators.backends.duckdb_backend import DuckDBComparisonBackend

    f1 = _write(
        tmp_path / "a.txt",
        [HEADER, "1|alice|100", "2|bob|200", "2|bobby|201", "3|carol|300"],
    )
    f2 = _write(
        tmp_path / "b.txt",
        [HEADER, "1|alice|100", "2|bob|999", "2|bobby|201", "3|carol|300"],
    )
    native = NativeComparisonBackend().compare(f1, f2, ["customer_id"], detailed=True)
    duck = DuckDBComparisonBackend().compare(f1, f2, ["customer_id"], detailed=True)

    assert duck["matching_rows"] == native["matching_rows"]
    assert duck["rows_with_differences"] == native["rows_with_differences"]
    assert duck["field_statistics"] == native["field_statistics"]

    def _stripped(diffs):
        return sorted(
            ({"keys": tuple(sorted(x["keys"].items())), "differences": x["differences"]}
             for x in diffs),
            key=lambda r: (r["keys"], str(r["differences"])),
        )

    assert _stripped(duck["differences"]) == _stripped(native["differences"])


@requires_duckdb
def test_duckdb_chunked_defers_to_native(value_diff):
    """use_chunked=True defers to the native set-based engine for parity."""
    from src.comparators.backends.duckdb_backend import DuckDBComparisonBackend

    f1, f2 = value_diff
    native = NativeComparisonBackend().compare(
        f1, f2, ["customer_id"], detailed=True, use_chunked=True, progress=False
    )
    duck = DuckDBComparisonBackend().compare(
        f1, f2, ["customer_id"], detailed=True, use_chunked=True, progress=False
    )
    assert duck == native


# ---------------------------------------------------------------------------
# S25-5: compare_frames — frame-direct entry (zero temp-file) parity
# ---------------------------------------------------------------------------


@requires_duckdb
def test_compare_frames_matches_temp_file_native(tmp_path):
    """compare_frames(df1, df2) == native reading the SAME frames via a temp file.

    Proves the S25-5 frame-direct path is byte-parity with the temp-file +
    native path it replaces: build two typed frames, run them through
    compare_frames, and compare against writing each frame to a pipe file and
    running the native backend over the files.
    """
    import pandas as pd

    from src.comparators.backends.duckdb_backend import DuckDBComparisonBackend
    from src.services.db_file_compare_service import _df_to_temp_file

    df1 = pd.DataFrame(
        [
            {"ID": 1, "NAME": "alice", "BALANCE": 100.0},
            {"ID": 2, "NAME": "bob", "BALANCE": 200.0},
            {"ID": 3, "NAME": "carol", "BALANCE": 300.0},
        ]
    )
    df2 = pd.DataFrame(
        [
            {"ID": 1, "NAME": "alice", "BALANCE": 100.0},
            {"ID": 2, "NAME": "bob", "BALANCE": 999.0},  # diff
            {"ID": 9, "NAME": "dave", "BALANCE": 900.0},  # only-in-file2
        ]
    )

    # Native via the exact temp-file round-trip the service uses today.
    t1 = _df_to_temp_file(df1)
    t2 = _df_to_temp_file(df2)
    try:
        native = NativeComparisonBackend().compare(t1, t2, ["ID"], detailed=True)
    finally:
        Path(t1).unlink(missing_ok=True)
        Path(t2).unlink(missing_ok=True)

    duck = DuckDBComparisonBackend().compare_frames(df1, df2, ["ID"], detailed=True)

    def _norm(res):
        out = dict(res)
        for k in ("only_in_file1", "only_in_file2"):
            v = res[k]
            recs = v.to_dict(orient="records") if hasattr(v, "to_dict") else v
            out[k] = sorted(
                recs, key=lambda r: tuple(sorted((str(a), str(b)) for a, b in r.items()))
            )
        out["differences"] = sorted(
            res["differences"],
            key=lambda d: tuple(sorted((str(a), str(b)) for a, b in d["keys"].items())),
        )
        return out

    assert _norm(duck) == _norm(native)


@requires_duckdb
def test_compare_frames_requires_keys(tmp_path):
    """compare_frames raises ValueError when no key columns are given."""
    import pandas as pd

    from src.comparators.backends.duckdb_backend import DuckDBComparisonBackend

    df = pd.DataFrame([{"ID": 1, "V": "a"}])
    with pytest.raises(ValueError):
        DuckDBComparisonBackend().compare_frames(df, df, [], detailed=True)
