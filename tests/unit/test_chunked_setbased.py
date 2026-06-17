"""Tests for the set-based chunked comparator (S18-5, #423).

These verify the per-row SELECT has been replaced by a bounded number of
set-based SQL queries (JOIN / EXCEPT), that ``only_in_file1`` /
``only_in_file2`` are returned complete (they were previously empty), that
the difference cap is configurable (no silent hard 1000), and that the
chunked result stays at parity with the in-memory ``FileComparator``.
"""

from __future__ import annotations

import os
import tempfile

import pandas as pd
import pytest

from src.comparators.chunked_comparator import ChunkedFileComparator
from src.comparators.file_comparator import FileComparator


def _write(content: str) -> str:
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
        f.write(content)
        return f.name


@pytest.fixture
def file1_path():
    content = (
        "id|name|value|status\n"
        "1|Alice|100|active\n"
        "2|Bob|200|inactive\n"
        "3|Charlie|300|active\n"
        "4|David|400|active\n"
        "5|Eve|500|inactive"
    )
    path = _write(content)
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def file2_path():
    content = (
        "id|name|value|status\n"
        "1|Alice|150|active\n"        # value differs 100 -> 150
        "2|Bob|200|active\n"          # status differs inactive -> active
        "3|Charlie|300|active\n"      # identical
        "6|Frank|600|active"          # only in file2
    )
    path = _write(content)
    yield path
    if os.path.exists(path):
        os.unlink(path)


class TestCompletenessOfOnlyInLists:
    """``only_in_file1`` / ``only_in_file2`` are returned complete (were [])."""

    def test_only_in_file1_is_complete(self, file1_path, file2_path):
        comparator = ChunkedFileComparator(
            file1_path, file2_path, key_columns=["id"], chunk_size=2
        )
        results = comparator.compare(detailed=False, show_progress=False)

        # ids 4 and 5 are only in file1.
        only1_ids = sorted(r["keys"]["id"] for r in results["only_in_file1"])
        assert only1_ids == ["4", "5"]
        assert results["only_in_file1_count"] == 2

    def test_only_in_file2_is_complete(self, file1_path, file2_path):
        comparator = ChunkedFileComparator(
            file1_path, file2_path, key_columns=["id"], chunk_size=2
        )
        results = comparator.compare(detailed=False, show_progress=False)

        only2_ids = sorted(r["keys"]["id"] for r in results["only_in_file2"])
        assert only2_ids == ["6"]
        assert results["only_in_file2_count"] == 1

    def test_matches_and_mismatches_correct(self, file1_path, file2_path):
        comparator = ChunkedFileComparator(
            file1_path, file2_path, key_columns=["id"], chunk_size=2
        )
        results = comparator.compare(detailed=True, show_progress=False)

        # id 1 (value) and id 2 (status) differ; id 3 matches.
        assert results["rows_with_differences"] == 2
        assert results["matching_rows"] == 1
        diff_ids = sorted(d["keys"]["id"] for d in results["differences"])
        assert diff_ids == ["1", "2"]


class TestConfigurableCap:
    """The 1000-diff truncation is now configurable, not a silent hard cap."""

    def _make_divergent_pair(self, n_rows: int):
        header = "id|value\n"
        rows1 = [f"{i}|A{i}" for i in range(1, n_rows + 1)]
        rows2 = [f"{i}|B{i}" for i in range(1, n_rows + 1)]  # every row differs
        return _write(header + "\n".join(rows1)), _write(header + "\n".join(rows2))

    def test_diff_beyond_old_1000_cap_is_present_by_default(self):
        f1, f2 = self._make_divergent_pair(1100)
        try:
            comparator = ChunkedFileComparator(
                f1, f2, key_columns=["id"], chunk_size=500
            )
            results = comparator.compare(detailed=False, show_progress=False)
            # All 1100 differing rows retained (old code hard-capped at 1000).
            assert results["total_differences_found"] == 1100
            assert len(results["differences"]) == 1100
            assert results["differences_truncated"] is False
            returned_ids = {d["keys"]["id"] for d in results["differences"]}
            assert "1050" in returned_ids  # beyond the old 1000 cap
        finally:
            for p in (f1, f2):
                if os.path.exists(p):
                    os.unlink(p)

    def test_explicit_cap_truncates_and_flags(self):
        f1, f2 = self._make_divergent_pair(50)
        try:
            comparator = ChunkedFileComparator(
                f1, f2, key_columns=["id"], chunk_size=500, max_differences=10
            )
            results = comparator.compare(detailed=False, show_progress=False)
            assert len(results["differences"]) == 10
            assert results["differences_truncated"] is True
            assert results["total_differences_found"] == 50
        finally:
            for p in (f1, f2):
                if os.path.exists(p):
                    os.unlink(p)


