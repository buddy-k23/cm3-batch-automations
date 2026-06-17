"""Unit tests for the MCP ``export_failed_rows`` tool adapter (S22-3, #440).

Exercises :func:`src.mcp.export_tools.export_failed_rows_payload` directly
(no MCP transport — that path is covered by
``tests/integration/test_mcp_export_tool.py``). The adapter is a thin wrapper
over :func:`src.services.validate_service.run_validate_service` +
:func:`src.services.error_extractor.extract_error_rows` — the same code path the
CLI ``validate --export-errors`` flag and the ``POST /api/v1/files/export-errors``
endpoint drive.

These tests pin: a real fixed-width validate+export (failed rows written, counts
returned), the PII posture (NO raw row values in the response), the clean-file
case (zero failed rows, still a result), and every caller-fixable ``ToolError``
branch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from src.mcp.export_tools import export_failed_rows_payload


# A headerless pipe-delimited mapping: id + amount. A rules config requires
# amount to be numeric. The sentinel token "SECRET99" sits in the second data
# row's amount field so we can assert the raw value never leaks into the
# response (it must appear only in the on-disk export file).
_MAPPING = {
    "mapping_name": "export_tool_unit_test",
    "version": "1.0.0",
    "source": {"type": "file", "format": "pipe_delimited", "has_header": False},
    "fields": [
        {"name": "id", "data_type": "string"},
        {"name": "amount", "data_type": "string"},
    ],
}

_RULES = {
    "rules": [
        {
            "id": "amount_numeric",
            "name": "Amount must be numeric",
            "type": "field_validation",
            "field": "amount",
            "operator": "numeric",
            "severity": "error",
        }
    ]
}

# Row 1 valid (numeric amount). Row 2 invalid — non-numeric "SECRET99". Row 3 valid.
_PIPE_DATA = "0001|100\n0002|SECRET99\n0003|300\n"


def _write_fixture(tmp_path: Path) -> tuple[str, str, str]:
    """Write the data file + mapping + rules JSON; return (file, mapping, rules)."""
    data_file = tmp_path / "txns.psv"
    data_file.write_text(_PIPE_DATA, encoding="utf-8")
    mapping_file = tmp_path / "txn_mapping.json"
    mapping_file.write_text(json.dumps(_MAPPING), encoding="utf-8")
    rules_file = tmp_path / "txn_rules.json"
    rules_file.write_text(json.dumps(_RULES), encoding="utf-8")
    return str(data_file), str(mapping_file), str(rules_file)


# ---------------------------------------------------------------------------
# Happy path — real validate + export (no Oracle)
# ---------------------------------------------------------------------------


def test_exports_failed_rows_and_returns_counts(tmp_path):
    data_file, mapping_file, rules_file = _write_fixture(tmp_path)
    output = tmp_path / "errors.psv"

    out = export_failed_rows_payload(
        file=data_file, mapping=mapping_file, output=str(output), rules=rules_file
    )

    # Path + counts are returned; the export file is written.
    assert out["output_path"] == str(output)
    assert Path(out["output_path"]).exists()
    assert out["failed_row_count"] == 1
    assert out["total_rows"] == 3
    assert out["valid_rows"] == 2
    assert out["valid"] is False


def test_response_contains_no_raw_row_values(tmp_path):
    """CRITICAL PII posture: the response must not echo any raw row content."""
    data_file, mapping_file, rules_file = _write_fixture(tmp_path)
    output = tmp_path / "errors.psv"

    out = export_failed_rows_payload(
        file=data_file, mapping=mapping_file, output=str(output), rules=rules_file
    )

    blob = json.dumps(out)
    # The failed row's raw value must NOT appear anywhere in the response.
    assert "SECRET99" not in blob
    # Response is counts + path only — no list of rows/values/errors.
    assert "rows" not in out
    assert "values" not in out
    assert "errors" not in out
    # But the failed rows ARE written to the on-disk export file.
    assert "SECRET99" in Path(output).read_text(encoding="utf-8")


def test_clean_file_zero_failed_rows_is_a_result(tmp_path):
    """A file with no errors yields failed_row_count=0 — a result, not an error."""
    data_file = tmp_path / "clean.psv"
    data_file.write_text("0001|100\n0003|300\n", encoding="utf-8")
    mapping_file = tmp_path / "m.json"
    mapping_file.write_text(json.dumps(_MAPPING), encoding="utf-8")
    rules_file = tmp_path / "r.json"
    rules_file.write_text(json.dumps(_RULES), encoding="utf-8")
    output = tmp_path / "errors.psv"

    out = export_failed_rows_payload(
        file=str(data_file),
        mapping=mapping_file.as_posix(),
        output=str(output),
        rules=str(rules_file),
    )

    assert out["failed_row_count"] == 0
    assert out["valid"] is True
    assert Path(output).exists()


def test_output_path_is_echoed_verbatim(tmp_path):
    data_file, mapping_file, rules_file = _write_fixture(tmp_path)
    nested = tmp_path / "nested" / "dir" / "errors.psv"

    out = export_failed_rows_payload(
        file=data_file, mapping=mapping_file, output=str(nested), rules=rules_file
    )

    # Parent dirs are created by the extractor; path echoed back verbatim.
    assert out["output_path"] == str(nested)
    assert nested.exists()


# ---------------------------------------------------------------------------
# Caller-fixable ToolError branches
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["", None, 123])
def test_blank_file_raises(bad, tmp_path):
    _, mapping_file, _ = _write_fixture(tmp_path)
    with pytest.raises(ToolError, match="file is required"):
        export_failed_rows_payload(
            file=bad, mapping=mapping_file, output=str(tmp_path / "e.psv")
        )  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["", None, 123])
def test_blank_output_raises(bad, tmp_path):
    data_file, mapping_file, _ = _write_fixture(tmp_path)
    with pytest.raises(ToolError, match="output is required"):
        export_failed_rows_payload(
            file=data_file, mapping=mapping_file, output=bad
        )  # type: ignore[arg-type]


def test_missing_file_raises(tmp_path):
    _, mapping_file, _ = _write_fixture(tmp_path)
    with pytest.raises(ToolError, match="not found"):
        export_failed_rows_payload(
            file=str(tmp_path / "nope.psv"),
            mapping=mapping_file,
            output=str(tmp_path / "e.psv"),
        )


def test_missing_mapping_raises(tmp_path):
    data_file, _, _ = _write_fixture(tmp_path)
    with pytest.raises(ToolError, match="not found"):
        export_failed_rows_payload(
            file=data_file,
            mapping=str(tmp_path / "nope.json"),
            output=str(tmp_path / "e.psv"),
        )


def test_unparseable_mapping_raises(tmp_path):
    data_file, _, _ = _write_fixture(tmp_path)
    bad_mapping = tmp_path / "bad.json"
    bad_mapping.write_text("{ not valid json", encoding="utf-8")
    with pytest.raises(ToolError):
        export_failed_rows_payload(
            file=data_file,
            mapping=str(bad_mapping),
            output=str(tmp_path / "e.psv"),
        )


def test_validation_infra_failure_surfaces_as_toolerror(tmp_path, monkeypatch):
    data_file, mapping_file, _ = _write_fixture(tmp_path)

    def _boom(**kwargs):
        raise RuntimeError("validator exploded")

    monkeypatch.setattr(
        "src.services.validate_service.run_validate_service", _boom
    )
    with pytest.raises(ToolError, match="Export failed"):
        export_failed_rows_payload(
            file=data_file, mapping=mapping_file, output=str(tmp_path / "e.psv")
        )
