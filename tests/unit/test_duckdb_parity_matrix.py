"""Exhaustive native-vs-DuckDB parity matrix (S25-3) — the merge GATE.

This module is the **gate** that lets the DuckDB-native-read fast path ship: a
single parametrized test runs *both* the
:class:`~src.comparators.backends.native_backend.NativeComparisonBackend` and the
:class:`~src.comparators.backends.duckdb_backend.DuckDBComparisonBackend` on the
**same** inputs and asserts the **full materialized result dicts are equal**.
Any divergence — counts, ``only_in_*`` rows, per-row field ``differences``,
``string_analysis``, ``field_statistics`` *or* ``source_row_file*`` — FAILS.

Order independence
------------------
The native in-memory path returns ``only_in_file1`` / ``only_in_file2`` as pandas
DataFrames and emits ``differences`` in pandas-merge order; the DuckDB fast path
reproduces the *same data* but list/row ordering is not contractually guaranteed
across engines.  :func:`_normalize` therefore converts the only-in DataFrames to
record dicts and sorts both the only-in rows and the ``differences`` list by a
stable key before comparison (same approach S25-2 used).  The duplicate-key case
additionally pins ``source_row_file*`` *within* the sort key so an enumeration
regression is still caught (see :func:`_diff_sort_key`).

All cases below run on **pipe** (``.txt``) inputs.  The comma ``.csv`` header
regime is deliberately excluded because the native engine is *broken* there (it
re-reads header keys with ``sep='|'`` and mis-parses the comma file); that
"replicate don't improve" behaviour is pinned separately in
``test_duckdb_backend.py::test_duckdb_csv_keyed_matches_native_behavior``.

Gated with ``skipif`` on the optional ``duckdb`` package being importable.
"""

from __future__ import annotations

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
# Helpers
# ---------------------------------------------------------------------------
def _write(path: Path, rows: list[str]) -> str:
    """Write newline-joined *rows* to *path* and return its string path."""
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return str(path)


def _diff_sort_key(diff: dict) -> tuple:
    """Stable, order-independent sort key for one ``differences`` entry.

    Sorts by the key tuple, then the stringified per-field differences, then the
    ``source_row_file*`` pair.  Including the source rows here means two diff
    lists that disagree *only* on source-row enumeration (the duplicate-key edge
    case) sort into different orders and the equality assertion still fails —
    i.e. this normalization does NOT mask a source-row regression.
    """
    return (
        tuple(sorted(diff.get("keys", {}).items())),
        str(diff.get("differences")),
        diff.get("source_row_file1"),
        diff.get("source_row_file2"),
    )


def _normalize(result: dict) -> dict:
    """Materialize only-in DataFrames to sorted record lists and sort diffs.

    Args:
        result: A comparison result dict from either backend.

    Returns:
        A shallow copy with ``only_in_file1`` / ``only_in_file2`` converted to
        order-independent sorted lists of record dicts and ``differences``
        sorted by :func:`_diff_sort_key`.
    """
    out = dict(result)
    for col in ("only_in_file1", "only_in_file2"):
        val = out.get(col)
        if hasattr(val, "to_dict"):
            records = val.to_dict("records")
            out[col] = sorted(records, key=lambda r: tuple(sorted(r.items())))
    diffs = out.get("differences")
    if isinstance(diffs, list):
        out["differences"] = sorted(diffs, key=_diff_sort_key)
    return out


def _assert_full_parity(native: dict, duck: dict) -> None:
    """Assert the two result dicts are equal across every contract field."""
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


# ---------------------------------------------------------------------------
# The matrix.  Each entry: (id, header, file1_rows, file2_rows, key_columns).
# Rows are the DATA rows (header is prepended).  Pipe-delimited throughout.
# ---------------------------------------------------------------------------
H = "customer_id|name|balance"

