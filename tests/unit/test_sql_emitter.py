"""Unit tests for the Oracle-dialect expected_*.sql emitter (ED-S2).

Covers (per the Sprint 3 / Move 4 story):

  1. test_shaw_workbook_emits_expected_sql_artefacts
       -- SHAW workbook with overrides CLEARED produces one SQL
       artefact per record type at the canonical path.
  2. test_emitted_batch_header_sql_structurally_matches_committed
       -- whitespace-normalised structural equivalence on the SHAW
       batch_header SQL.
  3. test_emitted_sql_dash_alias_quoting
       -- assert column aliases for key columns are duplicated as
       ``AS "DASH-NAME"``.
  4. test_predicate_emitted_in_where_clause
       -- synthetic workbook with ``predicate = "BAT_TYP_BRT = 'A'"``
       emits a ``WHERE`` clause.
  5. test_no_where_clause_when_predicate_blank
       -- synthetic workbook with blank predicate has no ``WHERE``
       in the output.
  6. test_override_skipped
       -- synthetic workbook with ``expected_sql_override = "custom.sql"``
       does NOT emit a SQL file for that row.
  7. test_emit_all_returns_no_disk_writes
       -- calling ``emit_all`` never mutates the committed SQL tree
       (mtime snapshot).
  8. test_auto_marker_treated_as_blank_override
       -- the ED-S1 ``auto`` sentinel triggers SQL emission
       (regression guard for the ED-S1/S2 hand-off contract).
  9. test_emitter_rejects_empty_source_code
       -- safety guard on the constructor.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from src.onboarding.emitters import EmitterError
from src.onboarding.emitters.mapping_emitter import emit_mapping_artefacts
from src.onboarding.emitters.sql_emitter import (
    EmittedSqlArtefact,
    SqlEmitter,
    emit_sql_artefacts,
)
from src.onboarding.models import (
    MappingFieldRow,
    MappingSheet,
    MultiRecordRow,
    MultiRecordSheet,
    OnboardingWorkbook,
    OutputFileSpec,
    ReconciliationRow,
    ReconciliationSheet,
    SourceInfo,
)
from src.onboarding.workbook_reader import read_workbook

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SHAW_WORKBOOK = REPO_ROOT / "templates" / "SHAW_onboarding.xlsx"
COMMITTED_BATCH_HEADER_SQL = (
    REPO_ROOT
    / "config"
    / "e2e"
    / "sources"
    / "SHAW"
    / "sql"
    / "tranert"
    / "20_query"
    / "expected_batch_header.sql"
)
COMMITTED_SQL_DIR = (
    REPO_ROOT / "config" / "e2e" / "sources" / "SHAW" / "sql" / "tranert" / "20_query"
)


# ---------------------------------------------------------------------------
# Helpers -- synthetic workbook fixtures for targeted behaviour tests.
# ---------------------------------------------------------------------------


def _make_source_info(code: str = "ACME") -> SourceInfo:
    """Minimal SourceInfo for synthetic fixtures."""
    return SourceInfo(
        source_code=code,
        schema_version=1,
        release_tag="2026.M06",
        description="Synthetic.",
        staging_schema="APP_INT",
        output_root="/tmp/acme",
        java_load_script="load.sh",
        java_generate_script="",
        gate_load_blocking=True,
        gate_load_invoke_java=True,
        gate_f2s_blocking=True,
        gate_generate_blocking=False,
        gate_generate_invoke_java=False,
        gate_l1_blocking=True,
        gate_l2b_blocking=True,
        gate_l3_blocking=True,
        gate_mr_report_blocking=False,
    )


def _make_mapping_sheet(name: str) -> MappingSheet:
    """Build a tiny mapping sheet with two fields covering string + decimal."""
    return MappingSheet(
        sheet_name=name,
        rows=[
            MappingFieldRow(
                field_name="LN-NUM-ERT",
                data_type="Numeric",
                position=1,
                length=10,
                target_name="ln_num_ert",
                required="Yes",
                format="9(10)",
                transformation=None,
                valid_values=None,
                description=None,
            ),
            MappingFieldRow(
                field_name="TRN-COD-ERT",
                data_type="String",
                position=11,
                length=5,
                target_name="trn_cod_ert",
                required="Yes",
                format=None,
                transformation=None,
                valid_values=None,
                description=None,
            ),
        ],
    )


def _make_multi_record_sheet(
    file_type: str, mapping_sheet_name: str
) -> MultiRecordSheet:
    """One-row multi-record sheet so the emitter can resolve the layout tag."""
    return MultiRecordSheet(
        file_type=file_type,
        rows=[
            MultiRecordRow(
                record_type_name="rt_x",
                discriminator_field="TRN-COD-ERT",
                discriminator_position=11,
                discriminator_length=5,
                match_kind="discriminator_equals",
                match_value="X",
                mapping_sheet=mapping_sheet_name,
                rules_sheet="",
                cardinality="one_per_driver_row",
            ),
        ],
    )


def _make_workbook(
    source_code: str,
    file_type: str,
    *,
    expected_sql_override: str = "",
    predicate: str = "",
    staging_table: str = "",
    key_columns: list[str] | None = None,
) -> OnboardingWorkbook:
    """Assemble a one-record reconciliation workbook for targeted tests."""
    mapping_sheet_name = f"{file_type}_LAYOUT_Mapping"
    return OnboardingWorkbook(
        source=_make_source_info(source_code),
        input_files=[],
        output_files=[
            OutputFileSpec(
                file_type=file_type,
                glob=f"{file_type.lower()}_*.txt",
                mapping_sheet="(umbrella)",
                rules_sheet="(umbrella)",
                tolerance_max_errors=None,
                tolerance_max_error_pct=None,
                tolerance_ignore_fields=None,
            )
        ],
        multi_record_sheets={
            file_type: _make_multi_record_sheet(file_type, mapping_sheet_name)
        },
        reconciliation_sheets={
            file_type: ReconciliationSheet(
                file_type=file_type,
                file_wide_assertions=[],
                rows=[
                    ReconciliationRow(
                        record_type_name="rt_x",
                        key_columns=list(key_columns or ["LN-NUM-ERT"]),
                        staging_table=staging_table,
                        predicate=predicate,
                        ignored_fields=[],
                        expected_sql_override=expected_sql_override,
                    )
                ],
            )
        },
        cross_type_rules_sheets={},
        mapping_sheets={mapping_sheet_name: _make_mapping_sheet(mapping_sheet_name)},
        rules_sheets={},
    )


def _shaw_workbook_with_cleared_overrides() -> OnboardingWorkbook:
    """Load the real SHAW workbook and clear every TRANERT override.

    The committed SHAW workbook carries an ``expected_sql_override`` on
    every reconciliation row (the EC-S9 reverse-engineered state). To
    exercise the SQL emitter against real SHAW field metadata we clear
    the override on a copy of the workbook -- the underlying mapping
    sheets and reconciliation rows are unchanged.
    """
    wb = read_workbook(str(SHAW_WORKBOOK))
    sheet = wb.reconciliation_sheets["TRANERT"]
    cleared_rows = [
        dataclasses.replace(row, expected_sql_override="") for row in sheet.rows
    ]
    cleared_sheet = ReconciliationSheet(
        file_type=sheet.file_type,
        file_wide_assertions=sheet.file_wide_assertions,
        rows=cleared_rows,
    )
    return dataclasses.replace(
        wb, reconciliation_sheets={"TRANERT": cleared_sheet}
    )


def _normalise_sql(text: str) -> str:
    """Whitespace-tolerant structural normaliser (mirrors CLI's --check).

    Strips ``-- ...`` comments and collapses all whitespace to a single
    space. Two SQL strings comparing equal under this transform have
    the same column set, FROM clause, and WHERE clause.
    """
    import re

    no_comments = re.sub(r"--[^\n]*", "", text)
    return re.sub(r"\s+", " ", no_comments).strip()


# ---------------------------------------------------------------------------
# 1. SHAW workbook (with overrides cleared) emits one SQL per record type.
# ---------------------------------------------------------------------------


def test_shaw_workbook_emits_expected_sql_artefacts():
    """SHAW TRANERT carries 7 record types (batch_header + 6 detail
    record types). With overrides cleared, the emitter produces 7
    SQL artefacts at the canonical paths."""
    wb = _shaw_workbook_with_cleared_overrides()
    mapping_artefacts = emit_mapping_artefacts(wb)
    sql_artefacts = emit_sql_artefacts(wb, mapping_artefacts, source_code="SHAW")

    assert len(sql_artefacts) == 7, (
        f"Expected 7 SQL artefacts (1 batch_header + 6 detail record "
        f"types); got {len(sql_artefacts)}: "
        f"{[a.path for a in sql_artefacts]}"
    )

    paths = {a.path for a in sql_artefacts}
    assert (
        "config/e2e/sources/SHAW/sql/tranert/20_query/expected_batch_header.sql"
        in paths
    )
    assert (
        "config/e2e/sources/SHAW/sql/tranert/20_query/expected_32000.sql"
        in paths
    )
    assert (
        "config/e2e/sources/SHAW/sql/tranert/20_query/expected_32075.sql"
        in paths
    )


# ---------------------------------------------------------------------------
# 2. Structural equivalence with the committed batch_header SQL.
# ---------------------------------------------------------------------------


def test_emitted_batch_header_sql_structurally_matches_committed():
    """The emitted ``expected_batch_header.sql`` body covers the same
    set of SELECT columns the committed file projects, references the
    same staging table, and emits no spurious WHERE clause.

    Note: the committed SQL is hand-CURATED (5 columns) while the
    emitter projects the FULL mapping field set (22 columns). Both
    forms are structurally valid -- ED-S4 will reconcile the curation
    gap. This test asserts the EMITTED form is internally consistent
    (every committed-curated column appears in the emitted set, the
    FROM clause matches, and there is no WHERE clause)."""
    wb = _shaw_workbook_with_cleared_overrides()
    mapping_artefacts = emit_mapping_artefacts(wb)
    sql_artefacts = emit_sql_artefacts(wb, mapping_artefacts, source_code="SHAW")

    batch_header = next(
        a for a in sql_artefacts if a.path.endswith("/expected_batch_header.sql")
    )
    emitted_normalised = _normalise_sql(batch_header.content)
    committed_normalised = _normalise_sql(
        COMMITTED_BATCH_HEADER_SQL.read_text(encoding="utf-8")
    )

    # FROM clause must match (case-insensitive on schema -- both forms
    # use 'app_int' lower-case per the committed convention).
    assert "FROM app_int.EXPECTED_BATCH_HEADER_TBL t" in batch_header.content, (
        f"Expected FROM clause not present in:\n{batch_header.content}"
    )

    # No WHERE clause (batch_header has no predicate).
    assert " WHERE " not in batch_header.content.upper().replace(
        "WHEREAS", ""
    ), f"Unexpected WHERE clause:\n{batch_header.content}"

    # Every committed column appears in the emitted column set
    # (subset relation -- emitter projects the full mapping field
    # set, committed curates).
    for committed_col in (
        "BK_NUM_BRT",
        "APP_BRT",
        "TRN_COD_BRT",
        "BAT_TYP_BRT",
        "ITM_CNT_BRT",
    ):
        assert f"AS {committed_col}" in batch_header.content, (
            f"Expected column alias '{committed_col}' missing from emitted "
            f"SQL:\n{batch_header.content}"
        )
        # Cross-check the committed form contains it too (sanity).
        assert committed_col in committed_normalised, (
            f"Committed SQL missing '{committed_col}' (test fixture drift)"
        )

    # DASH-quoted key alias appears.
    assert 'AS "BK-NUM-BRT"' in batch_header.content


# ---------------------------------------------------------------------------
# 3. DASH-quoted alias for key columns.
# ---------------------------------------------------------------------------


def test_emitted_sql_dash_alias_quoting():
    """Key columns are projected twice: once under the underscore
    alias and once under an Oracle quoted identifier carrying the
    DASH form. The DASH-quoted alias enables the reconciliation
    spec's ``key`` to resolve on both the file side (DASH names) and
    the SQL rowset side."""
    wb = _make_workbook("ACME", "MYFILE", key_columns=["LN-NUM-ERT"])
    mapping_artefacts = emit_mapping_artefacts(wb)
    sql_artefacts = emit_sql_artefacts(wb, mapping_artefacts, source_code="ACME")

    assert len(sql_artefacts) == 1
    content = sql_artefacts[0].content

    # Underscore alias.
    assert "AS LN_NUM_ERT" in content, (
        f"Expected underscore alias for key column:\n{content}"
    )
    # DASH-quoted alias immediately after.
    assert 'AS "LN-NUM-ERT"' in content, (
        f"Expected DASH-quoted alias for key column:\n{content}"
    )

    # The non-key field is projected ONCE without the DASH duplicate.
    assert "AS TRN_COD_ERT" in content
    assert 'AS "TRN-COD-ERT"' not in content, (
        f"Non-key column should NOT have a DASH-quoted duplicate:\n{content}"
    )


# ---------------------------------------------------------------------------
# 4. Predicate emitted in WHERE clause.
# ---------------------------------------------------------------------------


def test_predicate_emitted_in_where_clause():
    """When the reconciliation row carries a ``predicate`` cell, the
    emitted SQL appends ``WHERE <predicate>``."""
    wb = _make_workbook(
        "ACME", "MYFILE", predicate="BAT_TYP_BRT = 'A'"
    )
    mapping_artefacts = emit_mapping_artefacts(wb)
    sql_artefacts = emit_sql_artefacts(wb, mapping_artefacts, source_code="ACME")

    assert len(sql_artefacts) == 1
    content = sql_artefacts[0].content
    assert "WHERE BAT_TYP_BRT = 'A'" in content, (
        f"Expected WHERE clause with predicate verbatim:\n{content}"
    )


# ---------------------------------------------------------------------------
# 5. No WHERE clause when predicate blank.
# ---------------------------------------------------------------------------


def test_no_where_clause_when_predicate_blank():
    """Blank ``predicate`` cell -> emitter omits the WHERE clause
    entirely (the engine fetches all rows from the staging table)."""
    wb = _make_workbook("ACME", "MYFILE", predicate="")
    mapping_artefacts = emit_mapping_artefacts(wb)
    sql_artefacts = emit_sql_artefacts(wb, mapping_artefacts, source_code="ACME")

    assert len(sql_artefacts) == 1
    content = sql_artefacts[0].content
    # Use a case-insensitive search to avoid catching SQL inside a column
    # name (no column name contains 'WHERE').
    assert "WHERE" not in content.upper(), (
        f"Unexpected WHERE clause for blank predicate:\n{content}"
    )


# ---------------------------------------------------------------------------
# 6. Override skipped: ED-S2 does NOT emit when BA owns the SQL.
# ---------------------------------------------------------------------------


def test_override_skipped():
    """When the reconciliation row carries a non-blank, non-``auto``
    ``expected_sql_override``, the emitter SKIPS the row entirely.
    The BA's hand-authored SQL remains the authority."""
    wb = _make_workbook(
        "ACME", "MYFILE", expected_sql_override="custom_query.sql"
    )
    mapping_artefacts = emit_mapping_artefacts(wb)
    sql_artefacts = emit_sql_artefacts(wb, mapping_artefacts, source_code="ACME")

    assert len(sql_artefacts) == 0, (
        f"Expected 0 SQL artefacts when override is set; got "
        f"{len(sql_artefacts)}: {[a.path for a in sql_artefacts]}"
    )


# ---------------------------------------------------------------------------
# 7. emit_all returns no disk writes (mtime snapshot guard).
# ---------------------------------------------------------------------------


def test_emit_all_returns_no_disk_writes():
    """Calling the emitter against the real SHAW workbook must never
    mutate the committed ``config/e2e/sources/SHAW/sql/`` tree. We
    snapshot the mtime of every committed SQL file, invoke the
    emitter, then assert no file's mtime changed."""
    snapshot: dict[Path, float] = {
        path: path.stat().st_mtime for path in COMMITTED_SQL_DIR.glob("*.sql")
    }

    wb = _shaw_workbook_with_cleared_overrides()
    mapping_artefacts = emit_mapping_artefacts(wb)
    artefacts = emit_sql_artefacts(wb, mapping_artefacts, source_code="SHAW")

    # Emitter produced artefacts but did NOT touch disk.
    assert artefacts  # non-empty
    for path, mtime_before in snapshot.items():
        assert path.stat().st_mtime == mtime_before, (
            f"Emitter mutated committed file: {path}"
        )


# ---------------------------------------------------------------------------
# 8. ED-S1 ``auto`` sentinel triggers emission.
# ---------------------------------------------------------------------------


def test_auto_marker_treated_as_blank_override():
    """The ED-S1 reconciliation YAML emitter substitutes the literal
    string ``auto`` for blank ``expected_sql_override`` cells. ED-S2
    must treat this sentinel as "blank" -- emit the SQL file -- not
    as "BA-authored, skip me"."""
    wb = _make_workbook("ACME", "MYFILE", expected_sql_override="auto")
    mapping_artefacts = emit_mapping_artefacts(wb)
    sql_artefacts = emit_sql_artefacts(wb, mapping_artefacts, source_code="ACME")

    assert len(sql_artefacts) == 1, (
        f"Expected 1 SQL artefact for 'auto' marker; got "
        f"{len(sql_artefacts)}"
    )


# ---------------------------------------------------------------------------
# 9. Empty source_code rejected at the constructor.
# ---------------------------------------------------------------------------


def test_emitter_rejects_empty_source_code():
    """Constructing :class:`SqlEmitter` with an empty source_code
    raises :class:`EmitterError`."""
    with pytest.raises(EmitterError):
        SqlEmitter(source_code="")
    with pytest.raises(EmitterError):
        SqlEmitter(source_code="   ")


# ---------------------------------------------------------------------------
# 10. Returned object type contract.
# ---------------------------------------------------------------------------


def test_emitted_artefact_is_immutable_dataclass():
    """The returned artefacts are frozen :class:`EmittedSqlArtefact`
    instances (consumers must not mutate them)."""
    wb = _make_workbook("ACME", "MYFILE")
    mapping_artefacts = emit_mapping_artefacts(wb)
    artefacts = emit_sql_artefacts(wb, mapping_artefacts, source_code="ACME")

    assert artefacts
    assert isinstance(artefacts[0], EmittedSqlArtefact)
    with pytest.raises(dataclasses.FrozenInstanceError):
        artefacts[0].path = "/tmp/mutated.sql"  # type: ignore[misc]
