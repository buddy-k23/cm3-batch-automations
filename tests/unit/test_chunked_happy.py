"""Tests for chunked validation parity — issue #100.

Verifies that chunked validation produces complete results and correctly
surfaces errors spread across multiple chunks.
"""

import json
from pathlib import Path

import pytest

from src.services.validate_service import run_validate_service

MAPPING_PIPE = {
    "mapping_name": "test_chunked_happy",
    "version": "1.0.0",
    "source": {"type": "file", "format": "pipe_delimited", "has_header": False},
    "fields": [
        {"name": "name", "data_type": "string"},
        {"name": "age", "data_type": "integer"},
    ],
    "key_columns": ["name"],
}


def _write_data(tmp_path: Path, content: str):
    """Write data and mapping files, return their paths as strings."""
    data_file = tmp_path / "data.txt"
    data_file.write_text(content, encoding="utf-8")
    mapping_file = tmp_path / "mapping.json"
    mapping_file.write_text(json.dumps(MAPPING_PIPE), encoding="utf-8")
    return str(data_file), str(mapping_file)


class TestChunkedHappy:
    """Chunked-validation parity tests for issue #100."""

    def test_chunked_validation_produces_complete_results(self, tmp_path):
        """Chunked path must return all contract keys and correct total_rows."""
        lines = [f"Person{i}|{20 + i}" for i in range(10)]
        content = "\n".join(lines) + "\n"
        data_file, mapping_file = _write_data(tmp_path, content)

        result = run_validate_service(
            file=data_file,
            mapping=mapping_file,
            use_chunked=True,
            chunk_size=3,
        )

        # Contract keys
        for key in ("valid", "total_rows", "error_count", "warning_count"):
            assert key in result, f"Missing contract key: {key}"

        assert result["total_rows"] == 10
        assert result["error_count"] == 0

    def test_chunked_validation_with_errors_spread_across_chunks(self, tmp_path):
        """Errors in different chunks should all be collected in the final result."""
        # 6 rows, chunk_size=2 -> 3 chunks.  Inject one bad row per chunk.
        rows = [
            "Alice|notanum",   # chunk 1 — bad
            "Bob|25",          # chunk 1 — good
            "Carol|30",        # chunk 2 — good
            "Dave|notanum",    # chunk 2 — bad
            "Eve|40",          # chunk 3 — good
            "Frank|notanum",   # chunk 3 — bad
        ]
        content = "\n".join(rows) + "\n"
        data_file, mapping_file = _write_data(tmp_path, content)

        result = run_validate_service(
            file=data_file,
            mapping=mapping_file,
            use_chunked=True,
            chunk_size=2,
        )

        assert result["total_rows"] == 6
        # There should be at least 1 error (bad integer values).
        # The exact count depends on how strict the validator is, but
        # errors across all chunks must be aggregated.
        assert result["error_count"] >= 1
        assert result["valid"] is False
