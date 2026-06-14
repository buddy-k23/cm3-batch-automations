"""Unit tests for the EC-S2 onboarding-workbook reader.

Covers (11 cases):

  * test_shaw_workbook_parses                             — end-to-end on SHAW
  * test_dash_field_names_preserved                       — LN-NUM-ERT verbatim
  * test_pipe_separated_parsing                           — A|B|C → [A,B,C]
  * test_blank_optional_returns_none                      — None, not 0
  * test_blank_string_field_returns_empty_string          — "", not None
  * test_bool_truthy_variants                             — yes/true/1/YES
  * test_invalid_bool_raises                              — "maybe" → error
  * test_invalid_workbook_raises_schema_error             — delegate to EC-S1
  * test_multi_record_sheet_keyed_by_file_type            — dict[file_type]
  * test_reconciliation_file_wide_assertions_lifted_to_sheet_level
  * test_mapping_sheet_lookup_via_outputfilespec          — cross-ref contract
"""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook

from src.onboarding.models import (
    OnboardingWorkbook,
    WorkbookReadError,
)
from src.onboarding.workbook_reader import WorkbookReader, read_workbook
from src.onboarding.workbook_schema import (
    INPUT_FILES_REQUIRED_COLUMNS,
    OUTPUT_FILES_REQUIRED_COLUMNS,
    SOURCE_SHEET_REQUIRED_COLUMNS,
    WorkbookSchemaError,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = REPO_ROOT / "templates"
SHAW_WORKBOOK = TEMPLATES_DIR / "SHAW_onboarding.xlsx"


# ---------------------------------------------------------------------------
# Synthetic-workbook helpers (mirror the EC-S1 test helpers but allow data
# rows, not just headers).
# ---------------------------------------------------------------------------


def _write_row(ws, row_number: int, values: list[object]) -> None:
    for col_idx, value in enumerate(values, start=1):
        ws.cell(row=row_number, column=col_idx, value=value)


# Default Source data row used when the test does not override individual cells.
# Order MUST match SOURCE_SHEET_REQUIRED_COLUMNS exactly.
_DEFAULT_SOURCE_DATA: dict[str, object] = {
    "source_code": "TEST",
    "schema_version": 1,
    "release_tag": "2026.M06",
    "description": "Synthetic test workbook.",
    "staging_schema": "APP_INT",
    "output_root": "/tmp/test",
    "java_load_script": "/tmp/load.sh",
    "java_generate_script": "",
    "gate_load_blocking": "true",
    "gate_load_invoke_java": "false",
    "gate_f2s_blocking": "true",
    "gate_generate_blocking": "false",
    "gate_generate_invoke_java": "false",
    "gate_l1_blocking": "true",
    "gate_l2b_blocking": "true",
    "gate_l3_blocking": "false",
    "gate_mr_report_blocking": "false",
}


def _build_workbook(
    tmp_path: Path,
    *,
    source_overrides: dict[str, object] | None = None,
    input_files_rows: list[dict[str, object]] | None = None,
    output_files_rows: list[dict[str, object]] | None = None,
    omit_sheets: list[str] | None = None,
    filename: str = "wb.xlsx",
) -> Path:
    """Synthesise a minimal valid-shape workbook with one Source data row.

    Args:
        tmp_path: pytest tmp_path fixture.
        source_overrides: Per-column overrides for the Source data row.
        input_files_rows: Extra data rows for InputFiles (header always written).
        output_files_rows: Extra data rows for OutputFiles.
        omit_sheets: Names of fixed sheets to skip creating.
        filename: Output filename in tmp_path.

    Returns:
        Path to the saved workbook.
    """
    omit_sheets = omit_sheets or []
    source_overrides = source_overrides or {}
    input_files_rows = input_files_rows or []
    output_files_rows = output_files_rows or []

    wb = Workbook()
    wb.remove(wb.active)

    if "Source" not in omit_sheets:
        ws = wb.create_sheet("Source")
        _write_row(ws, 1, list(SOURCE_SHEET_REQUIRED_COLUMNS))
        data_row: list[object] = []
        for col in SOURCE_SHEET_REQUIRED_COLUMNS:
            value = (
                source_overrides[col]
                if col in source_overrides
                else _DEFAULT_SOURCE_DATA[col]
            )
            data_row.append(value)
        _write_row(ws, 2, data_row)

    if "InputFiles" not in omit_sheets:
        ws = wb.create_sheet("InputFiles")
        _write_row(ws, 1, list(INPUT_FILES_REQUIRED_COLUMNS))
        for idx, row_dict in enumerate(input_files_rows, start=2):
            row_values = [row_dict.get(col) for col in INPUT_FILES_REQUIRED_COLUMNS]
            _write_row(ws, idx, row_values)

    if "OutputFiles" not in omit_sheets:
        ws = wb.create_sheet("OutputFiles")
        _write_row(ws, 1, list(OUTPUT_FILES_REQUIRED_COLUMNS))
        for idx, row_dict in enumerate(output_files_rows, start=2):
            row_values = [row_dict.get(col) for col in OUTPUT_FILES_REQUIRED_COLUMNS]
            _write_row(ws, idx, row_values)

    out_path = tmp_path / filename
    wb.save(str(out_path))
    return out_path


# ---------------------------------------------------------------------------
# 1. End-to-end SHAW workbook parses cleanly.
# ---------------------------------------------------------------------------


def test_shaw_workbook_parses():
    """The SHAW worked example parses into a fully-populated dataclass tree."""
    workbook = read_workbook(SHAW_WORKBOOK)

    assert isinstance(workbook, OnboardingWorkbook)
    assert workbook.source.source_code == "SHAW"
    assert workbook.source.schema_version == 1
    assert workbook.source.release_tag == "2026.M06"
    assert workbook.source.gate_l1_blocking is True
    assert workbook.source.gate_l3_blocking is True
    assert workbook.source.java_generate_script == ""

    assert len(workbook.input_files) == 6
    assert len(workbook.output_files) == 16

    # Multi-record sheets: TRANERT + ATOCTRAN (exactly two).
    assert set(workbook.multi_record_sheets) == {"TRANERT", "ATOCTRAN"}
    # TRANERT has 8 rows in the worked example: batch_header + 7 rt_* rows.
    tranert = workbook.multi_record_sheets["TRANERT"]
    assert len(tranert.rows) == 8
    record_type_names = {r.record_type_name for r in tranert.rows}
    assert "batch_header" in record_type_names
    assert "rt_32000" in record_type_names
    # ATOCTRAN has 7 rt_* rows in the worked example.
    atoctran = workbook.multi_record_sheets["ATOCTRAN"]
    assert len(atoctran.rows) == 7

    # Reconciliation sheet for TRANERT is present and keyed by file_type.
    assert set(workbook.reconciliation_sheets) == {"TRANERT"}


# ---------------------------------------------------------------------------
# 2. DASH-style field names preserved verbatim (NEVER snake-cased).
# ---------------------------------------------------------------------------


def test_dash_field_names_preserved():
    """LN-NUM-ERT must round-trip exactly — no snake_case normalisation."""
    workbook = read_workbook(SHAW_WORKBOOK)

    # TRANERT_REC_Mapping contains LN-NUM-ERT.
    rec_sheet = workbook.mapping_sheets["TRANERT_REC_Mapping"]
    field_names = [row.field_name for row in rec_sheet.rows]
    assert "LN-NUM-ERT" in field_names, (
        f"Expected DASH-style 'LN-NUM-ERT' in TRANERT_REC_Mapping; "
        f"got {field_names!r}"
    )
    # And the snake_case form must NOT appear.
    assert "ln_num_ert" not in [n.lower() for n in field_names]


# ---------------------------------------------------------------------------
# 3. Pipe-separated cell parsing.
# ---------------------------------------------------------------------------


def test_pipe_separated_parsing(tmp_path):
    """Cells containing 'A|B|C' must parse into ['A', 'B', 'C']."""
    path = _build_workbook(
        tmp_path,
        output_files_rows=[
            {
                "file_type": "EXAMPLE",
                "glob": "example_*.txt",
                "mapping_sheet": "EXAMPLE_Mapping",
                "rules_sheet": "EXAMPLE_Rules",
                "tolerance_max_errors": 10,
                "tolerance_max_error_pct": 1.5,
                "tolerance_ignore_fields": "A|B|C",
            }
        ],
    )
    workbook = read_workbook(path)
    assert workbook.output_files[0].tolerance_ignore_fields == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# 4. Blank optional numeric cells return None (not 0).
# ---------------------------------------------------------------------------


def test_blank_optional_returns_none(tmp_path):
    """Blank thresholds_max_errors must round-trip as None, not 0."""
    path = _build_workbook(
        tmp_path,
        input_files_rows=[
            {
                "file_type": "EXAMPLE",
                "glob": "example_*.txt",
                "mapping_sheet": "EXAMPLE_Mapping",
                "target_staging_table": "STG_EXAMPLE",
                "thresholds_max_errors": None,  # blank cell
            }
        ],
    )
    workbook = read_workbook(path)
    assert workbook.input_files[0].thresholds_max_errors is None


# ---------------------------------------------------------------------------
# 5. Blank string fields return "" (not None) where empty-string is meaningful.
# ---------------------------------------------------------------------------


def test_blank_string_field_returns_empty_string(tmp_path):
    """Blank description on Source sheet projects to empty string."""
    path = _build_workbook(
        tmp_path,
        source_overrides={"description": ""},
    )
    workbook = read_workbook(path)
    assert workbook.source.description == ""
    assert isinstance(workbook.source.description, str)


# ---------------------------------------------------------------------------
# 6. Bool truthy/falsy variants.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("yes", True),
        ("true", True),
        ("1", True),
        ("YES", True),
        ("TRUE", True),
        ("Y", True),
        ("no", False),
        ("false", False),
        ("0", False),
        ("NO", False),
        ("N", False),
        (True, True),  # native bool from formula cell
        (False, False),
    ],
)
def test_bool_truthy_variants(tmp_path, raw, expected):
    """gate_l1_blocking accepts true/false/yes/no/1/0 case-insensitively."""
    path = _build_workbook(
        tmp_path,
        source_overrides={"gate_l1_blocking": raw},
        filename=f"wb_{repr(raw).replace('/', '_')}.xlsx",
    )
    workbook = read_workbook(path)
    assert workbook.source.gate_l1_blocking is expected


