"""Tests for Fix 4: Total validation time in result dict.

Ensures run_validate_service always populates 'elapsed_seconds' in the
returned dict.

All imports in validate_service are lazy (inside the function body), so the
cleanest approach is a real end-to-end call against a tiny pipe-delimited file
with no mapping.  This exercises the shortest standard code path while still
exercising the timing code.
"""

import pytest
from src.services.validate_service import run_validate_service


def test_validate_service_returns_elapsed_seconds(tmp_path):
    """run_validate_service must include 'elapsed_seconds' in result dict."""
    data_file = tmp_path / "data.txt"
    data_file.write_text("col1|col2\nA|B\n", encoding="utf-8")

    result = run_validate_service(file=str(data_file))

    assert "elapsed_seconds" in result, "Result must contain 'elapsed_seconds'"
    assert isinstance(result["elapsed_seconds"], float), "elapsed_seconds must be a float"
    assert result["elapsed_seconds"] >= 0.0, "elapsed_seconds must be non-negative"


def test_validate_service_elapsed_seconds_is_reasonable(tmp_path):
    """Elapsed seconds must be a small non-negative float for fast runs."""
    data_file = tmp_path / "data.txt"
    data_file.write_text("col1|col2\nA|B\n", encoding="utf-8")

    result = run_validate_service(file=str(data_file))

    assert result["elapsed_seconds"] < 60.0, (
        "Elapsed time for a tiny file should be well under 60s"
    )
