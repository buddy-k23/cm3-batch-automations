"""Onboarding workbook reader (EC-S2).

Parse a BA-authored onboarding Excel workbook (validated against the
EC-S1 schema in :mod:`src.onboarding.workbook_schema`) into a typed
:class:`~src.onboarding.models.OnboardingWorkbook` dataclass tree.

Public API:

    WorkbookReader().read(path) -> OnboardingWorkbook
    read_workbook(path) -> OnboardingWorkbook   # convenience

Design notes:

    * Schema validation is delegated to EC-S1 — the reader calls
      :func:`~src.onboarding.workbook_schema.assert_workbook_valid` first
      and re-raises ``WorkbookSchemaError`` unchanged. Read errors
      (bad cell values in an otherwise schema-valid workbook) raise
      ``WorkbookReadError`` from :mod:`src.onboarding.models`.
    * Column lookups are case-insensitive + whitespace-stripped (matches
      the EC-S1 validator's convention so BA-side typos like
      ``Field name`` vs ``Field Name`` are tolerated).
    * Cells are opened with ``data_only=True`` so formula cells return
      their cached value, not the formula text.
    * Pipe-separated cells (``|``) are split here so emitters never
      reparse. ``"A|B|C"`` -> ``["A", "B", "C"]``; blank cell -> ``[]``.
    * Blank optional cells project to ``None`` for typed-int/float
      fields and ``""`` for string fields where empty-string is a
      semantically meaningful default (description, predicate).
    * Bool cells accept ``true``/``false``/``yes``/``no``/``1``/``0``
      case-insensitively. Anything else raises ``WorkbookReadError``.
    * Sheet-name shortening (``~`` marker for names > 31 chars, per
      EC-S1) needs no special handling here: the reader stores the
      exact sheet name string as the key and downstream emitters look
      up by the exact name written in the ``mapping_sheet`` /
      ``rules_sheet`` cells (which the BA wrote to match).
    * DASH-style field names (``LN-NUM-ERT``) are preserved verbatim
      end-to-end. The reader never normalises to snake_case.

The full grammar lives in
``templates/source_onboarding_template_README.md``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from src.onboarding.models import (
    CrossTypeRuleRow,
    CrossTypeRulesSheet,
    InputFileSpec,
    MappingFieldRow,
    MappingSheet,
    MultiRecordRow,
    MultiRecordSheet,
    OnboardingWorkbook,
    OutputFileSpec,
    ReconciliationRow,
    ReconciliationSheet,
    RulesRow,
    RulesSheet,
    SourceInfo,
    WorkbookReadError,
)
from src.onboarding.workbook_schema import (
    CROSS_TYPE_RULES_REQUIRED_COLUMNS,
    DYNAMIC_SHEET_PREFIXES,
    assert_workbook_valid,
)

# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------

_BOOL_TRUE = frozenset({"true", "yes", "y", "1"})
_BOOL_FALSE = frozenset({"false", "no", "n", "0"})

_VALID_MATCH_KINDS = frozenset(
    {"position_first", "discriminator_equals", "discriminator_in"}
)
_VALID_CARDINALITIES = frozenset(
    {"one_per_driver_row", "many_per_driver_row", "zero_or_one_per_driver_row"}
)
_VALID_EXPECTED_TABLE_STRATEGIES = frozenset(
    {"view", "ctas", "ctas_with_drop"}
)
"""ED-S3: allowed values for the Source sheet's optional
``expected_table_strategy`` column. Blank cell / missing column defaults
to ``view`` (the ED-S2 SELECT-only behaviour)."""


# ---------------------------------------------------------------------------
# Cell normalisation helpers.
# ---------------------------------------------------------------------------


def _normalize_header(value: object) -> str:
    """Lowercase + strip a header cell value for case-insensitive lookup.

    Matches the convention used by ``workbook_schema._normalize_header`` so
    the reader and validator share the exact same column-matching contract.
    """
    if value is None:
        return ""
    return str(value).strip().lower()


def _cell_str_or_empty(value: object) -> str:
    """Return cell value as stripped string; ``""`` for ``None``/empty."""
    if value is None:
        return ""
    text = str(value).strip()
    return text


def _cell_str_or_none(value: object) -> str | None:
    """Return cell value as stripped string; ``None`` for ``None``/empty."""
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _cell_int_or_none(value: object, *, sheet: str, cell: str, column: str) -> int | None:
    """Coerce a cell value to ``int`` or return ``None`` for blank.

    Args:
        value: Raw cell value from openpyxl.
        sheet: Sheet name (for error reporting).
        cell: Excel cell address (for error reporting).
        column: Column name (for error reporting).

    Raises:
        WorkbookReadError: If the cell is non-blank and not coercible to int.
    """
    if value is None:
        return None
    if isinstance(value, bool):  # bool is an int subclass — reject explicitly
        raise WorkbookReadError(
            f"{sheet}!{cell}: column '{column}' expected an integer but found "
            f"a boolean value '{value}'."
        )
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise WorkbookReadError(
            f"{sheet}!{cell}: column '{column}' expected an integer but found "
            f"the non-integer float '{value}'."
        )
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise WorkbookReadError(
            f"{sheet}!{cell}: column '{column}' expected an integer but found "
            f"'{text}'."
        ) from exc


def _cell_float_or_none(
    value: object, *, sheet: str, cell: str, column: str
) -> float | None:
    """Coerce a cell value to ``float`` or return ``None`` for blank.

    Raises:
        WorkbookReadError: If the cell is non-blank and not coercible to float.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise WorkbookReadError(
            f"{sheet}!{cell}: column '{column}' expected a numeric value but "
            f"found a boolean '{value}'."
        )
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise WorkbookReadError(
            f"{sheet}!{cell}: column '{column}' expected a numeric value but "
            f"found '{text}'."
        ) from exc


