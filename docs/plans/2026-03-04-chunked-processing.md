# Chunked Processing Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable memory-bounded processing for large files by routing validation and comparison through existing chunked infrastructure when files exceed a 50 MB threshold.

**Architecture:** Two-task implementation. Task 1 adds `use_chunked` / `chunk_size` parameters to `run_validate_service`, routing to the existing `ChunkedFileValidator` for delimited files. Task 2 adds a `_should_use_chunked(path)` helper to `files.py` and calls it in both the `/validate` endpoint and `_run_compare_with_mapping` — no new API surface exposed to callers. Fixed-width files always use the standard path (ChunkedFileValidator is delimiter-based).

**Tech Stack:** Python, existing `ChunkedFileValidator` (`src/parsers/chunked_validator.py`), existing `run_compare_service(use_chunked=True)` (`src/services/compare_service.py`).

---

## Background — existing infrastructure

**`ChunkedFileValidator`** (`src/parsers/chunked_validator.py`):
- Validates delimiter-separated files in chunks via `ChunkedFileParser` (pandas `read_csv` with `chunksize`)
- Constructor: `ChunkedFileValidator(file_path, delimiter="|", chunk_size=100_000, rules_config_path=None, expected_row_length=None, strict_fixed_width=False, strict_level="format", strict_fields=None, workers=1)`
- Returns: `{"valid": bool, "errors": list, "warnings": list, "total_rows": int, "statistics": {...}, "business_rules": {...}}`
- The normalisation block already in `run_validate_service` (lines 62–81) maps this output to the expected contract.

**`run_compare_service`** (`src/services/compare_service.py`):
- Already accepts `use_chunked: bool = False`
- When `True`, uses `ChunkedFileComparator` (SQLite-indexed, streams files) — **requires key_columns**
- `_run_compare_with_mapping` hardcodes `use_chunked=False` at line 188.

**Threshold:** 50 MB = `50 * 1024 * 1024` bytes. Constant named `_CHUNK_THRESHOLD_BYTES`.

---

## Task 1 — Add chunked path to `run_validate_service`

**Files:**
- Modify: `src/services/validate_service.py`
- Create: `tests/unit/test_validate_service_chunked.py`

---

### Step 1: Write the failing tests

Create `tests/unit/test_validate_service_chunked.py`:

```python
"""Tests for run_validate_service chunked path."""
import json
import tempfile
from pathlib import Path

import pytest

from src.services.validate_service import run_validate_service


PIPE_CONTENT = "Alice|30\nBob|25\nCarol|35\n"

MAPPING_PIPE = {
    "mapping_name": "test_chunked_pipe",
    "version": "1.0.0",
    "source": {"type": "file", "format": "pipe_delimited", "has_header": False},
    "fields": [
        {"name": "name", "data_type": "string"},
        {"name": "age",  "data_type": "integer"},
    ],
    "key_columns": ["name"],
}


def _write_files(tmp_path: Path):
    data_file = tmp_path / "sample.txt"
    data_file.write_text(PIPE_CONTENT, encoding="utf-8")
    mapping_file = tmp_path / "mapping.json"
    mapping_file.write_text(json.dumps(MAPPING_PIPE), encoding="utf-8")
    return str(data_file), str(mapping_file)


def test_chunked_validate_returns_required_contract_keys(tmp_path):
    """run_validate_service with use_chunked=True returns same contract as non-chunked."""
    data_file, mapping_file = _write_files(tmp_path)
    result = run_validate_service(file=data_file, mapping=mapping_file, use_chunked=True)
    for key in ("valid", "total_rows", "error_count", "warning_count"):
        assert key in result, f"Missing key: {key}"
    assert result["total_rows"] == 3


def test_chunked_validate_small_chunk_size(tmp_path):
    """use_chunked=True with chunk_size=1 still processes all rows."""
    data_file, mapping_file = _write_files(tmp_path)
    result = run_validate_service(
        file=data_file, mapping=mapping_file, use_chunked=True, chunk_size=1
    )
    assert result["total_rows"] == 3


def test_non_chunked_and_chunked_agree_on_validity(tmp_path):
    """Chunked and non-chunked paths return the same validity for clean data."""
    data_file, mapping_file = _write_files(tmp_path)
    r_standard = run_validate_service(file=data_file, mapping=mapping_file)
    r_chunked  = run_validate_service(file=data_file, mapping=mapping_file, use_chunked=True)
    assert r_standard["valid"] == r_chunked["valid"]


def test_fixed_width_mapping_ignores_use_chunked(tmp_path):
    """Fixed-width mappings fall back to standard path even when use_chunked=True."""
    fw_content = "Alice 030\nBob   025\n"
    fw_mapping = {
        "mapping_name": "fw_test",
        "version": "1.0.0",
        "source": {"type": "file", "format": "fixed_width"},
        "fields": [
            {"name": "name", "position": 1, "length": 6, "data_type": "string"},
            {"name": "age",  "position": 7, "length": 3, "data_type": "integer"},
        ],
    }
    data_file = tmp_path / "fw.txt"
    data_file.write_text(fw_content, encoding="utf-8")
    mapping_file = tmp_path / "fw_mapping.json"
    mapping_file.write_text(json.dumps(fw_mapping), encoding="utf-8")

    # Should not raise — falls back to standard path silently
    result = run_validate_service(file=str(data_file), mapping=str(mapping_file),
                                  use_chunked=True)
    assert "total_rows" in result
```