# ---------------------------------------------------------------------------
# 7. Invalid bool value raises WorkbookReadError with cell address.
# ---------------------------------------------------------------------------


def test_invalid_bool_raises(tmp_path):
    """Bool cell containing 'maybe' raises WorkbookReadError pointing at the cell."""
    path = _build_workbook(
        tmp_path,
        source_overrides={"gate_l1_blocking": "maybe"},
    )
    with pytest.raises(WorkbookReadError) as exc_info:
        read_workbook(path)

    msg = str(exc_info.value)
    assert "gate_l1_blocking" in msg
    assert "maybe" in msg
    # Cell-addressability — message must contain a Source!XN address.
    assert "Source!" in msg


# ---------------------------------------------------------------------------
# 8. Schema-invalid workbook raises WorkbookSchemaError (delegated, NOT
#    re-raised as a WorkbookReadError).
# ---------------------------------------------------------------------------


def test_invalid_workbook_raises_schema_error(tmp_path):
    """Missing 'Source' sheet must raise WorkbookSchemaError unchanged."""
    path = _build_workbook(tmp_path, omit_sheets=["Source"])
    with pytest.raises(WorkbookSchemaError) as exc_info:
        read_workbook(path)
    # Must NOT be a WorkbookReadError.
    assert not isinstance(exc_info.value, WorkbookReadError)
    assert "Source" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 9. MultiRecord sheets keyed by file_type with the expected record types.
