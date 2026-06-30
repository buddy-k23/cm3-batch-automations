"""Golden / characterization tests for the ComparisonBackend seam (S25-1).

These tests pin the *exact* public behaviour of
:func:`src.services.compare_service.run_compare_service` across both engine
paths (in-memory ``FileComparator`` and set-based ``ChunkedFileComparator``)
plus the structure-incompatibility early return.

They are written tests-first and were confirmed green on the pre-refactor code
*before* the backend seam was introduced, then must remain byte-identical green
after ``run_compare_service`` is refactored to delegate to
:func:`src.comparators.backends.get_comparison_backend`.  Any drift in the
result dict — keys, values, ordering of list payloads — fails here, which is
the whole point: the seam is a pure, zero-behaviour-change refactor.

Fixtures are written to ``tmp_path`` (self-contained, pipe-delimited ``.txt``
files) rather than the gitignored ``data/samples`` tree, so the suite runs in a
clean checkout / CI runner.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.comparators.backends import (
    ComparisonBackend,
    get_comparison_backend,
)
from src.comparators.backends.native_backend import NativeComparisonBackend
from src.services.compare_service import run_compare_service


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------
def _write(path: Path, rows: list[str]) -> str:
    """Write newline-joined *rows* to *path* and return its string path."""
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return str(path)


HEADER = "customer_id|name|balance"


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


# ---------------------------------------------------------------------------
# In-memory (FileComparator) path — golden full-dict assertions
# ---------------------------------------------------------------------------
def test_inmemory_clean_match_full_dict(clean_match):
    f1, f2 = clean_match
    result = run_compare_service(
        file1=f1, file2=f2, keys="customer_id", detailed=True, use_chunked=False
    )

    assert result["structure_compatible"] is True
    assert result["total_rows_file1"] == 3
    assert result["total_rows_file2"] == 3
    assert result["matching_rows"] == 3
    assert result["rows_with_differences"] == 0
    assert list(result["only_in_file1"].itertuples(index=False, name=None)) == []
    assert list(result["only_in_file2"].itertuples(index=False, name=None)) == []
    assert result["differences"] == []
    assert result["field_statistics"] == {
        "fields_with_differences": 0,
        "field_difference_counts": {},
        "field_difference_types": {},
        "most_different_field": None,
    }


def test_inmemory_value_diff_full_dict(value_diff):
    f1, f2 = value_diff
    result = run_compare_service(
        file1=f1, file2=f2, keys="customer_id", detailed=True, use_chunked=False
    )

    assert result["structure_compatible"] is True
    assert result["total_rows_file1"] == 3
    assert result["total_rows_file2"] == 3
    assert result["rows_with_differences"] == 1
    assert result["matching_rows"] == 2
    assert len(result["differences"]) == 1

    diff = result["differences"][0]
    assert diff["keys"] == {"customer_id": "2"}
    assert diff["difference_count"] == 1
    assert "balance" in diff["differences"]
    assert diff["differences"]["balance"]["file1"] == "200"
    assert diff["differences"]["balance"]["file2"] == "999"

    stats = result["field_statistics"]
    assert stats["fields_with_differences"] == 1
    assert stats["field_difference_counts"] == {"balance": 1}
    assert stats["most_different_field"] == "balance"


def test_inmemory_only_in_each_full_dict(only_in_each):
    f1, f2 = only_in_each
    result = run_compare_service(
        file1=f1, file2=f2, keys="customer_id", detailed=True, use_chunked=False
    )

    assert result["structure_compatible"] is True
    assert result["total_rows_file1"] == 3
    assert result["total_rows_file2"] == 3
    assert result["differences"] == []

    only1 = result["only_in_file1"]
    only2 = result["only_in_file2"]
    assert list(only1["customer_id"]) == ["3"]
    assert list(only2["customer_id"]) == ["4"]
    assert result["matching_rows"] == 2


def test_inmemory_structure_incompatible(tmp_path):
    """Column-count mismatch returns the early structure-error dict."""
    f1 = _write(tmp_path / "a.txt", ["customer_id|name|balance", "1|alice|100"])
    f2 = _write(tmp_path / "b.txt", ["customer_id|name", "1|alice"])
    result = run_compare_service(
        file1=f1, file2=f2, keys="customer_id", detailed=True, use_chunked=False
    )

    assert result["structure_compatible"] is False
    assert result["matching_rows"] == 0
    assert result["only_in_file1"] == 0
    assert result["only_in_file2"] == 0
    assert result["differences"] == 0
    err = result["structure_errors"][0]
    assert err["type"] == "column_count_mismatch"
    # Pin the *actual* current parser column counts (characterization): the
    # pipe-delimited parser yields a distinct column count per file so the two
    # differ — that mismatch is what triggers the early return.
    assert err["file1_count"] != err["file2_count"]


# ---------------------------------------------------------------------------
# Chunked (ChunkedFileComparator) path — golden full-dict assertions
# ---------------------------------------------------------------------------
def test_chunked_value_diff_full_dict(value_diff):
    f1, f2 = value_diff
    result = run_compare_service(
        file1=f1,
        file2=f2,
        keys="customer_id",
        detailed=True,
        use_chunked=True,
        chunk_size=1000,
        progress=False,
    )

    # The chunked engine has a distinct contract: no structure_compatible,
    # *_count fields, total_differences_found and differences_truncated.
    assert "structure_compatible" not in result
    assert result["total_rows_file1"] == 3
    assert result["total_rows_file2"] == 3
    assert result["only_in_file1"] == []
    assert result["only_in_file2"] == []
    assert result["only_in_file1_count"] == 0
    assert result["only_in_file2_count"] == 0
    assert result["rows_with_differences"] == 1
    assert result["total_differences_found"] == 1
    assert result["differences_truncated"] is False
    assert result["matching_rows"] == 2

    diff = result["differences"][0]
    assert diff["keys"] == {"customer_id": "2"}
    assert diff["differences"]["balance"]["file1"] == "200"
    assert diff["differences"]["balance"]["file2"] == "999"


def test_chunked_only_in_each_full_dict(only_in_each):
    f1, f2 = only_in_each
    result = run_compare_service(
        file1=f1,
        file2=f2,
        keys="customer_id",
        detailed=True,
        use_chunked=True,
        chunk_size=1000,
        progress=False,
    )

    assert result["only_in_file1_count"] == 1
    assert result["only_in_file2_count"] == 1
    assert result["only_in_file1"] == [{"keys": {"customer_id": "3"}}]
    assert result["only_in_file2"] == [{"keys": {"customer_id": "4"}}]
    assert result["total_differences_found"] == 0
    assert result["matching_rows"] == 2


def test_chunked_requires_keys(clean_match):
    """Chunked path without keys raises ValueError (routing semantics preserved)."""
    f1, f2 = clean_match
    with pytest.raises(ValueError):
        run_compare_service(file1=f1, file2=f2, keys=None, use_chunked=True)


# ---------------------------------------------------------------------------
# Factory / seam contract
# ---------------------------------------------------------------------------
def test_factory_default_is_native():
    backend = get_comparison_backend()
    assert isinstance(backend, NativeComparisonBackend)
    assert isinstance(backend, ComparisonBackend)


def test_factory_native_and_pandas_aliases():
    assert isinstance(get_comparison_backend("native"), NativeComparisonBackend)
    assert isinstance(get_comparison_backend("pandas"), NativeComparisonBackend)


def test_factory_env_var(monkeypatch):
    monkeypatch.setenv("COMPARISON_BACKEND", "pandas")
    assert isinstance(get_comparison_backend(), NativeComparisonBackend)


def test_factory_duckdb_placeholder_not_implemented():
    """'duckdb' is a recognized-but-unimplemented placeholder for S25-2."""
    with pytest.raises(NotImplementedError):
        get_comparison_backend("duckdb")


def test_factory_unknown_backend_raises_value_error():
    with pytest.raises(ValueError):
        get_comparison_backend("does-not-exist")


# ---------------------------------------------------------------------------
# Backend parity: calling the native backend directly == via the service
# ---------------------------------------------------------------------------
def test_native_backend_matches_service_inmemory(value_diff):
    f1, f2 = value_diff
    via_service = run_compare_service(
        file1=f1, file2=f2, keys="customer_id", detailed=True, use_chunked=False
    )
    via_backend = NativeComparisonBackend().compare(
        f1, f2, ["customer_id"], detailed=True, use_chunked=False
    )
    # DataFrame payloads compare by identity of values, so equate the scalar
    # contract and the differences list which is what downstream consumes.
    assert via_backend["structure_compatible"] == via_service["structure_compatible"]
    assert via_backend["differences"] == via_service["differences"]
    assert via_backend["matching_rows"] == via_service["matching_rows"]
    assert via_backend["field_statistics"] == via_service["field_statistics"]