class TestSetBasedNoPerRowSelect:
    """Prove the N-round-trip per-row SELECT is gone: bounded query count."""

    def test_query_count_bounded_regardless_of_rows(self):
        header = "id|value\n"
        n = 3000
        rows1 = [f"{i}|A{i}" for i in range(1, n + 1)]
        rows2 = [f"{i}|A{i}" for i in range(1, n + 1)]
        rows2[10] = "11|CHANGED"  # one diff
        f1 = _write(header + "\n".join(rows1))
        f2 = _write(header + "\n".join(rows2))
        try:
            comparator = ChunkedFileComparator(
                f1, f2, key_columns=["id"], chunk_size=1000
            )

            # sqlite3's trace callback fires once per executed statement; count
            # the data-querying SELECTs against the file1/file2 tables. A
            # per-row implementation would emit ~3000 such SELECTs.
            select_count = {"n": 0}

            def trace(stmt: str) -> None:
                upper = stmt.lstrip().upper()
                if upper.startswith("SELECT") and ("FILE1" in upper or "FILE2" in upper):
                    select_count["n"] += 1

            comparator.db_conn.set_trace_callback(trace)
            results = comparator.compare(detailed=False, show_progress=False)

            # Set-based issues a small constant number of SELECTs against the
            # data tables (the JOIN + two EXCEPT queries + their COUNT wrappers),
            # independent of row count.
            assert select_count["n"] < 20, (
                f"Expected bounded SELECT count, got {select_count['n']} "
                "— per-row SELECT may have regressed."
            )
            assert results["rows_with_differences"] == 1
        finally:
            for p in (f1, f2):
                if os.path.exists(p):
                    os.unlink(p)


class TestParityWithNonChunked:
    """Chunked result agrees with in-memory FileComparator on the same input."""

    def test_parity_matches_mismatches_and_only_in(self, file1_path, file2_path):
        chunked = ChunkedFileComparator(
            file1_path, file2_path, key_columns=["id"], chunk_size=2
        ).compare(detailed=True, show_progress=False)

        df1 = pd.read_csv(file1_path, sep="|", dtype=str, keep_default_na=False)
        df2 = pd.read_csv(file2_path, sep="|", dtype=str, keep_default_na=False)
        non_chunked = FileComparator(df1, df2, key_columns=["id"]).compare(detailed=True)

        # matching_rows + rows_with_differences parity
        assert chunked["matching_rows"] == non_chunked["matching_rows"]
        assert chunked["rows_with_differences"] == non_chunked["rows_with_differences"]

        # only_in sets parity (by key)
        chunked_only1 = sorted(r["keys"]["id"] for r in chunked["only_in_file1"])
        non_only1 = sorted(str(v) for v in non_chunked["only_in_file1"]["id"].tolist())
        assert chunked_only1 == non_only1

        chunked_only2 = sorted(r["keys"]["id"] for r in chunked["only_in_file2"])
        non_only2 = sorted(str(v) for v in non_chunked["only_in_file2"]["id"].tolist())
        assert chunked_only2 == non_only2

        # differing keys parity
        chunked_diff_keys = sorted(d["keys"]["id"] for d in chunked["differences"])
        non_diff_keys = sorted(str(d["keys"]["id"]) for d in non_chunked["differences"])
        assert chunked_diff_keys == non_diff_keys


class TestLargerInput:
    """A few-thousand-row input completes correctly and reasonably fast."""

    def test_three_thousand_rows(self):
        header = "id|value\n"
        n = 3000
        rows1 = [f"{i}|A{i}" for i in range(1, n + 1)]
        rows2 = [f"{i}|A{i}" for i in range(1, n + 1)]
        # 100 rows differ, 50 only-in-1, 50 only-in-2
        for i in range(0, 100):
            rows2[i] = f"{i + 1}|CHANGED{i + 1}"
        rows1 = rows1 + [f"{n + i}|X" for i in range(1, 51)]      # only in file1
        rows2 = rows2 + [f"{n + 100 + i}|Y" for i in range(1, 51)]  # only in file2
        f1 = _write(header + "\n".join(rows1))
        f2 = _write(header + "\n".join(rows2))
        try:
            results = ChunkedFileComparator(
                f1, f2, key_columns=["id"], chunk_size=500
            ).compare(detailed=False, show_progress=False)
            assert results["rows_with_differences"] == 100
            assert results["only_in_file1_count"] == 50
            assert results["only_in_file2_count"] == 50
        finally:
            for p in (f1, f2):
                if os.path.exists(p):
                    os.unlink(p)
