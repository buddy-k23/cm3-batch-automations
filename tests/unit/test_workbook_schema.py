"""Unit tests for the EC-S1 onboarding-workbook schema validator.

Covers:

  * test_template_validates                          — blank canonical template
  * test_shaw_workbook_validates                     — SHAW worked example
  * test_missing_required_sheet_raises               — missing ``Source`` sheet
  * test_missing_required_column_in_source_sheet_raises — missing ``source_code`` column
  * test_multi_record_sheet_naming_pattern_enforced  — ``Multirecord_TRANERT`` (wrong case)
  * test_unknown_sheet_warns_not_fails               — ``Notes`` scratch sheet
"""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook

from src.onboarding.workbook_schema import (
    CROSS_TYPE_RULES_REQUIRED_COLUMNS,
    SOURCE_SHEET_REQUIRED_COLUMNS,
    WorkbookSchemaError,
    assert_workbook_valid,
    validate_workbook,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = REPO_ROOT / "templates"


# ---------------------------------------------------------------------------
# Helpers — synthesise minimal workbooks for the negative cases.
# ---------------------------------------------------------------------------


def _write_headers(ws, headers: list[str]) -> None:
    for col_idx, header in enumerate(headers, start=1):
        ws.cell(row=1, column=col_idx, value=header)


def _build_minimal_workbook(
    tmp_path: Path,
    *,
    source_columns: list[str] | None = None,
    extra_sheets: dict[str, list[str]] | None = None,
    omit_sheets: list[str] | None = None,
    filename: str = "wb.xlsx",
) -> Path:
    """Build a minimal valid-shape workbook on disk, optionally mutated.

    Args:
        tmp_path: pytest ``tmp_path`` fixture.
        source_columns: Override the Source sheet column list. Default = all
            required columns. Pass an explicit list to omit columns.
        extra_sheets: ``{sheet_name: [column_headers]}`` — added before save.
        omit_sheets: List of fixed sheet names to NOT create.

    Returns:
        Path to the saved workbook.
    """
    source_columns = (
        list(SOURCE_SHEET_REQUIRED_COLUMNS) if source_columns is None else source_columns
    )
    omit_sheets = omit_sheets or []
    extra_sheets = extra_sheets or {}

    wb = Workbook()
    wb.remove(wb.active)

    if "Source" not in omit_sheets:
        ws = wb.create_sheet("Source")
        _write_headers(ws, source_columns)
    if "InputFiles" not in omit_sheets:
        ws = wb.create_sheet("InputFiles")
        _write_headers(
            ws,
            [
                "file_type",
                "glob",
                "mapping_sheet",
                "target_staging_table",
                "thresholds_max_errors",
            ],
        )
    if "OutputFiles" not in omit_sheets:
        ws = wb.create_sheet("OutputFiles")
        _write_headers(
            ws,
            [
                "file_type",
                "glob",
                "mapping_sheet",
                "rules_sheet",
                "tolerance_max_errors",
                "tolerance_max_error_pct",
                "tolerance_ignore_fields",
            ],
        )

    for sheet_name, headers in extra_sheets.items():
        ws = wb.create_sheet(sheet_name)
        _write_headers(ws, headers)

    out_path = tmp_path / filename
    wb.save(str(out_path))
    return out_path


# ---------------------------------------------------------------------------
# Positive cases — the shipped artefacts must validate cleanly.
# ---------------------------------------------------------------------------


def test_template_validates():
    """The canonical blank template ships with zero schema findings."""
    findings = validate_workbook(TEMPLATES_DIR / "source_onboarding_template.xlsx")
    errors = [f for f in findings if f.severity == "error"]
    assert errors == [], f"Unexpected errors in blank template: {errors}"


def test_shaw_workbook_validates():
    """The SHAW worked-example workbook ships with zero schema findings."""
    findings = validate_workbook(TEMPLATES_DIR / "SHAW_onboarding.xlsx")
    errors = [f for f in findings if f.severity == "error"]
    assert errors == [], f"Unexpected errors in SHAW workbook: {errors}"


# ---------------------------------------------------------------------------
# Negative cases — synthesised in tmp_path.
# ---------------------------------------------------------------------------


def test_missing_required_sheet_raises(tmp_path):
    """A workbook missing the ``Source`` sheet must fail validation with a
    helpful, sheet-addressable error message.
    """
    path = _build_minimal_workbook(tmp_path, omit_sheets=["Source"])

    with pytest.raises(WorkbookSchemaError) as exc_info:
        assert_workbook_valid(path)

    msg = str(exc_info.value)
    assert "Source" in msg
    assert "missing" in msg.lower()
    # And the structured findings must include one with sheet='Source'.
    assert any(f.sheet == "Source" for f in exc_info.value.findings)


def test_missing_required_column_in_source_sheet_raises(tmp_path):
    """A workbook whose Source sheet omits ``source_code`` must fail with a
    cell-addressable error.
    """
    columns_without_source_code = [
        c for c in SOURCE_SHEET_REQUIRED_COLUMNS if c != "source_code"
    ]
    path = _build_minimal_workbook(tmp_path, source_columns=columns_without_source_code)

    with pytest.raises(WorkbookSchemaError) as exc_info:
        assert_workbook_valid(path)

    msg = str(exc_info.value)
    assert "source_code" in msg
    # Cell-addressability — one of the findings must carry a concrete cell ref.
    cell_findings = [f for f in exc_info.value.findings if f.cell]
    assert cell_findings, "Expected at least one finding with a cell address."
    target = next(
        (f for f in exc_info.value.findings if "source_code" in f.reason),
        None,
    )
    assert target is not None, "Expected a finding flagging the missing 'source_code'."
    assert target.cell, "The 'source_code' finding must include a cell address."
    assert target.sheet == "Source"


def test_multi_record_sheet_naming_pattern_enforced(tmp_path):
    """A sheet named ``Multirecord_TRANERT`` (wrong case on the prefix) must
    fail validation with a hint that the prefix is case-sensitive.
    """
    # The bad sheet must declare the MULTI_RECORD_REQUIRED_COLUMNS to ensure
    # the error we get is the *naming* error, not a column-shape error.
    bad_sheet_name = "Multirecord_TRANERT"
    extra_sheets = {
        bad_sheet_name: [
            "record_type_name",
            "discriminator_field",
            "discriminator_position",
            "discriminator_length",
            "match_kind",
            "match_value",
            "mapping_sheet",
            "rules_sheet",
            "cardinality",
        ]
    }
    path = _build_minimal_workbook(tmp_path, extra_sheets=extra_sheets)

    with pytest.raises(WorkbookSchemaError) as exc_info:
        assert_workbook_valid(path)

    msg = str(exc_info.value)
    assert "Multirecord_TRANERT" in msg
    # The hint must mention case sensitivity / MultiRecord_ in the fix.
    assert "MultiRecord_" in msg or "case" in msg.lower()
    # And the finding must be tagged against the badly-named sheet.
    assert any(
        f.sheet == bad_sheet_name and f.severity == "error"
        for f in exc_info.value.findings
    )


def test_unknown_sheet_warns_not_fails(tmp_path):
    """A sheet whose name matches no known pattern (e.g. ``Notes``) must be
    surfaced as a warning, not an error, so BAs can keep scratch sheets in
    the workbook.
    """
    extra_sheets = {"Notes": ["topic", "owner", "details"]}
    path = _build_minimal_workbook(tmp_path, extra_sheets=extra_sheets)

    # No exception expected.
    assert_workbook_valid(path)

    findings = validate_workbook(path)
    warnings = [f for f in findings if f.severity == "warning" and f.sheet == "Notes"]
    errors = [f for f in findings if f.severity == "error"]
    assert errors == [], f"Did not expect errors, got: {errors}"
    assert warnings, "Expected at least one warning for the unrecognised 'Notes' sheet."
    assert "Notes" in str(warnings[0])


# ---------------------------------------------------------------------------
# EC-S8 — CrossTypeRules_<FILETYPE> sheet recognition.
# ---------------------------------------------------------------------------


def test_cross_type_rules_sheet_recognized(tmp_path):
    """A synthetic workbook carrying a ``CrossTypeRules_TRANERT`` sheet with
    the canonical column set validates clean.

    Guards EC-S8 acceptance criterion #2: the schema validator must
    classify ``CrossTypeRules_<FILETYPE>`` as a known dynamic-prefix
    sheet, NOT raise an "unknown sheet" warning, and apply the
    canonical column check against it.
    """
    extra_sheets = {
        "CrossTypeRules_TRANERT": list(CROSS_TYPE_RULES_REQUIRED_COLUMNS),
    }
    path = _build_minimal_workbook(tmp_path, extra_sheets=extra_sheets)

    # No exception expected.
    assert_workbook_valid(path)

    findings = validate_workbook(path)
    errors = [f for f in findings if f.severity == "error"]
    assert errors == [], (
        f"Did not expect errors for CrossTypeRules sheet; got: {errors}"
    )
    # And NOT classified as an unknown sheet (no warning for it either).
    unknown_warnings = [
        f
        for f in findings
        if f.severity == "warning" and f.sheet == "CrossTypeRules_TRANERT"
    ]
    assert unknown_warnings == [], (
        f"CrossTypeRules_TRANERT should be recognised, not warned about; "
        f"got: {unknown_warnings}"
    )


def test_cross_type_rules_sheet_missing_required_column_raises(tmp_path):
    """A ``CrossTypeRules_<FILETYPE>`` sheet missing one of the canonical
    required columns (``check``) must fail validation with a
    cell-addressable error pointing at the missing column.
    """
    columns_without_check = [
        c for c in CROSS_TYPE_RULES_REQUIRED_COLUMNS if c != "check"
    ]
    extra_sheets = {"CrossTypeRules_TRANERT": columns_without_check}
    path = _build_minimal_workbook(tmp_path, extra_sheets=extra_sheets)

    with pytest.raises(WorkbookSchemaError) as exc_info:
        assert_workbook_valid(path)

    msg = str(exc_info.value)
    assert "check" in msg
    assert any(
        f.sheet == "CrossTypeRules_TRANERT" and "check" in f.reason
        for f in exc_info.value.findings
    )