### Step 2: Run to confirm they fail

```bash
cd /Users/buddy/claude-code/automations/valdo
python3 -m pytest tests/unit/test_validate_service_chunked.py -v 2>&1 | tail -15
```

Expected: `FAILED` — `run_validate_service() got an unexpected keyword argument 'use_chunked'`

---

### Step 3: Implement the chunked path in `run_validate_service`

In `src/services/validate_service.py`, replace the entire function signature and add the chunked branch. The full new implementation:

```python
def run_validate_service(
    file: str,
    mapping: Optional[str] = None,
    rules: Optional[str] = None,
    output: Optional[str] = None,
    detailed: bool = True,
    strict_fixed_width: bool = False,
    strict_level: str = "format",
    use_chunked: bool = False,
    chunk_size: int = 100_000,
) -> dict[str, Any]:
    """Shared validate workflow used by CLI and run-tests orchestrator.

    Returns a dict with at least:
      total_rows    - number of rows processed
      error_count   - number of validation errors
      warning_count - number of validation warnings
      valid         - bool overall validity flag

    Args:
        file: Path to the data file.
        mapping: Path to mapping JSON file.
        rules: Path to rules config file.
        output: Optional output path (.json or .html).
        detailed: Include detailed field analysis.
        strict_fixed_width: Enable strict fixed-width validation.
        strict_level: Validation strictness level ('format' or 'all').
        use_chunked: Route to ChunkedFileValidator for memory-efficient
            processing. Ignored for fixed-width mappings (falls back to
            standard path).
        chunk_size: Rows per chunk when use_chunked=True. Default 100,000.
    """
    from src.parsers.format_detector import FormatDetector
    from src.parsers.enhanced_validator import EnhancedFileValidator
    from src.parsers.fixed_width_parser import FixedWidthParser

    mapping_config: Optional[dict] = None
    if mapping:
        with open(mapping, "r", encoding="utf-8") as f:
            mapping_config = json.load(f)
        mapping_config["file_path"] = mapping

    is_fixed_width = mapping_config and _is_fixed_width_mapping(mapping_config)

    # ── Chunked path (delimited files only) ───────────────────────────────────
    if use_chunked and not is_fixed_width:
        result = _run_chunked_validate(
            file=file,
            mapping_config=mapping_config,
            rules=rules,
            chunk_size=chunk_size,
            strict_fixed_width=strict_fixed_width,
            strict_level=strict_level,
        )
    else:
        # ── Standard path ─────────────────────────────────────────────────────
        if is_fixed_width:
            parser_class = FixedWidthParser
        else:
            detector = FormatDetector()
            try:
                parser_class = detector.get_parser_class(file)
            except Exception:
                if mapping_config and mapping_config.get("fields"):
                    parser_class = FixedWidthParser
                else:
                    raise

        if mapping_config and parser_class == FixedWidthParser:
            field_specs = _build_fixed_width_specs(mapping_config)
            parser = FixedWidthParser(file, field_specs)
        else:
            parser = parser_class(file)

        validator = EnhancedFileValidator(parser, mapping_config, rules)
        result = validator.validate(
            detailed=detailed,
            strict_fixed_width=strict_fixed_width,
            strict_level=strict_level,
        )

    # ── Normalise counts so callers always get integers ───────────────────────
    result.setdefault("error_count", len(result.get("errors", [])))
    result.setdefault("warning_count", len(result.get("warnings", [])))
    result.setdefault("total_rows", result.get("row_count", 0))

    if not result.get("total_rows"):
        try:
            with open(file, encoding="utf-8", errors="replace") as fh:
                result["total_rows"] = sum(1 for line in fh if line.strip())
        except Exception:
            pass

    if not result.get("valid_rows"):
        affected = {
            e["row"] for e in result.get("errors", [])
            if isinstance(e.get("row"), int)
        }
        result["valid_rows"] = max(0, result.get("total_rows", 0) - len(affected))

    if output:
        from pathlib import Path

        Path(output).parent.mkdir(parents=True, exist_ok=True)
        if output.lower().endswith(".json"):
            with open(output, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
        elif output.lower().endswith((".html", ".htm")):
            from src.reports.renderers.validation_renderer import ValidationReporter

            reporter = ValidationReporter()
            reporter.generate(result, output)

    return result
```