# ---------------------------------------------------------------------------


def test_multi_record_sheet_keyed_by_file_type():
    """multi_record_sheets['TRANERT'].file_type matches its key."""
    workbook = read_workbook(SHAW_WORKBOOK)
    tranert = workbook.multi_record_sheets["TRANERT"]
    assert tranert.file_type == "TRANERT"

    # Verify the expected record-type names from the SHAW worked example.
    record_type_names = {r.record_type_name for r in tranert.rows}
    assert record_type_names == {
        "batch_header",
        "rt_32000",
        "rt_32001",
        "rt_32005",
        "rt_32010",
        "rt_32025",
        "rt_32040",
        "rt_32075",
    }

    # batch_header uses position_first, rt_32000 uses discriminator_equals.
    by_name = {r.record_type_name: r for r in tranert.rows}
    assert by_name["batch_header"].match_kind == "position_first"
    assert by_name["batch_header"].match_value == "first"
    assert by_name["rt_32000"].match_kind == "discriminator_equals"
    assert by_name["rt_32000"].match_value == "32000"
    # DASH-style discriminator field preserved.
    assert by_name["batch_header"].discriminator_field == "TRN-COD-ERT"


# ---------------------------------------------------------------------------
# 10. Reconciliation first-row file-wide assertion lifted to sheet level.
# ---------------------------------------------------------------------------


