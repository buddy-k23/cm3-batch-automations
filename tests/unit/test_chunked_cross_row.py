"""Tests for cross-row rule validation across chunk boundaries.

Issue #357: These tests confirm the gap (failing before the fix) and validate
the map-reduce implementation after the fix is applied (issue #358).

All tests use the public ChunkedFileValidator API — no internal imports.
"""

import csv
import json
from pathlib import Path

import pytest

from src.parsers.chunked_validator import ChunkedFileValidator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _csv(tmp_path: Path, rows: list, filename: str = "data.csv") -> str:
    """Write rows (list of dicts) to a CSV and return its path.

    Args:
        tmp_path: pytest tmp_path fixture directory.
        rows: List of dicts — all dicts must share the same keys.
        filename: Output filename. Defaults to ``data.csv``.

    Returns:
        Absolute path to the written CSV file as a string.
    """
    p = tmp_path / filename
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return str(p)


def _rules(tmp_path: Path, rules_list: list, filename: str = "rules.json") -> str:
    """Write a rules JSON config and return its path.

    Args:
        tmp_path: pytest tmp_path fixture directory.
        rules_list: List of rule dicts to include under the ``rules`` key.
        filename: Output filename. Defaults to ``rules.json``.

    Returns:
        Absolute path to the written JSON file as a string.
    """
    p = tmp_path / filename
    p.write_text(json.dumps({"rules": rules_list}))
    return str(p)


def _run(file_path: str, rules_path: str, chunk_size: int = 2) -> dict:
    """Run ChunkedFileValidator with the given file and rules.

    Args:
        file_path: Path to the CSV data file.
        rules_path: Path to the rules JSON config.
        chunk_size: Rows per chunk. Defaults to 2.

    Returns:
        Validation result dict from ChunkedFileValidator.validate().
    """
    return ChunkedFileValidator(
        file_path=file_path,
        delimiter=",",
        chunk_size=chunk_size,
        rules_config_path=rules_path,
    ).validate(show_progress=False)


# ---------------------------------------------------------------------------
# Test 1: unique — duplicate key across chunk boundary
# ---------------------------------------------------------------------------


def test_unique_duplicate_across_chunk_boundary(tmp_path):
    """Duplicate key value split across chunks must be detected.

    With chunk_size=2, the six rows form three chunks:
    - chunk 1: A, B
    - chunk 2: C, D   <- first occurrence of C
    - chunk 3: C, E   <- duplicate C
    Before the fix the validator only sees each chunk independently, so the
    duplicate is missed. After the fix the map-reduce aggregation detects it.
    """
    rows = [
        {"id": "A", "val": "1"},
        {"id": "B", "val": "2"},
        {"id": "C", "val": "3"},  # first occurrence of C — chunk 2
        {"id": "D", "val": "4"},
        {"id": "C", "val": "5"},  # duplicate C — chunk 3
        {"id": "E", "val": "6"},
    ]
    rules = [
        {
            "id": "R1",
            "name": "unique id",
            "type": "cross_row",
            "check": "unique",
            "field": "id",
            "severity": "error",
            "enabled": True,
        }
    ]
    result = _run(_csv(tmp_path, rows), _rules(tmp_path, rules), chunk_size=2)

    assert result["business_rules"]["enabled"]
    violations = result["business_rules"]["violations"]
    assert len(violations) >= 1, "Expected duplicate C to be detected across chunks"
    assert any("C" in str(v.get("value", "")) for v in violations)


# ---------------------------------------------------------------------------
# Test 2: group_sum — group spanning multiple chunks
# ---------------------------------------------------------------------------


def test_group_sum_across_chunk_boundary(tmp_path):
    """group_sum must aggregate values from all chunks, not just per-chunk.

    Four rows all in group X with amounts 100, 200, 300, 400 (total 1000).
    max_value=999 so the total must trigger a violation. With chunk_size=2
    each chunk sums to 300 and 700 respectively — only the global sum
    exceeds the threshold.
    """
    rows = [
        {"grp": "X", "amt": "100"},
        {"grp": "X", "amt": "200"},
        {"grp": "X", "amt": "300"},
        {"grp": "X", "amt": "400"},
    ]
    rules = [
        {
            "id": "R2",
            "name": "sum check",
            "type": "cross_row",
            "check": "group_sum",
            "key_field": "grp",
            "sum_field": "amt",
            "max_value": 999,
            "severity": "error",
            "enabled": True,
        }
    ]
    result = _run(_csv(tmp_path, rows), _rules(tmp_path, rules), chunk_size=2)
    violations = result["business_rules"]["violations"]
    assert len(violations) >= 1, "Expected group_sum violation (total 1000 > max 999)"


# ---------------------------------------------------------------------------
# Test 3: sequential — gap at chunk boundary
# ---------------------------------------------------------------------------