def _cell_bool(value: object, *, sheet: str, cell: str, column: str) -> bool:
    """Coerce a cell value to ``bool``.

    Accepts ``true``/``false``/``yes``/``no``/``y``/``n``/``1``/``0`` case-
    insensitively. Also accepts native Python ``bool`` (openpyxl returns
    ``True``/``False`` for ``=TRUE()`` formula cells).

    Raises:
        WorkbookReadError: If the value is blank or not in the accepted set.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        raise WorkbookReadError(
            f"{sheet}!{cell}: column '{column}' is blank — expected one of "
            f"true/false/yes/no/1/0."
        )
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
        raise WorkbookReadError(
            f"{sheet}!{cell}: column '{column}' expected a boolean but found "
            f"numeric '{value}'. Use true/false/yes/no/1/0."
        )
    text = str(value).strip().lower()
    if text in _BOOL_TRUE:
        return True
    if text in _BOOL_FALSE:
        return False
    raise WorkbookReadError(
        f"{sheet}!{cell}: column '{column}' has unrecognised boolean value "
        f"'{value}'. Expected one of true/false/yes/no/1/0 (case-insensitive)."
    )


def _cell_bool_or_default(
    value: object,
    *,
    default: bool,
    sheet: str,
    cell: str,
    column: str,
) -> bool:
    """Bool variant that returns ``default`` for blank cells (EC-S8).

    The strict :func:`_cell_bool` raises on blank cells (used by ``Source``
    sheet gate columns where a missing value is unambiguously a BA mistake).
    Cross-type-rules columns like ``allow_empty_batch`` / ``enabled`` are
    "optional flags" where blank means "use the engine default" -- this
    helper preserves that intent.

    Args:
        value: Raw cell value from openpyxl.
        default: Value to return when the cell is blank.
        sheet: Sheet name (for error reporting).
        cell: Excel cell address (for error reporting).
        column: Column name (for error reporting).

    Raises:
        WorkbookReadError: If the cell is non-blank and not coercible.
    """
    if value is None:
        return default
    if isinstance(value, str) and not value.strip():
        return default
    return _cell_bool(value, sheet=sheet, cell=cell, column=column)


def _split_pipe_list(value: object) -> list[str]:
    """Split a pipe-separated cell into a list, stripping whitespace.

    Blank/``None`` cells return ``[]``. Trailing/leading empty segments
    are dropped (so ``"A||B"`` returns ``["A", "B"]``).
    """
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    return [part.strip() for part in text.split("|") if part.strip()]


# ---------------------------------------------------------------------------
# Header-row + row-iteration helpers.
# ---------------------------------------------------------------------------


def _read_header_index(ws: Worksheet) -> dict[str, int]:
    """Return a ``{normalized_header: 1-indexed_column}`` map from row 1.

    The reader uses 1-indexed columns to match openpyxl's column model and
    so error messages reference the same column letter the BA sees.
    """
    headers: dict[str, int] = {}
    # ``read_only=True`` worksheets only support iter_rows; row indexing
    # via ``ws[1]`` would force a full load. Pull header row via iter_rows.
    for row in ws.iter_rows(min_row=1, max_row=1, values_only=False):
        for cell in row:
            normalized = _normalize_header(cell.value)
            if normalized:
                headers[normalized] = cell.column
        break
    return headers


def _column_value(
    row: tuple[Any, ...],
    header_index: dict[str, int],
    column: str,
) -> Any:
    """Look up a column's raw cell value from a ``values_only`` row tuple.

    Args:
        row: The row tuple (0-indexed against openpyxl's columns).
        header_index: ``_read_header_index`` output (1-indexed).
        column: Column name (case-insensitive match against header_index).

    Returns:
        The raw cell value, or ``None`` if the column was not found
        (which can only happen if the EC-S1 validator missed it — the
        reader treats absent columns as blank rather than erroring,
        since the validator is the source of truth for shape).
    """
    col_idx = header_index.get(column.lower())
    if col_idx is None:
        return None
    # Convert 1-indexed openpyxl column to 0-indexed tuple position.
    idx = col_idx - 1
    if idx >= len(row):
        return None
    return row[idx]


def _column_address(
    header_index: dict[str, int], column: str, row_number: int
) -> str:
    """Return the Excel cell address (``E4``) for a column on a given row.

    Used in ``WorkbookReadError`` messages so the BA can jump straight to
    the offending cell. If the column is not in the header index the
    address falls back to the sheet-level ``"<column>?"`` token.
    """
    col_idx = header_index.get(column.lower())
    if col_idx is None:
        return f"<{column}?>"
    return f"{get_column_letter(col_idx)}{row_number}"


# ---------------------------------------------------------------------------
# Per-sheet parsers.
# ---------------------------------------------------------------------------


def _parse_source_sheet(ws: Worksheet) -> SourceInfo:
    """Parse the single data row of the ``Source`` sheet into ``SourceInfo``."""
    headers = _read_header_index(ws)
    data_row = None
    data_row_number = 0
    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if any(cell is not None and str(cell).strip() for cell in row):
            data_row = row
            data_row_number = row_idx
            break
    if data_row is None:
        raise WorkbookReadError(
            "Source!A2: Source sheet has no data row. The Source sheet must "
            "carry exactly one row of source-wide configuration below the header."
        )

    def _str_or_empty(col: str) -> str:
        return _cell_str_or_empty(_column_value(data_row, headers, col))

    def _str_required(col: str) -> str:
        text = _str_or_empty(col)
        if not text:
            addr = _column_address(headers, col, data_row_number)
            raise WorkbookReadError(
                f"Source!{addr}: required column '{col}' is blank."
            )
        return text

    def _int_required(col: str) -> int:
        addr = _column_address(headers, col, data_row_number)
        value = _cell_int_or_none(
            _column_value(data_row, headers, col),
            sheet="Source",
            cell=addr,
            column=col,
        )
        if value is None:
            raise WorkbookReadError(
                f"Source!{addr}: required column '{col}' is blank — expected an integer."
            )
        return value

    def _bool_col(col: str) -> bool:
        addr = _column_address(headers, col, data_row_number)
        return _cell_bool(
            _column_value(data_row, headers, col),
            sheet="Source",
            cell=addr,
            column=col,
        )

    # ED-S3: optional ``expected_table_strategy`` column. Missing column
    # OR blank cell -> default to "view" (backward-compatible with ED-S2
    # workbooks that pre-date ED-S3). Non-blank cells are validated against
    # the allowed set so a BA typo fails fast at read time rather than
    # silently producing pure SELECT output when CTAS was intended.
    strategy_raw = _str_or_empty("expected_table_strategy")
    if strategy_raw:
        strategy = strategy_raw.lower()
        if strategy not in _VALID_EXPECTED_TABLE_STRATEGIES:
            addr = _column_address(
                headers, "expected_table_strategy", data_row_number
            )
            raise WorkbookReadError(
                f"Source!{addr}: expected_table_strategy "
                f"'{strategy_raw}' is not recognised. Expected one of: "
                f"{', '.join(sorted(_VALID_EXPECTED_TABLE_STRATEGIES))} "
                f"(case-insensitive)."
            )
    else:
        strategy = "view"

    return SourceInfo(
        source_code=_str_required("source_code"),
        schema_version=_int_required("schema_version"),
        release_tag=_str_required("release_tag"),
        description=_str_or_empty("description"),
        staging_schema=_str_required("staging_schema"),
        output_root=_str_required("output_root"),
        java_load_script=_str_required("java_load_script"),
        java_generate_script=_str_or_empty("java_generate_script"),
        gate_load_blocking=_bool_col("gate_load_blocking"),
        gate_load_invoke_java=_bool_col("gate_load_invoke_java"),
        gate_f2s_blocking=_bool_col("gate_f2s_blocking"),
        gate_generate_blocking=_bool_col("gate_generate_blocking"),
        gate_generate_invoke_java=_bool_col("gate_generate_invoke_java"),
        gate_l1_blocking=_bool_col("gate_l1_blocking"),
        gate_l2b_blocking=_bool_col("gate_l2b_blocking"),
        gate_l3_blocking=_bool_col("gate_l3_blocking"),
        gate_mr_report_blocking=_bool_col("gate_mr_report_blocking"),
        expected_table_strategy=strategy,  # type: ignore[arg-type]
    )


def _iter_data_rows(ws: Worksheet) -> Iterable[tuple[int, tuple[Any, ...]]]:
    """Yield ``(row_number, row_tuple)`` for every non-empty row after the header.

    A row is considered empty when every cell is ``None`` or whitespace.
    """
    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if any(cell is not None and str(cell).strip() for cell in row):
            yield row_idx, row


def _parse_input_files(ws: Worksheet) -> list[InputFileSpec]:
    """Parse every data row of the ``InputFiles`` sheet."""
    headers = _read_header_index(ws)
    specs: list[InputFileSpec] = []
    for row_number, row in _iter_data_rows(ws):
        max_errors_addr = _column_address(headers, "thresholds_max_errors", row_number)
        specs.append(
            InputFileSpec(
                file_type=_cell_str_or_empty(_column_value(row, headers, "file_type")),
                glob=_cell_str_or_empty(_column_value(row, headers, "glob")),
                mapping_sheet=_cell_str_or_empty(
                    _column_value(row, headers, "mapping_sheet")
                ),
                target_staging_table=_cell_str_or_empty(
                    _column_value(row, headers, "target_staging_table")
                ),
                thresholds_max_errors=_cell_int_or_none(
                    _column_value(row, headers, "thresholds_max_errors"),
                    sheet="InputFiles",
                    cell=max_errors_addr,
                    column="thresholds_max_errors",
                ),
            )
        )
    return specs


def _parse_output_files(ws: Worksheet) -> list[OutputFileSpec]:
    """Parse every data row of the ``OutputFiles`` sheet."""
    headers = _read_header_index(ws)
    specs: list[OutputFileSpec] = []
    for row_number, row in _iter_data_rows(ws):
        max_errors_addr = _column_address(headers, "tolerance_max_errors", row_number)
        max_pct_addr = _column_address(headers, "tolerance_max_error_pct", row_number)
        ignore_raw = _column_value(row, headers, "tolerance_ignore_fields")
        ignore_fields = _split_pipe_list(ignore_raw) if ignore_raw else None
        specs.append(
            OutputFileSpec(
                file_type=_cell_str_or_empty(_column_value(row, headers, "file_type")),
                glob=_cell_str_or_empty(_column_value(row, headers, "glob")),
                mapping_sheet=_cell_str_or_empty(
                    _column_value(row, headers, "mapping_sheet")
                ),
                rules_sheet=_cell_str_or_empty(
                    _column_value(row, headers, "rules_sheet")
                ),
                tolerance_max_errors=_cell_int_or_none(
                    _column_value(row, headers, "tolerance_max_errors"),
                    sheet="OutputFiles",
                    cell=max_errors_addr,
                    column="tolerance_max_errors",
                ),
                tolerance_max_error_pct=_cell_float_or_none(
                    _column_value(row, headers, "tolerance_max_error_pct"),
                    sheet="OutputFiles",
                    cell=max_pct_addr,
                    column="tolerance_max_error_pct",
                ),
                tolerance_ignore_fields=ignore_fields,
            )
        )
    return specs


def _parse_multi_record_sheet(ws: Worksheet, file_type: str) -> MultiRecordSheet:
    """Parse a ``MultiRecord_<FILETYPE>`` sheet."""
    headers = _read_header_index(ws)
    rows: list[MultiRecordRow] = []
    sheet_name = ws.title
    for row_number, row in _iter_data_rows(ws):
        pos_addr = _column_address(headers, "discriminator_position", row_number)
        len_addr = _column_address(headers, "discriminator_length", row_number)

        match_kind_raw = _cell_str_or_empty(_column_value(row, headers, "match_kind"))
        match_kind = match_kind_raw.lower()
        if match_kind not in _VALID_MATCH_KINDS:
            addr = _column_address(headers, "match_kind", row_number)
            raise WorkbookReadError(
                f"{sheet_name}!{addr}: match_kind '{match_kind_raw}' is not "
                f"recognised. Expected one of: "
                f"{', '.join(sorted(_VALID_MATCH_KINDS))}."
            )

        cardinality_raw = _cell_str_or_empty(_column_value(row, headers, "cardinality"))
        cardinality = cardinality_raw.lower()
        if cardinality not in _VALID_CARDINALITIES:
            addr = _column_address(headers, "cardinality", row_number)
            raise WorkbookReadError(
                f"{sheet_name}!{addr}: cardinality '{cardinality_raw}' is not "
                f"recognised. Expected one of: "
                f"{', '.join(sorted(_VALID_CARDINALITIES))}."
            )

        pos_value = _cell_int_or_none(
            _column_value(row, headers, "discriminator_position"),
            sheet=sheet_name,
            cell=pos_addr,
            column="discriminator_position",
        )
        len_value = _cell_int_or_none(
            _column_value(row, headers, "discriminator_length"),
            sheet=sheet_name,
            cell=len_addr,
            column="discriminator_length",
        )
        if pos_value is None:
            raise WorkbookReadError(
                f"{sheet_name}!{pos_addr}: discriminator_position is required."
            )
        if len_value is None:
            raise WorkbookReadError(
                f"{sheet_name}!{len_addr}: discriminator_length is required."
            )

        rows.append(
            MultiRecordRow(
                record_type_name=_cell_str_or_empty(
                    _column_value(row, headers, "record_type_name")
                ),
                discriminator_field=_cell_str_or_empty(
                    _column_value(row, headers, "discriminator_field")
                ),
                discriminator_position=pos_value,
                discriminator_length=len_value,
                match_kind=match_kind,  # type: ignore[arg-type]
                match_value=_cell_str_or_empty(
                    _column_value(row, headers, "match_value")
                ),
                mapping_sheet=_cell_str_or_empty(
                    _column_value(row, headers, "mapping_sheet")
                ),
                rules_sheet=_cell_str_or_empty(
                    _column_value(row, headers, "rules_sheet")
                ),
                cardinality=cardinality,  # type: ignore[arg-type]
            )
        )
    return MultiRecordSheet(file_type=file_type, rows=rows)


def _parse_reconciliation_sheet(
    ws: Worksheet, file_type: str
) -> ReconciliationSheet:
    """Parse a ``Reconciliation_<FILETYPE>`` sheet.

    The ``assertions`` cell on the FIRST data row is lifted to the sheet-
    level ``file_wide_assertions`` (per the EC-S1 convention) and NOT
    repeated on each row dataclass.
    """
    headers = _read_header_index(ws)
    rows: list[ReconciliationRow] = []
    file_wide_assertions: list[str] = []
    first_row_seen = False
    for _row_number, row in _iter_data_rows(ws):
        if not first_row_seen:
            file_wide_assertions = _split_pipe_list(
                _column_value(row, headers, "assertions")
            )
            first_row_seen = True
        rows.append(
            ReconciliationRow(
                record_type_name=_cell_str_or_empty(
                    _column_value(row, headers, "record_type_name")
                ),
                key_columns=_split_pipe_list(
                    _column_value(row, headers, "key_columns")
                ),
                staging_table=_cell_str_or_empty(
                    _column_value(row, headers, "staging_table")
                ),
                predicate=_cell_str_or_empty(
                    _column_value(row, headers, "predicate")
                ),
                ignored_fields=_split_pipe_list(
                    _column_value(row, headers, "ignored_fields")
                ),
                expected_sql_override=_cell_str_or_empty(
                    _column_value(row, headers, "expected_sql_override")
                ),
                cardinality_override=_cell_str_or_empty(
                    _column_value(row, headers, "cardinality")
                ),
            )
        )
    return ReconciliationSheet(
        file_type=file_type,
        file_wide_assertions=file_wide_assertions,
        rows=rows,
    )


def _parse_cross_type_rules_sheet(
    ws: Worksheet, file_type: str
) -> CrossTypeRulesSheet:
    """Parse a ``CrossTypeRules_<FILETYPE>`` sheet (EC-S8).

    Rows with a blank ``rule_id`` are skipped (matches the EC-S5
    :class:`RulesRow` convention). Optional columns beyond the canonical
    set (``CROSS_TYPE_RULES_REQUIRED_COLUMNS``) are captured in
    :attr:`CrossTypeRuleRow.extra` so the EC-S4 mapping emitter can pass
    them through to the umbrella YAML without the workbook needing a
    schema-version bump every time a new rule-type field is exercised.
    """
    headers = _read_header_index(ws)
    sheet_name = ws.title
    # ``enabled`` is captured into its own bool field on the dataclass
    # (defaults to True when blank), NOT into ``extra`` — it is a
    # workbook-only soft toggle the emitter consumes to skip disabled
    # rows. Adding it to ``canonical`` here keeps it out of ``extra``.
    canonical = set(CROSS_TYPE_RULES_REQUIRED_COLUMNS) | {"enabled"}
    rows: list[CrossTypeRuleRow] = []
    for row_number, row in _iter_data_rows(ws):
        rule_id = _cell_str_or_empty(_column_value(row, headers, "rule_id"))
        if not rule_id:
            # Match the EC-S5 RulesRow convention: blank rule_id => skip.
            continue

        allow_empty_addr = _column_address(
            headers, "allow_empty_batch", row_number
        )
        enabled_addr = _column_address(headers, "enabled", row_number)

        allow_empty_batch = _cell_bool_or_default(
            _column_value(row, headers, "allow_empty_batch"),
            default=False,
            sheet=sheet_name,
            cell=allow_empty_addr,
            column="allow_empty_batch",
        )
        enabled = _cell_bool_or_default(
            _column_value(row, headers, "enabled"),
            default=True,
            sheet=sheet_name,
            cell=enabled_addr,
            column="enabled",
        )

        # Capture any non-canonical columns the BA added (e.g. header_field,
        # detail_field, sum_field, sum_of, when_type, requires_type,
        # expected_order, exactly). Blank cells are NOT included so the
        # emitter's "is-set" check uses dict membership rather than
        # falsy-value checks.
        extra: dict[str, str] = {}
        for header_norm, col_idx in headers.items():
            if header_norm in canonical or header_norm == "rule_id":
                continue
            raw = _column_value(row, headers, header_norm)
            text = _cell_str_or_empty(raw)
            if text:
                extra[header_norm] = text

        rows.append(
            CrossTypeRuleRow(
                rule_id=rule_id,
                check=_cell_str_or_empty(_column_value(row, headers, "check")),
                record_type=_cell_str_or_empty(
                    _column_value(row, headers, "record_type")
                ),
                trailer_field=_cell_str_or_empty(
                    _column_value(row, headers, "trailer_field")
                ),
                count_of=_cell_str_or_empty(
                    _column_value(row, headers, "count_of")
                ),
                allow_empty_batch=allow_empty_batch,
                severity=_cell_str_or_empty(
                    _column_value(row, headers, "severity")
                ),
                message=_cell_str_or_empty(_column_value(row, headers, "message")),
                enabled=enabled,
                extra=extra,
            )
        )
    return CrossTypeRulesSheet(file_type=file_type, rows=rows)


def _parse_mapping_sheet(ws: Worksheet) -> MappingSheet:
    """Parse a ``*_Mapping`` sheet into :class:`MappingSheet`.

    Cells are preserved as raw strings (or ``None`` for blank). The
    EC-S4 emitter applies the type coercion / valid-values parsing in
    one centralised pass via the existing
    :class:`src.config.template_converter.TemplateConverter`.

    ED-S4 (Sprint 4 / Move 4) — the optional ``Reconciliation`` BOOLEAN
    column flags whether the field is in scope for the reconciliation
    YAML's ``record_types.<name>.fields[]`` and the SQL emitter's
    SELECT column list. Missing column OR blank cell -> ``False`` so
    pre-ED-S4 workbooks preserve the legacy "emit all fields" behaviour
    via the emitter's opt-in convention.
    """
    headers = _read_header_index(ws)
    rows: list[MappingFieldRow] = []
    sheet_name = ws.title
    for row_number, row in _iter_data_rows(ws):
        pos_addr = _column_address(headers, "position", row_number)
        len_addr = _column_address(headers, "length", row_number)
        recon_addr = _column_address(headers, "reconciliation", row_number)
        reconciliation = _cell_bool_or_default(
            _column_value(row, headers, "reconciliation"),
            default=False,
            sheet=sheet_name,
            cell=recon_addr,
            column="Reconciliation",
        )
        recon_order_addr = _column_address(
            headers, "reconciliation order", row_number
        )
        recon_order_value = _cell_int_or_none(
            _column_value(row, headers, "reconciliation order"),
            sheet=sheet_name,
            cell=recon_order_addr,
            column="Reconciliation Order",
        )
        reconciliation_order = recon_order_value if recon_order_value else 0
        reconciliation_column = _cell_str_or_empty(
            _column_value(row, headers, "reconciliation column")
        )
        reconciliation_predicate = _cell_str_or_empty(
            _column_value(row, headers, "reconciliation predicate")
        )
        reconciliation_sql_expression = _cell_str_or_empty(
            _column_value(row, headers, "reconciliation sql expression")
        )
        rows.append(
            MappingFieldRow(
                field_name=_cell_str_or_empty(
                    _column_value(row, headers, "field name")
                ),
                data_type=_cell_str_or_empty(
                    _column_value(row, headers, "data type")
                ),
                position=_cell_int_or_none(
                    _column_value(row, headers, "position"),
                    sheet=sheet_name,
                    cell=pos_addr,
                    column="Position",
                ),
                length=_cell_int_or_none(
                    _column_value(row, headers, "length"),
                    sheet=sheet_name,
                    cell=len_addr,
                    column="Length",
                ),
                target_name=_cell_str_or_none(
                    _column_value(row, headers, "target name")
                ),
                required=_cell_str_or_none(
                    _column_value(row, headers, "required")
                ),
                format=_cell_str_or_none(_column_value(row, headers, "format")),
                transformation=_cell_str_or_none(
                    _column_value(row, headers, "transformation")
                ),
                valid_values=_cell_str_or_none(
                    _column_value(row, headers, "valid values")
                ),
                description=_cell_str_or_none(
                    _column_value(row, headers, "description")
                ),
                reconciliation=reconciliation,
                reconciliation_order=reconciliation_order,
                reconciliation_column=reconciliation_column,
                reconciliation_predicate=reconciliation_predicate,
                reconciliation_sql_expression=reconciliation_sql_expression,
            )
        )
    return MappingSheet(sheet_name=sheet_name, rows=rows)


def _parse_rules_sheet(ws: Worksheet) -> RulesSheet:
    """Parse a ``*_Rules`` sheet into :class:`RulesSheet`.

    Rows with a blank ``Rule ID`` are skipped, matching the existing
    :class:`src.config.ba_rules_template_converter.BARulesTemplateConverter`
    convention.
    """
    headers = _read_header_index(ws)
    rows: list[RulesRow] = []
    sheet_name = ws.title
    for _row_number, row in _iter_data_rows(ws):
        rule_id = _cell_str_or_empty(_column_value(row, headers, "rule id"))
        if not rule_id:
            continue
        rows.append(
            RulesRow(
                rule_id=rule_id,
                rule_name=_cell_str_or_none(
                    _column_value(row, headers, "rule name")
                ),
                field=_cell_str_or_empty(_column_value(row, headers, "field")),
                rule_type=_cell_str_or_empty(
                    _column_value(row, headers, "rule type")
                ),
                severity=_cell_str_or_none(
                    _column_value(row, headers, "severity")
                ),
                enabled=_cell_str_or_none(_column_value(row, headers, "enabled")),
                message=_cell_str_or_none(_column_value(row, headers, "message")),
                expected_values=_cell_str_or_none(
                    _column_value(row, headers, "expected / values")
                ),
                condition=_cell_str_or_none(
                    _column_value(row, headers, "condition (optional)")
                ),
                notes=_cell_str_or_none(_column_value(row, headers, "notes")),
            )
        )
    return RulesSheet(sheet_name=sheet_name, rows=rows)


# ---------------------------------------------------------------------------
# Public reader class + module-level convenience function.
# ---------------------------------------------------------------------------


class WorkbookReader:
    """Read a BA onboarding workbook into a typed dataclass tree.

    Usage:

        >>> reader = WorkbookReader()
        >>> wb = reader.read(Path("templates/SHAW_onboarding.xlsx"))
        >>> wb.source.source_code
        'SHAW'

    The reader is stateless across calls (no instance attributes). The
    class form exists so callers can swap in subclasses for testing
    (e.g. injecting a fake schema validator) without monkey-patching
    module-level functions.
    """

    def read(self, path: Path | str) -> OnboardingWorkbook:
        """Read the workbook at ``path`` into an :class:`OnboardingWorkbook`.

        Args:
            path: Filesystem path to the ``.xlsx`` workbook.

        Returns:
            The fully-parsed :class:`OnboardingWorkbook` dataclass tree.

        Raises:
            WorkbookSchemaError: If the EC-S1 schema validator reports
                any error-level findings. Re-raised unchanged.
            WorkbookReadError: If schema is valid but a cell value
                cannot be coerced (bad bool, malformed int, unrecognised
                ``match_kind`` / ``cardinality``).
        """
        path = Path(path)
        # 1. Delegate schema validation. Raises WorkbookSchemaError if any
        #    error-level findings are present.
        assert_workbook_valid(path)

        # 2. Open the workbook (read-only, formulas resolved to values).
        wb = load_workbook(filename=str(path), read_only=True, data_only=True)
        try:
            source = _parse_source_sheet(wb["Source"])
            input_files = _parse_input_files(wb["InputFiles"])
            output_files = _parse_output_files(wb["OutputFiles"])

            multi_record_sheets: dict[str, MultiRecordSheet] = {}
            reconciliation_sheets: dict[str, ReconciliationSheet] = {}
            cross_type_rules_sheets: dict[str, CrossTypeRulesSheet] = {}
            mapping_sheets: dict[str, MappingSheet] = {}
            rules_sheets: dict[str, RulesSheet] = {}

            # The DYNAMIC_SHEET_PREFIXES tuple order is significant: the
            # CrossTypeRules_ prefix MUST be checked before any future
            # prefix that could share a common stem. The lookup is
            # explicit-by-name (not positional) to make this robust.
            mr_prefix = "MultiRecord_"
            recon_prefix = "Reconciliation_"
            ctr_prefix = "CrossTypeRules_"

            for sheet_name in wb.sheetnames:
                if sheet_name in {"Source", "InputFiles", "OutputFiles"}:
                    continue
                ws = wb[sheet_name]
                # MultiRecord_<FILETYPE>
                if sheet_name.startswith(mr_prefix):
                    file_type = sheet_name[len(mr_prefix):]
                    multi_record_sheets[file_type] = _parse_multi_record_sheet(
                        ws, file_type
                    )
                    continue
                # Reconciliation_<FILETYPE>
                if sheet_name.startswith(recon_prefix):
                    file_type = sheet_name[len(recon_prefix):]
                    reconciliation_sheets[file_type] = _parse_reconciliation_sheet(
                        ws, file_type
                    )
                    continue
                # CrossTypeRules_<FILETYPE> (EC-S8)
                if sheet_name.startswith(ctr_prefix):
                    file_type = sheet_name[len(ctr_prefix):]
                    cross_type_rules_sheets[file_type] = (
                        _parse_cross_type_rules_sheet(ws, file_type)
                    )
                    continue
                # *_Mapping
                if sheet_name.endswith("_Mapping"):
                    mapping_sheets[sheet_name] = _parse_mapping_sheet(ws)
                    continue
                # *_Rules
                if sheet_name.endswith("_Rules"):
                    rules_sheets[sheet_name] = _parse_rules_sheet(ws)
                    continue
                # Unknown sheet — EC-S1 validator already emitted a warning;
                # the reader silently ignores it (e.g. "Notes" scratch sheets).

            return OnboardingWorkbook(
                source=source,
                input_files=input_files,
                output_files=output_files,
                multi_record_sheets=multi_record_sheets,
                reconciliation_sheets=reconciliation_sheets,
                cross_type_rules_sheets=cross_type_rules_sheets,
                mapping_sheets=mapping_sheets,
                rules_sheets=rules_sheets,
            )
        finally:
            wb.close()


def read_workbook(path: Path | str) -> OnboardingWorkbook:
    """Module-level convenience wrapper around :meth:`WorkbookReader.read`.

    Args:
        path: Filesystem path to the ``.xlsx`` onboarding workbook.

    Returns:
        The fully-parsed :class:`OnboardingWorkbook` dataclass tree.
    """
    return WorkbookReader().read(path)


__all__ = [
    "WorkbookReader",
    "read_workbook",
]