def test_reconciliation_file_wide_assertions_lifted_to_sheet_level():
    """The first data row's 'assertions' cell becomes file_wide_assertions."""
    workbook = read_workbook(SHAW_WORKBOOK)
    recon = workbook.reconciliation_sheets["TRANERT"]
    assert recon.file_type == "TRANERT"
    assert recon.file_wide_assertions == [
        "header.ITM-CNT-BRT == sum(detail_row_counts)"
    ]
    # The first row (batch_header) is still present in rows — only the
    # 'assertions' cell is lifted, not the whole row.
    assert recon.rows[0].record_type_name == "batch_header"
    # And the rt_32005 row has its key_columns parsed from the pipe list.
    rt_32005 = next(r for r in recon.rows if r.record_type_name == "rt_32005")
    assert rt_32005.key_columns == ["LN-NUM-ERT", "CONTACT-ID"]
    assert rt_32005.ignored_fields == ["CIF-REF-NUM-CUS", "CIF-ACT-COD-CUS"]


# ---------------------------------------------------------------------------
# 11. Cross-reference contract: OutputFileSpec.mapping_sheet resolves via
#     mapping_sheets dict. This is the EC-S4 lookup the emitter will rely on.
# ---------------------------------------------------------------------------


def test_mapping_sheet_lookup_via_outputfilespec():
    """Every flat OutputFileSpec.mapping_sheet resolves in mapping_sheets."""
    workbook = read_workbook(SHAW_WORKBOOK)

    # Find a flat (non-umbrella) output and assert its mapping sheet resolves.
    flat_outputs = [o for o in workbook.output_files if not o.is_multi_record]
    assert flat_outputs, "Expected at least one flat output in SHAW workbook."

    first_flat = flat_outputs[0]
    assert first_flat.mapping_sheet in workbook.mapping_sheets, (
        f"OutputFileSpec.mapping_sheet={first_flat.mapping_sheet!r} did not "
        f"resolve in mapping_sheets (keys: "
        f"{sorted(workbook.mapping_sheets.keys())[:5]}...)."
    )
    resolved = workbook.mapping_sheets[first_flat.mapping_sheet]
    assert resolved.sheet_name == first_flat.mapping_sheet
    assert resolved.rows, "Resolved mapping sheet must have at least one field row."

    # And the umbrella outputs MUST mark themselves as multi-record.
    umbrella_outputs = [o for o in workbook.output_files if o.is_multi_record]
    assert umbrella_outputs, "Expected umbrella outputs in SHAW workbook."
    for umbrella in umbrella_outputs:
        # multi_record_sheets is keyed by file_type, NOT mapping_sheet (the
        # cell carries the literal '(umbrella)' sentinel).
        assert umbrella.mapping_sheet == "(umbrella)"
        assert umbrella.file_type in workbook.multi_record_sheets


# ---------------------------------------------------------------------------
# 12. Bonus sanity: WorkbookReader().read() and read_workbook() are equivalent.
# ---------------------------------------------------------------------------


def test_class_and_function_apis_equivalent():
    """The class method and module-level function return the same result."""
    via_class = WorkbookReader().read(SHAW_WORKBOOK)
    via_func = read_workbook(SHAW_WORKBOOK)
    assert via_class.source == via_func.source
    assert len(via_class.input_files) == len(via_func.input_files)
    assert len(via_class.output_files) == len(via_func.output_files)


# ---------------------------------------------------------------------------
# 13. EC-S8 — CrossTypeRules_<FILETYPE> sheet parses into typed dataclasses.
# ---------------------------------------------------------------------------


def test_cross_type_rules_sheet_parses():
    """The SHAW workbook's ``CrossTypeRules_TRANERT`` sheet parses into
    ``workbook.cross_type_rules_sheets['TRANERT']`` with the expected row.

    Verifies EC-S8 acceptance criterion #4: the reader discovers
    ``CrossTypeRules_*`` sheets, parses them into ``CrossTypeRulesSheet``
    dataclasses, and keys them by file_type (the suffix). The fixture's
    single row carries the SHAW TRANERT ``header_trailer_count`` rule
    matching the committed umbrella overlay.
    """
    workbook = read_workbook(SHAW_WORKBOOK)
    assert "TRANERT" in workbook.cross_type_rules_sheets, (
        f"Expected CrossTypeRules_TRANERT to parse; got keys "
        f"{sorted(workbook.cross_type_rules_sheets)}"
    )
    # ATOCTRAN does not have a cross-type-rules overlay → no sheet → no key.
    assert "ATOCTRAN" not in workbook.cross_type_rules_sheets

    sheet = workbook.cross_type_rules_sheets["TRANERT"]
    assert sheet.file_type == "TRANERT"
    assert len(sheet.rows) == 1

    row = sheet.rows[0]
    assert row.rule_id == "CT001"
    assert row.check == "header_trailer_count"
    assert row.record_type == "batch_header"
    # DASH-style field preserved verbatim.
    assert row.trailer_field == "ITM-CNT-BRT"
    assert row.count_of == "detail"
    assert row.allow_empty_batch is True
    assert row.severity == "error"
    assert "ITM-CNT-BRT" in row.message
    # enabled defaults to True when the cell is truthy.
    assert row.enabled is True
    # Non-canonical optional columns left blank → empty extra dict.
    assert row.extra == {}


