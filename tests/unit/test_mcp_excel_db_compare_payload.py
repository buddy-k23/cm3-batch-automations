"""Unit tests for the ``excel_db_compare`` MCP tool wrapper (S24-4).

These tests pin the THIN-WRAPPER + redaction contract of
:mod:`src.mcp.excel_db_compare_tools` directly (no transport): caller-fixable
arg validation raises :class:`ToolError`; the response is BOUNDED to counts (no
raw rows / no diff field values / no credentials); the direction-aware
``only_in_db`` / ``only_in_excel`` aliases are correct for both directions; and
``include_report=True`` attaches an ADR-0023 report handle rendered from the RAW
verdict. The underlying service (S24-2) is stubbed so the wrapper is tested in
isolation — the service itself is covered by its own suite.
"""

from __future__ import annotations

import pandas as pd
import pytest
from mcp.server.fastmcp.exceptions import ToolError

import src.mcp.excel_db_compare_tools as mod
from src.mcp.excel_db_compare_tools import excel_db_compare_payload


def _raw_verdict(direction: str) -> dict:
    """A raw S24-2-shaped verdict whose compare carries DataFrames + diff rows.

    The DataFrames and the ``differences`` list hold RAW values precisely so the
    test can prove the wrapper collapses them to counts and never leaks them.
    """
    return {
        "workflow": {
            "status": "failed",
            "db_rows_extracted": 2,
            "excel_rows_read": 3,
            "query_or_table": "CUSTOMER",
            "direction": direction,
        },
        "compare": {
            "structure_compatible": True,
            "total_rows_file1": 3,
            "total_rows_file2": 2,
            "matching_rows": 1,
            # Raw rows — must NOT survive into the response.
            "only_in_file1": pd.DataFrame([{"ID": "3", "NAME": "Carol"}]),
            "only_in_file2": pd.DataFrame([]),
            # Raw diff values — must be collapsed to a COUNT.
            "differences": [
                {"keys": {"ID": "2"}, "differences": {"AMOUNT": {"file1": "200", "file2": "999"}}},
            ],
        },
    }


def test_missing_excel_file_raises():
    with pytest.raises(ToolError, match="excel_file is required"):
        excel_db_compare_payload(excel_file="", table="CUSTOMER")


def test_both_table_and_query_raises():
    with pytest.raises(ToolError, match="exactly one of"):
        excel_db_compare_payload(
            excel_file="x.xlsx", table="CUSTOMER", query="SELECT * FROM CUSTOMER"
        )


def test_neither_table_nor_query_raises():
    with pytest.raises(ToolError, match="exactly one of"):
        excel_db_compare_payload(excel_file="x.xlsx")


def test_invalid_direction_raises():
    with pytest.raises(ToolError, match="invalid direction"):
        excel_db_compare_payload(
            excel_file="x.xlsx", table="CUSTOMER", direction="sideways"
        )


def test_invalid_adapter_raises(monkeypatch):
    # build_connection_override raises ValueError for a bad adapter -> ToolError.
    with pytest.raises(ToolError, match="Invalid db_adapter"):
        excel_db_compare_payload(
            excel_file="x.xlsx",
            table="CUSTOMER",
            db_adapter="mysql",
            connection={"db_password": "p"},
        )


def test_service_file_not_found_becomes_tool_error(monkeypatch):
    def _boom(**kwargs):
        raise FileNotFoundError("Excel file not found: x.xlsx")

    monkeypatch.setattr(mod, "compare_excel_to_db", _boom)
    with pytest.raises(ToolError, match="not found"):
        excel_db_compare_payload(excel_file="x.xlsx", table="CUSTOMER")


def test_service_unexpected_error_becomes_tool_error(monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("adapter exploded")

    monkeypatch.setattr(mod, "compare_excel_to_db", _boom)
    with pytest.raises(ToolError, match="Excel<->DB compare failed"):
        excel_db_compare_payload(excel_file="x.xlsx", table="CUSTOMER")


def test_bounded_response_db_source_direction(monkeypatch):
    monkeypatch.setattr(
        mod, "compare_excel_to_db", lambda **kw: _raw_verdict("db-source")
    )
    out = excel_db_compare_payload(
        excel_file="x.xlsx", table="CUSTOMER", direction="db-source"
    )

    comp = out["compare"]
    # Counts only — DataFrames + diff rows collapsed to ints.
    assert comp["only_in_file1"] == 1
    assert comp["only_in_file2"] == 0
    assert comp["differences"] == 1
    assert comp["matching_rows"] == 1
    # db-source: file1 == DB, file2 == Excel.
    assert comp["only_in_db"] == 1
    assert comp["only_in_excel"] == 0
    assert comp["structure_compatible"] is True

    # No raw row / diff value anywhere in the serialized response.
    import json

    serialized = json.dumps(out)
    assert "Carol" not in serialized
    assert "999" not in serialized


def test_bounded_response_excel_source_direction(monkeypatch):
    monkeypatch.setattr(
        mod, "compare_excel_to_db", lambda **kw: _raw_verdict("excel-source")
    )
    out = excel_db_compare_payload(
        excel_file="x.xlsx", table="CUSTOMER", direction="excel-source"
    )

    comp = out["compare"]
    # excel-source: file1 == Excel, file2 == DB. So only_in_file1 -> only_in_excel.
    assert comp["only_in_excel"] == 1
    assert comp["only_in_db"] == 0


def test_include_report_attaches_handle_not_rows(monkeypatch, tmp_path):
    monkeypatch.setenv("VALDO_REPORTS_DIR", str(tmp_path))
    monkeypatch.setattr(
        mod, "compare_excel_to_db", lambda **kw: _raw_verdict("db-source")
    )

    captured = {}

    class _StubReporter:
        def generate(self, compare, path):
            # The renderer must see the RAW differences (full list), proving the
            # report is rendered before the bounded coercion.
            captured["differences"] = compare["differences"]
            captured["path"] = path
            from pathlib import Path

            Path(path).write_text("<html>report</html>", encoding="utf-8")

    import src.reports.renderers.comparison_renderer as renderer_mod

    monkeypatch.setattr(renderer_mod, "HTMLReporter", _StubReporter)

    out = excel_db_compare_payload(
        excel_file="x.xlsx", table="CUSTOMER", include_report=True
    )

    # Handle present (ADR 0023) — NOT inline rows.
    assert out["report_uri"].startswith("report://")
    assert out["report_url"].startswith("/reports/")
    assert out["report_path"].endswith(".html")
    # The renderer saw the full raw diff list.
    assert isinstance(captured["differences"], list) and captured["differences"]
    # The bounded compare still carries only the COUNT.
    assert out["compare"]["differences"] == 1