Add the new `_run_chunked_validate` and `_detect_delimiter` helpers after `_build_fixed_width_specs`:

```python
def _detect_delimiter(mapping_config: Optional[dict]) -> str:
    """Return the field delimiter for a delimited mapping config.

    Args:
        mapping_config: Parsed mapping JSON dict, or None.

    Returns:
        Single-character delimiter string. Defaults to '|'.
    """
    if not mapping_config:
        return "|"
    fmt = mapping_config.get("source", {}).get("format", "").lower()
    if "comma" in fmt or "csv" in fmt:
        return ","
    if "tab" in fmt or "tsv" in fmt:
        return "\t"
    return "|"


def _run_chunked_validate(
    file: str,
    mapping_config: Optional[dict],
    rules: Optional[str],
    chunk_size: int,
    strict_fixed_width: bool,
    strict_level: str,
) -> dict[str, Any]:
    """Run validation via ChunkedFileValidator.

    Args:
        file: Path to the data file.
        mapping_config: Parsed mapping dict (or None).
        rules: Path to rules config file (or None).
        chunk_size: Rows per chunk.
        strict_fixed_width: Enable strict fixed-width checks.
        strict_level: Strictness level ('format' or 'all').

    Returns:
        Raw result dict from ChunkedFileValidator (normalised by caller).
    """
    from src.parsers.chunked_validator import ChunkedFileValidator

    delimiter = _detect_delimiter(mapping_config)
    strict_fields = mapping_config.get("fields", []) if mapping_config else []

    validator = ChunkedFileValidator(
        file_path=file,
        delimiter=delimiter,
        chunk_size=chunk_size,
        rules_config_path=rules,
        strict_fixed_width=strict_fixed_width,
        strict_level=strict_level,
        strict_fields=strict_fields,
    )
    return validator.validate(show_progress=False)
```

---

### Step 4: Run tests to confirm they pass

```bash
python3 -m pytest tests/unit/test_validate_service_chunked.py -v 2>&1 | tail -15
```

Expected: `4 passed`

---

### Step 5: Run full unit suite to confirm no regressions

```bash
python3 -m pytest tests/unit/ \
  --ignore=tests/unit/test_contracts_pipeline.py \
  --ignore=tests/unit/test_pipeline_runner.py \
  --ignore=tests/unit/test_workflow_wrapper_parity.py -q 2>&1 | tail -5
```

Expected: 393+ passed, 0 failed, ≥80% coverage.

---

### Step 6: Commit

```bash
git add src/services/validate_service.py tests/unit/test_validate_service_chunked.py
git commit -m "feat(validation): add chunked path to run_validate_service via ChunkedFileValidator

Adds use_chunked and chunk_size parameters. When use_chunked=True and the
mapping is not fixed-width, routes to ChunkedFileValidator for memory-
bounded processing. Fixed-width mappings silently fall back to the
standard EnhancedFileValidator path. Output contract is unchanged."
```

---

## Task 2 — Auto-threshold chunking in API routers

**Files:**
- Modify: `src/api/routers/files.py`
- Create: `tests/unit/test_api_chunked_threshold.py`

---

### Step 1: Write the failing tests

Create `tests/unit/test_api_chunked_threshold.py`:

```python
"""Tests for the _should_use_chunked threshold helper in files router."""
import tempfile
from pathlib import Path

import pytest

from src.api.routers.files import _should_use_chunked, _CHUNK_THRESHOLD_BYTES


def test_small_file_returns_false(tmp_path):
    """Files below threshold should not trigger chunked processing."""
    f = tmp_path / "small.txt"
    f.write_bytes(b"x" * 100)
    assert _should_use_chunked(f) is False


def test_large_file_returns_true(tmp_path):
    """Files at or above threshold should trigger chunked processing."""
    f = tmp_path / "large.txt"
    f.write_bytes(b"x" * _CHUNK_THRESHOLD_BYTES)
    assert _should_use_chunked(f) is True


def test_threshold_constant_is_50mb():
    """Threshold should be exactly 50 MB."""
    assert _CHUNK_THRESHOLD_BYTES == 50 * 1024 * 1024


def test_missing_file_returns_false(tmp_path):
    """Non-existent files should return False (safe default)."""
    assert _should_use_chunked(tmp_path / "ghost.txt") is False
```