def test_sequential_gap_across_chunk_boundary(tmp_path):
    """A sequence that restarts across chunks must be detected as non-sequential.

    Group G has four rows split into two chunks:
    - chunk 1: seq [1, 2]  — looks sequential per-chunk (expected {1,2} matches)
    - chunk 2: seq [1, 2]  — looks sequential per-chunk (expected {1,2} matches)
    Globally G has 4 rows with values {1,2,1,2} = {1,2}; the expected global
    set is {1,2,3,4}, so the check must fail after aggregation.
    Before the fix, each chunk independently appears valid; the bug is invisible.
    """
    rows = [
        {"grp": "G", "seq": "1"},
        {"grp": "G", "seq": "2"},  # chunk 1: G={1,2} vs expected {1,2} -> PASS per-chunk
        {"grp": "G", "seq": "1"},  # duplicate seq=1 in chunk 2
        {"grp": "G", "seq": "2"},  # chunk 2: G={1,2} vs expected {1,2} -> PASS per-chunk
        # Globally: G has 4 rows but unique seq values {1,2} != expected {1,2,3,4}
    ]
    rules = [
        {
            "id": "R3",
            "name": "seq check",
            "type": "cross_row",
            "check": "sequential",
            "key_field": "grp",
            "sequence_field": "seq",
            "severity": "error",
            "enabled": True,
        }
    ]
    result = _run(_csv(tmp_path, rows), _rules(tmp_path, rules), chunk_size=2)
    violations = result["business_rules"]["violations"]
    assert len(violations) >= 1, "Expected sequential gap to be detected across chunks"


# ---------------------------------------------------------------------------
# Test 4: consistent — inconsistent value across chunks
# ---------------------------------------------------------------------------


def test_consistent_mismatch_across_chunk_boundary(tmp_path):
    """Inconsistent target_field value in a group spanning chunks must be flagged.

    Each account key appears exactly once per chunk, so no inconsistency is
    detectable within a single chunk. Only cross-chunk aggregation reveals that
    A1 has both EAST (chunk 1) and WEST (chunk 2), and A2 has both WEST (chunk 1)
    and EAST (chunk 2).
    Before the fix each chunk looks clean; the violation is invisible.
    """
    rows = [
        {"acct": "A1", "region": "EAST"},
        {"acct": "A2", "region": "WEST"},  # chunk 1: each key appears once -> PASS per-chunk
        {"acct": "A1", "region": "WEST"},  # A1 now inconsistent across chunks
        {"acct": "A2", "region": "EAST"},  # A2 now inconsistent across chunks
    ]
    rules = [
        {
            "id": "R4",
            "name": "consistent region",
            "type": "cross_row",
            "check": "consistent",
            "key_field": "acct",
            "target_field": "region",
            "severity": "error",
            "enabled": True,
        }
    ]
    result = _run(_csv(tmp_path, rows), _rules(tmp_path, rules), chunk_size=2)
    violations = result["business_rules"]["violations"]
    assert len(violations) >= 1, "Expected inconsistent region to be detected across chunks"


# ---------------------------------------------------------------------------
# Test 5: field-level rules still work (regression guard)
# ---------------------------------------------------------------------------


def test_field_rules_still_work_in_chunked_mode(tmp_path):
    """Field-level rules must continue to produce violations in chunked mode.

    Row 3 has val='bad' which is not in the allowed list ['good']. This must
    be reported even with the new map-reduce cross-row path in place.
    """
    rows = [{"id": str(i), "val": "bad" if i == 3 else "good"} for i in range(1, 7)]
    rules = [
        {
            "id": "R5",
            "name": "val check",
            "type": "field_validation",
            "field": "val",
            "operator": "in",
            "values": ["good"],
            "severity": "error",
            "enabled": True,
        }
    ]
    result = _run(_csv(tmp_path, rows), _rules(tmp_path, rules), chunk_size=2)
    assert len(result["business_rules"]["violations"]) >= 1


# ---------------------------------------------------------------------------
# Test 6: non-chunked mode unchanged
# ---------------------------------------------------------------------------


def test_non_chunked_mode_unchanged(tmp_path):
    """Non-chunked validation must still detect cross-row violations.

    When chunk_size is large enough to hold all rows in a single chunk the
    existing single-pass logic handles deduplication. This test ensures the
    refactor does not regress that path.
    """
    rows = [{"id": "A"}, {"id": "A"}]  # duplicate in same chunk
    rules = [
        {
            "id": "R6",
            "name": "unique",
            "type": "cross_row",
            "check": "unique",
            "field": "id",
            "severity": "error",
            "enabled": True,
        }
    ]
    # large chunk_size = all rows in one chunk → already works
    result = _run(_csv(tmp_path, rows), _rules(tmp_path, rules), chunk_size=1000)
    assert len(result["business_rules"]["violations"]) >= 1
