# Two-Phase Comparison Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Run a structure compatibility check (column count, names, order) before data comparison; return early with a clear error when files are structurally incompatible.

**Architecture:** Add `_check_structure_compatibility(df1, df2, mapping_config)` to `src/services/compare_service.py`. It is called after both files are parsed into DataFrames (lines 78-79) but before any comparator is instantiated. On mismatch it returns an early result dict with `structure_compatible: False`. On success it adds `structure_compatible: True` to the normal comparator result. `FileCompareResult` gets two new optional fields: `structure_compatible` and `structure_errors`.

**Tech Stack:** Python, pandas DataFrames (already used by `FileComparator`), Pydantic (`FileCompareResult`).

---

## Background

`run_compare_service` (lines 28–102 of `src/services/compare_service.py`) works in two paths:

- **Chunked path** (lines 41–54): exits early via `ChunkedFileComparator` — structure check is **not** applied here (files are streamed, not loaded into DataFrames).
- **Standard path** (lines 56–102): loads `df1` and `df2` via parsers (lines 78–79), then hands them to `FileComparator`. **The structure check goes here, between lines 79 and 81.**

`FileCompareResult` lives in `src/api/models/file.py` lines 39–48. It currently has no structure-related fields.

---

## Task 1 — `_check_structure_compatibility` + model update + wire-up

**Files:**
- Modify: `src/services/compare_service.py`
- Modify: `src/api/models/file.py`
- Create: `tests/unit/test_compare_service_structure.py`

---

### Step 1: Write the failing unit tests

Create `tests/unit/test_compare_service_structure.py`:

```python
"""Tests for _check_structure_compatibility and run_compare_service structure phase."""
import pandas as pd
import pytest

from src.services.compare_service import _check_structure_compatibility, run_compare_service


# ── helpers ──────────────────────────────────────────────────────────────────

def _df(cols):
    """Minimal DataFrame with given column names and no rows."""
    return pd.DataFrame(columns=cols)


# ── _check_structure_compatibility unit tests ─────────────────────────────────

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
    err = next(e for e in errors if e.get("in_file") == "file2")
    assert "c" in err["columns"]


def test_missing_columns_in_file1():
    """Same count but file1 is missing a column — missing_columns error for file1."""
    errors = _check_structure_compatibility(_df(["a", "b", "x"]), _df(["a", "b", "c"]))
    types = {e["type"] for e in errors}
    assert "missing_columns" in types
    err = next(e for e in errors if e.get("in_file") == "file1")
    assert "c" in err["columns"]


def test_column_order_mismatch_with_mapping():
    """Same names but wrong order vs mapping — column_order_mismatch error."""
    mapping = {"fields": [{"name": "a"}, {"name": "b"}, {"name": "c"}]}
    errors = _check_structure_compatibility(_df(["b", "a", "c"]), _df(["b", "a", "c"]), mapping)
    assert any(e["type"] == "column_order_mismatch" for e in errors)


def test_no_order_check_without_mapping():
    """Without a mapping, different column order is not flagged."""
    assert _check_structure_compatibility(_df(["a", "b", "c"]), _df(["b", "a", "c"])) == []


def test_correct_order_with_mapping_no_errors():
    """Columns in the order the mapping expects — no errors."""
    mapping = {"fields": [{"name": "a"}, {"name": "b"}, {"name": "c"}]}
    assert _check_structure_compatibility(_df(["a", "b", "c"]), _df(["a", "b", "c"]), mapping) == []


# ── run_compare_service integration tests ────────────────────────────────────

def test_run_compare_service_structure_error_on_count_mismatch(tmp_path):
    """run_compare_service returns structure_compatible=False when column counts differ."""
    f1 = tmp_path / "f1.txt"
    f2 = tmp_path / "f2.txt"
    f1.write_text("a|b|c\n1|2|3\n", encoding="utf-8")
    f2.write_text("a|b\n1|2\n", encoding="utf-8")
    result = run_compare_service(str(f1), str(f2))
    assert result["structure_compatible"] is False
    assert result["valid"] is False
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
```

### Step 2: Run to confirm they fail

```bash
cd /Users/buddy/claude-code/automations/valdo
python3 -m pytest tests/unit/test_compare_service_structure.py -v 2>&1 | tail -15
```

Expected: `FAILED` — `cannot import name '_check_structure_compatibility'`

---

### Step 3: Implement `_check_structure_compatibility` in `compare_service.py`

In `src/services/compare_service.py`, add this function after `_build_fixed_width_specs` (after line 25) and before `run_compare_service`:

