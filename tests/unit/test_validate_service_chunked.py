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
    assert r_standard["total_rows"] == r_chunked["total_rows"]
    assert r_standard["error_count"] == r_chunked["error_count"]


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

    r_chunked  = run_validate_service(file=str(data_file), mapping=str(mapping_file),
                                      use_chunked=True)
    r_standard = run_validate_service(file=str(data_file), mapping=str(mapping_file),
                                      use_chunked=False)
    # Both paths should produce the same row count and error count,
    # confirming the fallback routes to the standard EnhancedFileValidator.
    assert r_chunked["total_rows"] == r_standard["total_rows"]
    assert r_chunked["error_count"] == r_standard["error_count"]


def test_chunked_validate_detects_errors_in_bad_data(tmp_path):
    """Errors in data are surfaced through the chunked path."""
    bad_content = "Alice|thirty\nBob|25\n"  # "thirty" is not an integer
    data_file = tmp_path / "bad.txt"
    data_file.write_text(bad_content, encoding="utf-8")
    mapping_file = tmp_path / "mapping.json"
    mapping_file.write_text(json.dumps(MAPPING_PIPE), encoding="utf-8")

    result = run_validate_service(file=str(data_file), mapping=str(mapping_file), use_chunked=True)
    assert result["error_count"] > 0, "Expected errors for non-integer age value"
    assert result["valid"] == False