_CASES: list[tuple[str, str, list[str], list[str], list[str]]] = [
    # 1. Clean match — no diffs, no only-in.
    (
        "clean_match",
        H,
        ["1|alice|100", "2|bob|200", "3|carol|300"],
        ["1|alice|100", "2|bob|200", "3|carol|300"],
        ["customer_id"],
    ),
    # 2. Single-column value diff.
    (
        "single_value_diff",
        H,
        ["1|alice|100", "2|bob|200", "3|carol|300"],
        ["1|alice|100", "2|bob|999", "3|carol|300"],
        ["customer_id"],
    ),
    # 3. Multi-column value diff in one row.
    (
        "multi_col_diff",
        H,
        ["1|alice|100", "2|bob|200"],
        ["1|alice|100", "2|robert|999"],
        ["customer_id"],
    ),
    # 4. only_in file1 only.
    (
        "only_in_file1",
        H,
        ["1|alice|100", "2|bob|200", "3|carol|300"],
        ["1|alice|100", "2|bob|200"],
        ["customer_id"],
    ),
    # 5. only_in file2 only.
    (
        "only_in_file2",
        H,
        ["1|alice|100", "2|bob|200"],
        ["1|alice|100", "2|bob|200", "9|zoe|900"],
        ["customer_id"],
    ),
    # 6. only_in on BOTH sides plus a diff.
    (
        "only_in_both_plus_diff",
        H,
        ["1|alice|100", "2|bob|200", "3|carol|300"],
        ["1|alice|111", "2|bob|200", "4|dave|400"],
        ["customer_id"],
    ),
    # 7. NULL vs empty-string: file1 has empty name, file2 has a value.
    (
        "null_vs_empty",
        H,
        ["1||100", "2|bob|200"],
        ["1|x|100", "2|bob|200"],
        ["customer_id"],
    ),
    # 8. Both sides empty-string in the same field (must be EQUAL, not a diff).
    (
        "both_empty_equal",
        H,
        ["1||100", "2|bob|200"],
        ["1||100", "2|bob|200"],
        ["customer_id"],
    ),
    # 9. Leading-zero / numeric-looking keys: '007' must NOT collapse to 7.
    (
        "leading_zero_keys",
        H,
        ["007|alice|100", "08|bob|200", "9|carol|300"],
        ["007|alice|100", "08|bob|999", "9|carol|300"],
        ["customer_id"],
    ),
    # 10. Numeric-looking values that must stay textual ('1.0' != '1').
    (
        "numeric_string_values",
        H,
        ["1|alice|1.0", "2|bob|10"],
        ["1|alice|1", "2|bob|10"],
        ["customer_id"],
    ),
    # 11. Embedded delimiter inside a quoted field.
    (
        "embedded_delimiter",
        "customer_id|name|note",
        ['1|"a|b"|x', "2|bob|y"],
        ['1|"a|b"|x', "2|bob|z"],
        ["customer_id"],
    ),
    # 12. Embedded quotes (doubled) inside a quoted field.
    (
        "embedded_quotes",
        "customer_id|name|note",
        ['1|"say ""hi"""|x', "2|bob|y"],
        ['1|"say ""hi"""|x', "2|bob|z"],
        ["customer_id"],
    ),
    # 13. Whitespace-only difference (string_analysis whitespace_diff=True).
    (
        "whitespace_only_diff",
        H,
        ["1|alice|100", "2|bob| 200"],
        ["1|alice|100", "2|bob|200"],
        ["customer_id"],
    ),
    # 14. Case-only difference (string_analysis case_only=True).
    (
        "case_only_diff",
        H,
        ["1|Alice|100", "2|bob|200"],
        ["1|alice|100", "2|bob|200"],
        ["customer_id"],
    ),
    # 15. Duplicate key values (the canonicalized cartesian + source_row case).
    (
        "duplicate_keys",
        H,
        ["1|alice|100", "2|bob|200", "2|bobby|201", "3|carol|300"],
        ["1|alice|100", "2|bob|999", "2|bobby|201", "3|carol|300"],
        ["customer_id"],
    ),
    # 16. Duplicate keys with asymmetric multiplicity (2 vs 3 copies of key 2).
    (
        "duplicate_keys_asymmetric",
        H,
        ["2|bob|200", "2|bobby|201", "1|alice|100"],
        ["2|bob|999", "2|bobby|201", "2|bee|202", "1|alice|100"],
        ["customer_id"],
    ),
    # 17. Multi-column composite key.
    (
        "composite_key",
        "region|customer_id|name|balance",
        ["US|1|alice|100", "US|2|bob|200", "EU|1|hans|300"],
        ["US|1|alice|100", "US|2|bob|999", "EU|1|hans|300"],
        ["region", "customer_id"],
    ),
    # 18. Header-only (no data rows) on both sides.
    (
        "header_only",
        H,
        [],
        [],
        ["customer_id"],
    ),
    # 19. Header-only file1, data in file2 (everything only_in_file2).
    (
        "header_only_vs_data",
        H,
        [],
        ["1|alice|100", "2|bob|200"],
        ["customer_id"],
    ),
    # 20. Narrow: single value column.
    (
        "narrow_two_col",
        "id|v",
        ["1|a", "2|b", "3|c"],
        ["1|a", "2|X", "3|c"],
        ["id"],
    ),
]