### Step 2: Run to confirm they fail

```bash
python3 -m pytest tests/unit/test_api_chunked_threshold.py -v 2>&1 | tail -10
```

Expected: `FAILED` — `cannot import name '_should_use_chunked'`

---

### Step 3: Add the threshold helper and update both endpoints in `files.py`

**3a.** Add the constant and helper near the top of `src/api/routers/files.py`, after the existing imports (find the `BASE_URL` or first constant, or just after the `router = APIRouter(...)` line):

```python
_CHUNK_THRESHOLD_BYTES: int = 50 * 1024 * 1024  # 50 MB


def _should_use_chunked(path: Path) -> bool:
    """Return True when the file at *path* meets the chunked-processing threshold.

    Args:
        path: Filesystem path to the uploaded file.

    Returns:
        True if the file size is >= _CHUNK_THRESHOLD_BYTES, False otherwise
        (including when the file does not exist).
    """
    try:
        return path.stat().st_size >= _CHUNK_THRESHOLD_BYTES
    except OSError:
        return False
```

**3b.** In `validate_file`, pass `use_chunked` to `run_validate_service`. Find the existing call (around line 141):

```python
        result = run_validate_service(
            file=str(upload_path),
            mapping=str(mapping_file),
            output=str(report_path) if report_path else None,
            detailed=detailed,
            strict_fixed_width=strict_fixed_width,
            strict_level=strict_level,
        )
```

Replace with:

```python
        result = run_validate_service(
            file=str(upload_path),
            mapping=str(mapping_file),
            output=str(report_path) if report_path else None,
            detailed=detailed,
            strict_fixed_width=strict_fixed_width,
            strict_level=strict_level,
            use_chunked=_should_use_chunked(upload_path),
        )
```

**3c.** In `_run_compare_with_mapping`, replace the hardcoded `use_chunked=False`. Find (around line 182):

```python
    keys = ",".join(request.key_columns) if request.key_columns else None
    compare_result = run_compare_service(
        file1=str(upload_path1),
        file2=str(upload_path2),
        keys=keys,
        mapping=str(mapping_file),
        detailed=request.detailed,
        use_chunked=False,
    )
```

Replace with:

```python
    keys = ",".join(request.key_columns) if request.key_columns else None
    # Enable chunked compare only when key_columns are present (ChunkedFileComparator
    # requires keys) and at least one file is large enough to warrant it.
    use_chunked = bool(keys) and (
        _should_use_chunked(upload_path1) or _should_use_chunked(upload_path2)
    )
    compare_result = run_compare_service(
        file1=str(upload_path1),
        file2=str(upload_path2),
        keys=keys,
        mapping=str(mapping_file),
        detailed=request.detailed,
        use_chunked=use_chunked,
    )
```

---

### Step 4: Run tests to confirm they pass

```bash
python3 -m pytest tests/unit/test_api_chunked_threshold.py -v 2>&1 | tail -10
```

Expected: `4 passed`

---

### Step 5: Run full test suites

```bash
python3 -m pytest tests/unit/ \
  --ignore=tests/unit/test_contracts_pipeline.py \
  --ignore=tests/unit/test_pipeline_runner.py \
  --ignore=tests/unit/test_workflow_wrapper_parity.py -q 2>&1 | tail -5

python3 -m pytest tests/integration/ -q -o addopts='' 2>&1 | tail -5
```

Expected: 397+ passed, 0 failed, ≥80% coverage; 28 passed, 0 failed.

---

### Step 6: Commit

```bash
git add src/api/routers/files.py tests/unit/test_api_chunked_threshold.py
git commit -m "feat(api): auto-enable chunked processing for files > 50 MB

Adds _CHUNK_THRESHOLD_BYTES = 50 MB and _should_use_chunked(path) helper.
/validate: passes use_chunked=True to run_validate_service when the
uploaded file exceeds the threshold.
/compare: enables use_chunked when key_columns are present and at least
one file exceeds the threshold (ChunkedFileComparator requires keys).
No new API parameters — threshold is applied transparently.
Closes #41"
```