```python
def _check_structure_compatibility(
    df1,
    df2,
    mapping_config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Check that two DataFrames have compatible column structure for comparison.

    Checks column count, column names, and (when mapping_config provides
    ordered fields) column order. Returns a list of error dicts describing
    each mismatch found. An empty list means the files are compatible.

    Short-circuits after a column_count_mismatch — name/order checks are
    meaningless if counts differ.

    Args:
        df1: First parsed DataFrame.
        df2: Second parsed DataFrame.
        mapping_config: Optional parsed mapping dict. When provided and it
            contains a ``fields`` list, field names define the expected
            column order.

    Returns:
        List of error dicts. Each dict has at least a ``type`` key.
        Possible types: ``column_count_mismatch``, ``missing_columns``,
        ``column_order_mismatch``.
    """
    errors: list[dict[str, Any]] = []
    cols1 = list(df1.columns)
    cols2 = list(df2.columns)

    if len(cols1) != len(cols2):
        errors.append({
            "type": "column_count_mismatch",
            "file1_count": len(cols1),
            "file2_count": len(cols2),
        })
        return errors  # counts differ — name/order checks are meaningless

    missing_in_file2 = sorted(set(cols1) - set(cols2))
    missing_in_file1 = sorted(set(cols2) - set(cols1))

    if missing_in_file2:
        errors.append({"type": "missing_columns", "columns": missing_in_file2, "in_file": "file2"})
    if missing_in_file1:
        errors.append({"type": "missing_columns", "columns": missing_in_file1, "in_file": "file1"})

    if errors:
        return errors  # name mismatches — order check is meaningless

    if mapping_config and mapping_config.get("fields"):
        expected = [f["name"] for f in mapping_config["fields"] if f.get("name")]
        if expected and (cols1 != expected or cols2 != expected):
            errors.append({
                "type": "column_order_mismatch",
                "expected_columns": expected,
                "file1_columns": cols1,
                "file2_columns": cols2,
            })

    return errors
```

### Step 4: Wire the check into `run_compare_service`

In `src/services/compare_service.py`, find the standard path after `df1` and `df2` are parsed (currently lines 78–79):

```python
    df1 = parser1.parse()
    df2 = parser2.parse()
```

Add the structure check immediately after:

```python
    df1 = parser1.parse()
    df2 = parser2.parse()

    # Phase 1: structure compatibility check
    structure_errors = _check_structure_compatibility(df1, df2, mapping_config)
    if structure_errors:
        return {
            "structure_compatible": False,
            "structure_errors": structure_errors,
            "total_rows_file1": len(df1),
            "total_rows_file2": len(df2),
            "matching_rows": 0,
            "only_in_file1": 0,
            "only_in_file2": 0,
            "differences": 0,
            "valid": False,
        }
```

Then, at the end of `run_compare_service` (currently `return comparator.compare(detailed=detailed)`), change to:

```python
    result = comparator.compare(detailed=detailed)
    result["structure_compatible"] = True
    return result
```

### Step 5: Add `structure_compatible` and `structure_errors` to `FileCompareResult`

In `src/api/models/file.py`, find `FileCompareResult` (lines 39–48) and add two optional fields:

```python
class FileCompareResult(BaseModel):
    """Model for file comparison result."""
    total_rows_file1: int
    total_rows_file2: int
    matching_rows: int
    only_in_file1: int
    only_in_file2: int
    differences: int
    report_url: Optional[str] = None
    field_statistics: Optional[Dict[str, Any]] = None
    structure_compatible: Optional[bool] = None
    structure_errors: Optional[List[Dict[str, Any]]] = None
```

### Step 6: Run tests to confirm they pass

```bash
python3 -m pytest tests/unit/test_compare_service_structure.py -v
```

Expected: `10 passed`

---

### Step 7: Run full test suites

```bash
python3 -m pytest tests/unit/ \
  --ignore=tests/unit/test_contracts_pipeline.py \
  --ignore=tests/unit/test_pipeline_runner.py \
  --ignore=tests/unit/test_workflow_wrapper_parity.py -q 2>&1 | tail -5
```

Expected: 409+ passed, 0 failed, ≥80% coverage.

```bash
python3 -m pytest tests/integration/ -q -o addopts='' 2>&1 | tail -5
```

Expected: 28 passed, 0 failed.

---

### Step 8: Commit

```bash
git add src/services/compare_service.py src/api/models/file.py \
        tests/unit/test_compare_service_structure.py
git commit -m "feat(compare): add structure compatibility check before data diff

Adds _check_structure_compatibility() to compare_service. Checks column
count, column names, and (when a mapping is provided) column order.
On mismatch, run_compare_service returns early with structure_compatible=False
and a structure_errors list instead of running the data diff.
FileCompareResult gains structure_compatible and structure_errors fields.
Closes #46"
```
