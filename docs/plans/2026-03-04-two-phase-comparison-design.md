# Two-Phase Comparison Design

**Date:** 2026-03-04
**Issue:** #46

## Goal

Run a structure compatibility check before data comparison. If the two files have mismatched column counts, column names, or column order, return early with a clear error — no data diff is performed.

## Architecture

Add `_check_structure_compatibility(df1, df2, mapping_config)` in `src/services/compare_service.py`. It runs at the top of `run_compare_service`, after both files are parsed into DataFrames but before any comparator is instantiated. On mismatch it returns an early result dict. On success it adds `structure_compatible: True` to the normal result.

No changes to comparators, router, or HTML reporter.

## Structure Mismatch Conditions (hard stop)

1. Column count differs between file1 and file2
2. Column names differ (mapping fields are the reference when provided; file1 columns otherwise)
3. Column order differs (when mapping defines field order)

Type mismatches are out of scope — that is validation territory.

## Result Contract

**On mismatch:**
```python
{
    "structure_compatible": False,
    "structure_errors": [
        {"type": "column_count_mismatch", "file1_count": 10, "file2_count": 9},
        {"type": "missing_columns", "columns": ["age"], "in_file": "file2"},
    ],
    "total_rows_file1": 0,
    "total_rows_file2": 0,
    "matching_rows": 0,
    "only_in_file1": 0,
    "only_in_file2": 0,
    "differences": 0,
    "valid": False,
}
```

**On success:** `structure_compatible: True` added to normal result dict (no breaking change).

## Files Changed

| File | Change |
|------|--------|
| `src/services/compare_service.py` | Add `_check_structure_compatibility`, call at top of `run_compare_service` |
| `src/api/models/file.py` | Add `structure_compatible: Optional[bool]` and `structure_errors: Optional[list]` to `FileCompareResult` |
| `tests/unit/test_compare_service_structure.py` | New test file |