def _build_wide_case() -> tuple[str, str, list[str], list[str], list[str]]:
    """Build a 120-column wide case (1 key + 119 value cols) with a few diffs."""
    n_val = 119
    cols = ["customer_id"] + [f"col{i:03d}" for i in range(n_val)]
    header = "|".join(cols)

    def row(key: str, seed: int) -> str:
        vals = [key] + [f"v{(seed + i) % 7}" for i in range(n_val)]
        return "|".join(vals)

    f1 = [row(f"{k}", k) for k in range(5)]
    # file2: mutate two value columns of row key=2.
    f2_rows = []
    for k in range(5):
        if k == 2:
            vals = [f"{k}"] + [f"v{(k + i) % 7}" for i in range(n_val)]
            vals[10] = "CHANGED10"
            vals[50] = "CHANGED50"
            f2_rows.append("|".join(vals))
        else:
            f2_rows.append(row(f"{k}", k))
    return ("wide_120col", header, f1, f2_rows, ["customer_id"])


_CASES.append(_build_wide_case())


# ---------------------------------------------------------------------------
# The gate.
# ---------------------------------------------------------------------------
@requires_duckdb
@pytest.mark.parametrize(
    "case", _CASES, ids=[c[0] for c in _CASES]
)
@pytest.mark.parametrize("detailed", [True, False], ids=["detailed", "plain"])
def test_native_duckdb_full_parity(tmp_path, case, detailed):
    """Native and DuckDB produce equal full result dicts for every case.

    The single most important test in S25-3: with ``duckdb`` installed every
    parametrized input must yield ``native == duckdb`` across the entire
    materialized contract (and at both ``detailed`` settings).
    """
    from src.comparators.backends.duckdb_backend import DuckDBComparisonBackend

    case_id, header, rows1, rows2, keys = case
    f1 = _write(tmp_path / f"{case_id}_1.txt", [header, *rows1])
    f2 = _write(tmp_path / f"{case_id}_2.txt", [header, *rows2])

    native = NativeComparisonBackend().compare(f1, f2, keys, detailed=detailed)
    duck = DuckDBComparisonBackend().compare(f1, f2, keys, detailed=detailed)

    if detailed:
        _assert_full_parity(native, duck)
    else:
        # detailed=False: field_statistics is {} and diffs carry only file1/file2.
        n, d = _normalize(native), _normalize(duck)
        assert d["matching_rows"] == n["matching_rows"]
        assert d["rows_with_differences"] == n["rows_with_differences"]
        assert d["only_in_file1"] == n["only_in_file1"]
        assert d["only_in_file2"] == n["only_in_file2"]
        assert d["differences"] == n["differences"]
        assert d["field_statistics"] == n["field_statistics"] == {}


@requires_duckdb
def test_duplicate_key_source_rows_canonicalized(tmp_path):
    """Duplicate-key ``source_row_file*`` is reproduced EXACTLY, not just counts.

    S25-2 could only guarantee everything-except-source_row for duplicate keys.
    S25-3's native-read fast path carries each side's physical row order and
    orders the cartesian join by ``(file1_row, file2_row)`` — pandas' inner-merge
    enumeration order.  In this header-keyed regime native's
    ``FileComparator._find_detailed_differences`` has no ``__source_row__``
    column, so it emits the merged-frame 1-based *position* (``idx + 1``) for
    BOTH ``source_row_file1`` and ``source_row_file2`` (the two are equal).  By
    enumerating the cartesian rows in the identical order the fast path emits the
    identical positions — so the FULL differences list (source rows included)
    is equal, the stronger claim S25-2 could not make.
    """
    from src.comparators.backends.duckdb_backend import DuckDBComparisonBackend

    f1 = _write(
        tmp_path / "dup1.txt",
        [H, "1|alice|100", "2|bob|200", "2|bobby|201", "3|carol|300"],
    )
    f2 = _write(
        tmp_path / "dup2.txt",
        [H, "1|alice|100", "2|bob|999", "2|bobby|201", "3|carol|300"],
    )
    native = NativeComparisonBackend().compare(f1, f2, ["customer_id"], detailed=True)
    duck = DuckDBComparisonBackend().compare(f1, f2, ["customer_id"], detailed=True)

    # Sort by the SAME stable key (which includes source rows) on both sides and
    # require list equality — a source-row divergence would fail this.
    n_diffs = sorted(native["differences"], key=_diff_sort_key)
    d_diffs = sorted(duck["differences"], key=_diff_sort_key)
    assert d_diffs == n_diffs

    # And explicitly: every (source_row_file1, source_row_file2) pair native
    # produced is present in duck's output with identical field differences.
    n_by_src = {
        (x["source_row_file1"], x["source_row_file2"]): x["differences"]
        for x in native["differences"]
    }
    d_by_src = {
        (x["source_row_file1"], x["source_row_file2"]): x["differences"]
        for x in duck["differences"]
    }
    assert d_by_src == n_by_src
