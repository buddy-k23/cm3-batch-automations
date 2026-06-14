"""Tests for _check_structure_compatibility and run_compare_service structure phase."""
import pandas as pd
import pytest

from src.services.compare_service import _check_structure_compatibility, run_compare_service


def _df(cols):
    """Minimal DataFrame with given column names and no rows."""
    return pd.DataFrame(columns=cols)


def test_compatible_identical_columns():
    """Identical columns — no errors."""
    assert _check_structure_compatibility(_df(["a", "b", "c"]), _df(["a", "b", "c"])) == []


def test_column_count_mismatch():
    """Different column counts return a single column_count_mismatch error."""
    errors = _check_structure_compatibility(_df(["a", "b", "c"]), _df(["a", "b"]))
    assert len(errors) == 1
    assert errors[0]["type"] == "column_count_mismatch"
    assert errors[0]["file1_count"] == 3
    assert errors[0]["file2_count"] == 2


def test_count_mismatch_returns_early_no_name_errors():
    """Column count mismatch exits early — no spurious missing_columns errors."""
    errors = _check_structure_compatibility(_df(["a", "b", "c"]), _df(["x", "y"]))
    assert all(e["type"] == "column_count_mismatch" for e in errors)


def test_missing_columns_in_file2():
    """Same count but file2 is missing a column — missing_columns error for file2."""
    errors = _check_structure_compatibility(_df(["a", "b", "c"]), _df(["a", "b", "x"]))
    types = {e["type"] for e in errors}
    assert "missing_columns" in types
    assert len(errors) == 2  # both directions reported
    err2 = next(e for e in errors if e.get("in_file") == "file2")
    assert "c" in err2["columns"]
    err1 = next(e for e in errors if e.get("in_file") == "file1")
    assert "x" in err1["columns"]


def test_missing_columns_in_file1():
    """Same count but file1 is missing a column — missing_columns error for file1."""
    errors = _check_structure_compatibility(_df(["a", "b", "x"]), _df(["a", "b", "c"]))
    types = {e["type"] for e in errors}
    assert "missing_columns" in types
    assert len(errors) == 2
    err1 = next(e for e in errors if e.get("in_file") == "file1")
    assert "c" in err1["columns"]
    err2 = next(e for e in errors if e.get("in_file") == "file2")
    assert "x" in err2["columns"]


def test_column_order_mismatch_with_mapping():
    """Same names but wrong order vs mapping — column_order_mismatch error with payload."""
    mapping = {"fields": [{"name": "a"}, {"name": "b"}, {"name": "c"}]}
    errors = _check_structure_compatibility(_df(["b", "a", "c"]), _df(["b", "a", "c"]), mapping)
    assert len(errors) == 1
    err = errors[0]
    assert err["type"] == "column_order_mismatch"
    assert err["expected_columns"] == ["a", "b", "c"]
    assert err["file1_columns"] == ["b", "a", "c"]
    assert err["file2_columns"] == ["b", "a", "c"]


def test_no_order_check_without_mapping():
    """Without a mapping, different column order is not flagged."""
    assert _check_structure_compatibility(_df(["a", "b", "c"]), _df(["b", "a", "c"])) == []


def test_correct_order_with_mapping_no_errors():
    """Columns in the order the mapping expects — no errors."""
    mapping = {"fields": [{"name": "a"}, {"name": "b"}, {"name": "c"}]}
    assert _check_structure_compatibility(_df(["a", "b", "c"]), _df(["a", "b", "c"]), mapping) == []


def test_run_compare_service_structure_error_on_count_mismatch(tmp_path):
    """run_compare_service returns structure_compatible=False when column counts differ."""
    f1 = tmp_path / "f1.txt"
    f2 = tmp_path / "f2.txt"
    f1.write_text("a|b|c\n1|2|3\n", encoding="utf-8")
    f2.write_text("a|b\n1|2\n", encoding="utf-8")
    result = run_compare_service(str(f1), str(f2))
    assert result["structure_compatible"] is False
    assert result["differences"] == 0
    assert any(e["type"] == "column_count_mismatch" for e in result["structure_errors"])


def test_run_compare_service_sets_structure_compatible_true(tmp_path):
    """run_compare_service sets structure_compatible=True when files are compatible."""
    f1 = tmp_path / "f1.txt"
    f2 = tmp_path / "f2.txt"
    f1.write_text("a|b|c\n1|2|3\n", encoding="utf-8")
    f2.write_text("a|b|c\n1|2|3\n", encoding="utf-8")
    result = run_compare_service(str(f1), str(f2))
    assert result.get("structure_compatible") is True
