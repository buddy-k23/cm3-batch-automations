import json
from pathlib import Path

from src.reports.renderers.validation_renderer import ValidationReporter


def _sample_result():
    return {
        "valid": True,
        "timestamp": "2026-02-20T00:00:00Z",
        "file_metadata": {
            "file_name": "sample.txt",
            "format": "pipe_delimited",
            "size_bytes": 100,
            "size_mb": 0.0001,
            "modified_time": "2026-02-20T00:00:00",
        },
        "quality_metrics": {
            "quality_score": 99.5,
            "total_rows": 2,
            "total_columns": 2,
            "completeness_pct": 100.0,
            "uniqueness_pct": 100.0,
            "total_cells": 4,
            "filled_cells": 4,
            "null_cells": 0,
            "unique_rows": 2,
            "duplicate_rows": 0,
        },
        "errors": [],
        "warnings": [{"severity": "warning", "message": "sample warning"}],
        "info": [{"severity": "info", "message": "sample info"}],
        "error_count": 0,
        "warning_count": 1,
        "info_count": 1,
        "field_analysis": {
            0: {"inferred_type": "string", "fill_rate_pct": 100.0, "unique_count": 2},
            "name": {"inferred_type": "string", "fill_rate_pct": 100.0, "unique_count": 2},
        },
        "duplicate_analysis": {"unique_rows": 2, "duplicate_rows": 0, "duplicate_pct": 0.0},
        "date_analysis": {},
        "business_rules": None,
        "appendix": {"sample_records": []},
    }


def test_validation_renderer_generates_html_and_sidecars(tmp_path):
    out = tmp_path / "report.html"
    reporter = ValidationReporter()
    reporter.generate(_sample_result(), str(out))

    assert out.exists()
    html = out.read_text(encoding="utf-8").lower()
    assert "file validation report" in html

    err = tmp_path / "report_errors.csv"
    warn = tmp_path / "report_warnings.csv"
    assert err.exists()
    assert warn.exists()


def test_validation_renderer_layout_counts_and_field_order(tmp_path):
    out = tmp_path / "report_layout.html"
    mapping = tmp_path / "mapping.json"
    mapping.write_text(
        json.dumps(
            {
                "fields": [
                    {"name": "LOCATION-CODE", "position": 1, "length": 6, "required": True},
                    {"name": "ACCT-NUM", "position": 7, "length": 18, "required": True},
                ]
            }
        ),
        encoding="utf-8",
    )

    payload = _sample_result()
    payload["field_analysis"] = {
        "acct-num": {"inferred_type": "string", "fill_rate_pct": 100.0, "unique_count": 2},
        "location-code": {"inferred_type": "string", "fill_rate_pct": 100.0, "unique_count": 2},
    }
    payload["appendix"] = {
        "validation_config": {"mapping_file": str(mapping)},
        "mapping_details": {
            "required_field_count": 2,
            "required_fields": ["LOCATION-CODE", "ACCT-NUM"],
            "total_fields": 2,
            "total_width": 24,
        },
        "affected_rows": {"total_affected_rows": 1, "affected_row_pct": 50.0},
    }

    reporter = ValidationReporter()
    reporter.generate(payload, str(out))
    html = out.read_text(encoding="utf-8")

    assert "Errored Rows" in html
    assert "Good Rows" in html
    assert "Required Fields" in html

    assert html.index("Required Fields") < html.index("Field-Level Analysis")
    assert html.index("location-code") < html.index("acct-num")


def test_validation_renderer_renders_chunked_telemetry_rows(tmp_path):
    out = tmp_path / "report_telemetry.html"
    payload = _sample_result()
    payload["appendix"] = {
        "validation_config": {
            "mode": "chunked",
            "chunk_size": 25000,
            "elapsed_seconds": 1.23,
            "rows_per_second": 4567.89,
            "mapping_file": "config/mappings/sample.json",
            "validator_version": "1.0.0",
        },
        "mapping_details": {"total_fields": 2, "required_field_count": 0, "required_fields": []},
    }

    reporter = ValidationReporter()
    reporter.generate(payload, str(out))

    html = out.read_text(encoding="utf-8")
    assert "Validation Mode" in html
    assert "chunked" in html
    assert "Chunk Size" in html
    assert "25,000" in html
    assert "Rows / Second" in html


def test_validation_renderer_required_field_error_summary(tmp_path):
    out = tmp_path / "report_required.html"
    payload = _sample_result()
    payload["valid"] = False
    payload["errors"] = [
        {"severity": "error", "code": "FW_REQ_001", "field": "LOCATION-CODE", "message": "Required field 'LOCATION-CODE' is empty"},
        {"severity": "error", "code": "FW_REQ_001", "field": "LOCATION-CODE", "message": "Required field 'LOCATION-CODE' is empty"},
        {"severity": "error", "code": "FW_REQ_001", "field": "BASE-CURRENCY", "message": "Required field 'BASE-CURRENCY' is empty"},
    ]

    reporter = ValidationReporter()
    reporter.generate(payload, str(out))

    html = out.read_text(encoding="utf-8")
    assert "Required Field Error Summary" in html
    assert "Mapping Errors" in html
    assert "Data Errors" in html
    assert "LOCATION-CODE" in html
    assert "BASE-CURRENCY" in html