def test_cross_type_rules_sheet_blank_rule_id_row_skipped(tmp_path):
    """Rows whose ``rule_id`` cell is blank are skipped at read time.

    Mirrors the EC-S5 ``RulesRow`` convention so BAs can leave example
    template rows in place during draft authoring without polluting the
    parsed row list.
    """
    from src.onboarding.workbook_schema import (
        CROSS_TYPE_RULES_REQUIRED_COLUMNS,
    )

    wb_obj = Workbook()
    wb_obj.remove(wb_obj.active)

    # Source / InputFiles / OutputFiles minimal valid shape.
    ws = wb_obj.create_sheet("Source")
    _write_row(ws, 1, list(SOURCE_SHEET_REQUIRED_COLUMNS))
    _write_row(
        ws, 2, [_DEFAULT_SOURCE_DATA[col] for col in SOURCE_SHEET_REQUIRED_COLUMNS]
    )
    ws = wb_obj.create_sheet("InputFiles")
    _write_row(ws, 1, list(INPUT_FILES_REQUIRED_COLUMNS))
    ws = wb_obj.create_sheet("OutputFiles")
    _write_row(ws, 1, list(OUTPUT_FILES_REQUIRED_COLUMNS))

    # CrossTypeRules_TEST sheet: header + one blank-id row + one real row.
    ws = wb_obj.create_sheet("CrossTypeRules_TEST")
    _write_row(ws, 1, list(CROSS_TYPE_RULES_REQUIRED_COLUMNS))
    _write_row(ws, 2, ["", "header_trailer_count", "", "", "", "", "", ""])
    _write_row(
        ws,
        3,
        [
            "CT001",
            "header_trailer_count",
            "batch_header",
            "ITM-CNT",
            "detail",
            "true",
            "error",
            "msg",
        ],
    )

    out = tmp_path / "ctr_blank.xlsx"
    wb_obj.save(str(out))
    workbook = read_workbook(out)

    sheet = workbook.cross_type_rules_sheets["TEST"]
    assert len(sheet.rows) == 1
    assert sheet.rows[0].rule_id == "CT001"


def test_cross_type_rules_extra_columns_captured(tmp_path):
    """Non-canonical optional columns are captured into
    :attr:`CrossTypeRuleRow.extra` so non-``header_trailer_count`` rule
    types round-trip cleanly without a schema-version bump.
    """
    from src.onboarding.workbook_schema import (
        CROSS_TYPE_RULES_REQUIRED_COLUMNS,
    )

    wb_obj = Workbook()
    wb_obj.remove(wb_obj.active)
    ws = wb_obj.create_sheet("Source")
    _write_row(ws, 1, list(SOURCE_SHEET_REQUIRED_COLUMNS))
    _write_row(
        ws, 2, [_DEFAULT_SOURCE_DATA[col] for col in SOURCE_SHEET_REQUIRED_COLUMNS]
    )
    wb_obj.create_sheet("InputFiles").cell(row=1, column=1, value="file_type")
    ws_in = wb_obj["InputFiles"]
    _write_row(ws_in, 1, list(INPUT_FILES_REQUIRED_COLUMNS))
    ws_out = wb_obj.create_sheet("OutputFiles")
    _write_row(ws_out, 1, list(OUTPUT_FILES_REQUIRED_COLUMNS))

    # CrossTypeRules sheet with two extras (header_field, sum_of).
    ws = wb_obj.create_sheet("CrossTypeRules_TEST")
    cols = list(CROSS_TYPE_RULES_REQUIRED_COLUMNS) + [
        "header_field",
        "sum_of",
    ]
    _write_row(ws, 1, cols)
    _write_row(
        ws,
        2,
        [
            "CT001",
            "header_trailer_sum",
            "batch_header",
            "TOTAL",
            "detail",
            "false",
            "error",
            "totals mismatch",
            "HDR-TOTAL",
            "AMT_A|AMT_B|AMT_C",
        ],
    )

    out = tmp_path / "ctr_extras.xlsx"
    wb_obj.save(str(out))
    workbook = read_workbook(out)

    row = workbook.cross_type_rules_sheets["TEST"].rows[0]
    assert row.extra == {
        "header_field": "HDR-TOTAL",
        "sum_of": "AMT_A|AMT_B|AMT_C",
    }
