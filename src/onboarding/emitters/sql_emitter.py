"""Oracle-dialect ``expected_*.sql`` emitter (ED-S2).

Generates the L2b SQL-Truth "expected rowset" SQL files that the
:mod:`scripts.e2e_lib.db_truth_comparator` engine executes to fetch the
expected rows for each reconciled record type. Inputs:

    * The parsed :class:`~src.onboarding.models.OnboardingWorkbook` (for
      reconciliation rows' ``staging_table`` / ``predicate`` /
      ``expected_sql_override`` cells and the ``MultiRecord_<FILETYPE>``
      cross-reference between ``record_type_name`` and the per-type
      ``*_Mapping`` sheet that defines the projected columns).
    * The list of :class:`~src.onboarding.emitters.mapping_emitter.EmittedMappingArtefact`
      produced by EC-S4 (the mapping JSON's ``fields[]`` carries each
      field's ``name`` (DASH form), ``target_name`` (snake-case SQL column
      name), ``data_type`` and ``format`` — everything the emitter needs to
      pick the right Oracle wrapping per column).

Public API
----------
    >>> from src.onboarding.workbook_reader import read_workbook
    >>> from src.onboarding.emitters.mapping_emitter import emit_mapping_artefacts
    >>> from src.onboarding.emitters.sql_emitter import emit_sql_artefacts
    >>> wb = read_workbook("templates/SHAW_onboarding.xlsx")
    >>> mapping_artefacts = emit_mapping_artefacts(wb)
    >>> sql_artefacts = emit_sql_artefacts(wb, mapping_artefacts, source_code="SHAW")

Sprint 3 scope (this story = ED-S2)
-----------------------------------
The emitter targets STRUCTURAL equivalence with the committed
``config/e2e/sources/SHAW/sql/tranert/20_query/expected_*.sql`` files:

    * Same column count (one ``AS`` per workbook-projected field, plus
      the DASH-quoted key-column duplicates).
    * Same alias spellings: underscore form (``BK_NUM_BRT``) for the
      primary projection, DASH-quoted form (``"BK-NUM-BRT"``) for each
      key column duplicated immediately after its underscore version.
    * Same ``FROM <staging_schema>.<staging_table> t`` clause shape.
    * Same ``WHERE <predicate>`` clause when the workbook carries a
      ``predicate`` cell; clause omitted when blank.

The emitter applies a best-effort Oracle-dialect wrapping per column
based on the mapping field's ``data_type`` + ``format``:

    * ``string`` -> ``TRIM(t.<COL>) AS <COL>`` (matches the committed
      pattern for every ``string`` column).
    * ``date`` with ``MM/DD/CCYY``-style format -> ``TO_CHAR(t.<COL>,
      'MM/DD/YYYY') AS <COL>`` (Oracle's date mask uses ``YYYY`` for the
      four-digit year; the workbook's ``CCYY`` is COBOL notation).
    * ``decimal`` / ``numeric`` with ``-Z(N).9(M)`` format -> ``LTRIM(
      TO_CHAR(t.<COL>, 'FM9...90.00'), '0') AS <COL>`` (leading-zero
      suppression so ``0`` renders ``.00``; matches the committed
      pattern for every amount column).
    * ``decimal`` / ``numeric`` with ``9(N)`` format -> ``LPAD(
      TO_CHAR(t.<COL>), N, '0') AS <COL>`` (zero-padding to the format
      width; matches the committed pattern for ITM-CNT-BRT).
    * Anything else -> ``TRIM(t.<COL>) AS <COL>`` (safe default; matches
      the committed pattern for most decimal columns without a
      specialised format).

Byte-equivalence with the committed files is explicitly DEFERRED to
ED-S4 (Sprint 4). The committed files use hand-curated column alignment
(multi-space padding) and reference comments that the emitter does NOT
attempt to replicate. The round-trip test compares
whitespace-normalised structural equivalence only — column set, alias
order, FROM clause, WHERE clause.

CTAS-vs-view fallback (ED-S3, Sprint 4)
---------------------------------------
The Source sheet's optional ``expected_table_strategy`` column selects
the wrapper shape so the emitter works against restricted Oracle
schemas (the historic ``cm3int``, now de-branded to ``app_int``) where
the validation user lacks ``CREATE VIEW``:

    * ``view`` (default) -- emit the bare ``SELECT`` (engine wraps it
      behind ``CREATE OR REPLACE VIEW`` at run time). This is the
      ED-S2 behaviour, preserved for backward compatibility.
    * ``ctas`` -- wrap as ``CREATE TABLE <schema>.EXPECTED_<TOKEN>_TBL
      AS <SELECT>`` inside an idempotent ``BEGIN EXECUTE IMMEDIATE …
      EXCEPTION WHEN OTHERS THEN IF SQLCODE != -955 THEN RAISE`` PL/SQL
      block. ORA-00955 = "name is already used by an existing object" so
      re-running the bootstrap is a no-op (matches the committed
      ``00_bootstrap/030_expected_tables.sql`` pattern verbatim).
    * ``ctas_with_drop`` -- prepend a separate ``BEGIN EXECUTE IMMEDIATE
      'DROP TABLE <schema>.EXPECTED_<TOKEN>_TBL PURGE'; EXCEPTION WHEN
      OTHERS THEN IF SQLCODE NOT IN (-942) THEN RAISE`` block so the
      CTAS replaces any prior copy. ORA-00942 = "table or view does
      not exist" so the DROP is idempotent on first run.

The ``<TOKEN>`` derives from the reconciliation row's record_type_name:
``rt_`` prefix stripped + upper-cased. ``batch_header`` ->
``BATCH_HEADER``; ``rt_32000`` -> ``32000``; ``(flat)`` -> ``<FT>``.
This matches the committed convention (``EXPECTED_BATCH_HEADER_TBL``,
``EXPECTED_32000_TBL``). When the workbook carries an explicit
``staging_table`` cell, that name is used verbatim instead.

Override semantics
------------------
The reconciliation row's ``expected_sql_override`` field is the
single source of truth for whether the BA hand-authored the SQL:

    * Non-blank override (e.g. ``"expected_batch_header.sql"``) ->
      the emitter SKIPS the row entirely. The BA's hand-authored SQL
      remains the authority; the emitter must not clobber it.
    * Blank override (or the ED-S1 ``auto`` marker substituted
      downstream) -> the emitter generates the SQL file at the
      canonical path.

This contract preserves the EC-S9-reverse-engineered SHAW state: every
SHAW TRANERT reconciliation row carries an override pointing at a
hand-authored committed file, so the SQL emitter produces zero
artefacts for SHAW today. The artefacts are produced when a NEW source
is onboarded without operator overrides — the BA-value moment.

Per-record-type cross-reference
--------------------------------
For multi-record reconciliation rows the emitter looks up the
record_type_name in the workbook's ``MultiRecord_<FILETYPE>`` sheet to
find the mapping-sheet name (which becomes the EC-S4 layout-tagged
mapping JSON path under ``config/mappings/``). The mapping artefact's
in-memory ``content`` (JSON text) is parsed once per emitted SQL file
so the emitter sees the converter-resolved ``target_name`` (SQL column
name), ``data_type``, and ``format`` for every column verbatim.

For flat reconciliation rows (``record_type_name == "(flat)"``) the
emitter looks up the matching flat mapping artefact by file_type +
source_code (``config/mappings/<SOURCE>_<FT>.json``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from src.onboarding.emitters import EmitterError, derive_layout_tag
from src.onboarding.emitters.mapping_emitter import EmittedMappingArtefact
from src.onboarding.models import (
    MappingSheet,
    MultiRecordRow,
    OnboardingWorkbook,
    ReconciliationRow,
    ReconciliationSheet,
)

# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------

#: Marker token reserved by ED-S1 for "auto-derive at ED-S2". When the
#: workbook's ``expected_sql_override`` cell carries this token (or is
#: blank), ED-S2 produces a generated SQL file at the canonical path.
_EXPECTED_SQL_AUTO_MARKER: str = "auto"

#: Sentinel matching :data:`src.onboarding.emitters.reconciliation_emitter._FLAT_RECORD_TYPE_TOKEN`.
_FLAT_RECORD_TYPE_TOKEN: str = "(flat)"

#: Default Oracle staging schema when the workbook's ``SourceInfo.staging_schema``
#: is blank. SHAW uses ``app_int``; this default lower-cases it to match the
#: committed convention.
_DEFAULT_STAGING_SCHEMA: str = "app_int"

#: Format-pattern regex for ``9(N)`` zero-padded fixed-width decimals.
_FORMAT_FIXED_NUMERIC: re.Pattern[str] = re.compile(r"^9\((\d+)\)$")

#: Format-pattern regex for ``-Z(...).9(M)`` Oracle amount columns.
_FORMAT_SIGNED_AMOUNT: re.Pattern[str] = re.compile(r"^-?Z\((\d+)\)\.9\((\d+)\)$")

#: Format hints for date columns. The workbook's ``MM/DD/CCYY`` notation is
#: COBOL; Oracle's ``TO_CHAR`` mask uses ``YYYY`` for the four-digit year.
_DATE_FORMATS: dict[str, str] = {
    "MM/DD/CCYY": "MM/DD/YYYY",
    "MM/DD/YYYY": "MM/DD/YYYY",
}


# ---------------------------------------------------------------------------
# Public artefact dataclass.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EmittedSqlArtefact:
    """One emitted ``expected_*.sql`` artefact.

    Attributes:
        path: Repo-relative path the artefact should be written to
            (e.g. ``config/e2e/sources/SHAW/sql/tranert/20_query/expected_batch_header.sql``).
            The emitter never writes to disk; the orchestrator (CLI)
            owns filesystem I/O.
        content: The SQL text in Oracle dialect, terminated with ``\\n``.
    """

    path: str
    content: str


# ---------------------------------------------------------------------------
# Path / cross-reference helpers.
# ---------------------------------------------------------------------------


def _file_type_lower(file_type: str) -> str:
    """Lower-case the ``file_type`` for path derivation (``TRANERT`` -> ``tranert``)."""
    return file_type.lower()


def _record_type_filename_stem(record_type_name: str) -> str:
    """Derive the SQL filename stem from a reconciliation row's record_type name.

    The committed convention uses the record_type_name verbatim with a
    leading ``expected_`` prefix:

        * ``batch_header`` -> ``expected_batch_header``
        * ``rt_32000`` -> ``expected_32000`` (the ``rt_`` prefix is
          conventionally dropped on the SQL filename so the committed
          ``expected_32000.sql`` matches; we preserve this convention).
        * ``(flat)`` -> ``expected_flat`` (synthetic; only reached when
          the BA uses the ``(flat)`` token for a flat output file).

    Args:
        record_type_name: The ``record_type_name`` cell value from the
            reconciliation row.

    Returns:
        The filename stem without the ``.sql`` suffix.
    """
    if record_type_name == _FLAT_RECORD_TYPE_TOKEN:
        return "expected_flat"
    if record_type_name.startswith("rt_"):
        return f"expected_{record_type_name[3:]}"
    return f"expected_{record_type_name}"


def _derive_output_path(
    source_code: str,
    file_type: str,
    record_type_name: str,
    output_dir: str,
) -> str:
    """Compute the on-disk path for an emitted ``expected_*.sql`` file.

    Args:
        source_code: The source code (e.g. ``"SHAW"``).
        file_type: The uppercase file type from the workbook
            (e.g. ``"TRANERT"``).
        record_type_name: The reconciliation row's ``record_type_name``.
        output_dir: The base directory under which the path is rooted
            (defaults to ``config/e2e/sources``).

    Returns:
        The repo-relative path
        ``<output_dir>/<SOURCE>/sql/<filetype_lower>/20_query/expected_<stem>.sql``.
    """
    stem = _record_type_filename_stem(record_type_name)
    return (
        f"{output_dir.rstrip('/')}/{source_code}/sql/"
        f"{_file_type_lower(file_type)}/20_query/{stem}.sql"
    )


def _index_multi_record_rows(
    workbook: OnboardingWorkbook, file_type: str
) -> dict[str, MultiRecordRow]:
    """Index a ``MultiRecord_<FILETYPE>`` sheet by ``record_type_name``.

    Mirrors the helper of the same name in
    :mod:`src.onboarding.emitters.reconciliation_emitter` so the SQL
    emitter and the reconciliation YAML emitter share the same
    cross-reference convention.

    Args:
        workbook: The parsed workbook.
        file_type: The uppercase file type to look up.

    Returns:
        A dict keyed by record-type name; empty when the workbook has no
        matching multi-record sheet.
    """
    sheet = workbook.multi_record_sheets.get(file_type)
    if sheet is None:
        return {}
    return {row.record_type_name: row for row in sheet.rows}


def _index_mapping_artefacts(
    mapping_artefacts: list[EmittedMappingArtefact],
) -> dict[str, dict[str, Any]]:
    """Parse each mapping JSON artefact once and index by filename basename.

    Args:
        mapping_artefacts: The list returned by
            :func:`src.onboarding.emitters.mapping_emitter.emit_mapping_artefacts`.

    Returns:
        A dict keyed by basename without extension (e.g.
        ``"SHAW_TRANERT_BATCH_HEADER_mapping"``), value is the parsed
        mapping JSON dict. Umbrella YAML artefacts are skipped (only
        flat JSON + per-type JSON kinds carry ``fields[]``).
    """
    index: dict[str, dict[str, Any]] = {}
    for artefact in mapping_artefacts:
        if artefact.kind == "umbrella_yaml":
            continue
        # ``path`` is e.g. ``config/mappings/SHAW_TRANERT_BATCH_HEADER_mapping.json``.
        # Use the basename without ``.json`` as the index key.
        filename = artefact.path.rsplit("/", 1)[-1]
        stem = filename.rsplit(".", 1)[0]
        try:
            index[stem] = json.loads(artefact.content)
        except json.JSONDecodeError as exc:
            raise EmitterError(
                f"Failed to parse mapping artefact '{artefact.path}' "
                f"as JSON: {exc}"
            ) from exc
    return index


def _resolve_workbook_mapping_sheet(
    workbook: OnboardingWorkbook,
    recon_row: ReconciliationRow,
    multi_record_row: MultiRecordRow | None,
) -> MappingSheet | None:
    """Locate the workbook ``MappingSheet`` matching one reconciliation row.

    ED-S4: the SQL emitter needs the WORKBOOK mapping sheet (not the
    emitted JSON artefact) to read the per-row ``reconciliation`` flag.
    The mapping JSON artefact intentionally does NOT carry the flag —
    it is a workbook-only BA curation toggle that drives which
    artefacts emit which subset, not a field-level engine attribute.

    Args:
        workbook: The parsed onboarding workbook.
        recon_row: The reconciliation row being emitted.
        multi_record_row: The matching ``MultiRecord_<FILETYPE>`` row,
            or ``None`` for flat reconciliation entries.

    Returns:
        The matching :class:`MappingSheet`, or ``None`` when the
        cross-reference cannot be resolved.
    """
    if recon_row.record_type_name == _FLAT_RECORD_TYPE_TOKEN:
        return None
    if multi_record_row is None:
        return None
    return workbook.mapping_sheets.get(multi_record_row.mapping_sheet)


def _resolve_mapping_for_record_type(
    source_code: str,
    file_type: str,
    recon_row: ReconciliationRow,
    multi_record_row: MultiRecordRow | None,
    mapping_index: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Locate the mapping JSON dict matching one reconciliation row.

    For multi-record reconciliation rows (``record_type_name != "(flat)"``)
    the matching mapping JSON is the per-type artefact named after the
    EC-S4 layout tag (e.g. ``SHAW_TRANERT_NEW1_mapping``). For flat
    rows the matching mapping is the per-file artefact
    (``SHAW_<FT>``).

    Args:
        source_code: The source code.
        file_type: The uppercase file type.
        recon_row: The reconciliation row being emitted.
        multi_record_row: The matching ``MultiRecord_<FILETYPE>`` row,
            or ``None`` for flat reconciliation entries.
        mapping_index: The dict returned by
            :func:`_index_mapping_artefacts`.

    Returns:
        The matching mapping JSON dict, or ``None`` when the
        cross-reference cannot be resolved. The caller surfaces this
        as an :class:`EmitterError` only if the SQL emission for the
        row is required (i.e. no override).
    """
    if recon_row.record_type_name == _FLAT_RECORD_TYPE_TOKEN:
        stem = f"{source_code}_{file_type}"
        return mapping_index.get(stem)

    if multi_record_row is None:
        return None

    try:
        layout_tag = derive_layout_tag(
            file_type, multi_record_row.mapping_sheet, suffix="_Mapping"
        )
    except EmitterError:
        return None
    stem = f"{source_code}_{file_type}_{layout_tag}_mapping"
    return mapping_index.get(stem)


# ---------------------------------------------------------------------------
# Per-column Oracle-dialect wrapping.
# ---------------------------------------------------------------------------


def _column_expression(field: dict[str, Any]) -> str:
    """Build the Oracle-dialect column projection for one mapping field.

    Returns the right-hand side of one ``SELECT`` projection (the
    expression to the LEFT of the ``AS <alias>`` part). The alias is
    rendered separately by :func:`_build_column_line` so the key-column
    duplication (underscore + DASH-quoted) can share the same
    expression.

    Wrapping logic — see the module docstring for the full table. Falls
    back to ``TRIM(t.<COL>)`` for any combination not explicitly
    enumerated, matching the committed pattern's default-safe choice.

    ED-S4: when the workbook carries a
    ``__reconciliation_expression_override`` key on the field dict
    (spliced in by :func:`_curate_fields`), that value is returned
    verbatim — the BA owns the expression. This lets the committed
    SHAW SQL files (which mix ``TRIM`` and ``LPAD`` per
    hand-curation) round-trip without the emitter needing to predict
    the BA's choice from ``format`` alone.

    Args:
        field: One element of the mapping JSON's ``fields[]`` array.

    Returns:
        The expression text (e.g. ``"TRIM(t.BK_NUM_BRT)"`` or
        ``"TO_CHAR(t.EFF_DAT_ERT, 'MM/DD/YYYY')"``).
    """
    override = field.get("__reconciliation_expression_override", "")
    if override:
        return override
    target_name = (field.get("target_name") or "").strip()
    if not target_name:
        # Fall back to upper-snake form of the DASH name.
        target_name = (field.get("name") or "").replace("-", "_")
    col = target_name.upper()

    data_type = (field.get("data_type") or "").strip().lower()
    fmt = (field.get("format") or "").strip()

    if data_type == "date":
        oracle_mask = _DATE_FORMATS.get(fmt, "MM/DD/YYYY")
        return f"TO_CHAR(t.{col}, '{oracle_mask}')"

    if data_type in ("decimal", "numeric", "integer"):
        amt_match = _FORMAT_SIGNED_AMOUNT.match(fmt) if fmt else None
        if amt_match is not None:
            int_width = int(amt_match.group(1))
            frac_width = int(amt_match.group(2))
            # ``FM`` strips Oracle's leading sign space; the trailing
            # ``LTRIM(..., '0')`` matches the committed pattern (yielding
            # ``.00`` for zero and ``.50`` for 0.5).
            mask_int = "9" * (int_width - 1) + "0" if int_width >= 1 else "0"
            mask_frac = "0" * frac_width if frac_width >= 1 else ""
            mask = f"FM{mask_int}.{mask_frac}" if mask_frac else f"FM{mask_int}"
            return f"LTRIM(TO_CHAR(t.{col}, '{mask}'), '0')"

        fixed_match = _FORMAT_FIXED_NUMERIC.match(fmt) if fmt else None
        if fixed_match is not None:
            width = int(fixed_match.group(1))
            return f"LPAD(TO_CHAR(t.{col}), {width}, '0')"

        # Decimal without specialised format -> TRIM (matches the
        # committed pattern for BK_NUM_BRT etc.).
        return f"TRIM(t.{col})"

    # ``string`` and everything else -> TRIM.
    return f"TRIM(t.{col})"


def _column_alias(field: dict[str, Any]) -> str:
    """Compute the underscore-form SQL alias for a mapping field.

    ED-S4: when the workbook carries a
    ``__reconciliation_column_override`` key on the field dict
    (spliced in by :func:`_curate_fields`), that value is returned
    verbatim — the BA owns the alias, matching the reconciliation
    YAML's ``expected_column`` cell.

    Args:
        field: One element of the mapping JSON's ``fields[]`` array.

    Returns:
        The upper-case underscore alias (e.g. ``"BK_NUM_BRT"``).
    """
    override = field.get("__reconciliation_column_override", "")
    if override:
        return override
    target_name = (field.get("target_name") or "").strip()
    if target_name:
        return target_name.upper()
    return (field.get("name") or "").replace("-", "_").upper()


def _column_dash_alias(field: dict[str, Any]) -> str:
    """Compute the DASH-quoted alias for a mapping field.

    The Oracle quoted identifier (``"BK-NUM-BRT"``) preserves the dash
    form so the reconciliation spec's ``key`` resolves on both the
    parsed file side and the SQL rowset side.

    Args:
        field: One element of the mapping JSON's ``fields[]`` array.

    Returns:
        The DASH form wrapped in double quotes (e.g.
        ``'"BK-NUM-BRT"'``).
    """
    dash_name = (field.get("name") or "").strip()
    return f'"{dash_name}"'


# ---------------------------------------------------------------------------
# SQL document assembly.
# ---------------------------------------------------------------------------


def _build_column_line(
    expression: str,
    alias: str,
    *,
    indent: int,
    alias_column: int,
    is_last: bool,
) -> str:
    """Render one column projection line in the canonical alignment style.

    ED-S4: ``alias_column`` controls the 1-indexed column where the
    ``AS`` keyword starts. Padding is applied between the expression
    and ``AS`` so that every projection's ``AS`` lines up vertically,
    matching the hand-curated committed-SQL alignment. When the
    expression is wider than the alignment column the function falls
    back to a single space before ``AS`` (degenerate case, matches
    the committed ``expected_32010.sql`` line-continuation shape).

    Args:
        expression: The right-hand side of the projection
            (e.g. ``"TRIM(t.BK_NUM_BRT)"``).
        alias: The alias text including any quoting
            (e.g. ``BK_NUM_BRT`` or ``"BK-NUM-BRT"``).
        indent: Leading spaces before the expression.
        alias_column: 1-indexed column for the ``AS`` keyword
            (computed from the widest expression on the SELECT body).
        is_last: When ``True`` the line gets no trailing comma (the
            final projection); otherwise it does.

    Returns:
        A single line WITHOUT the trailing newline.
    """
    sep = "" if is_last else ","
    prefix = f"{' ' * indent}{expression}"
    # ``alias_column`` is 1-indexed; convert to 0-indexed padded length.
    target_len = alias_column - 1
    if target_len > len(prefix):
        padding = " " * (target_len - len(prefix))
    else:
        padding = " "
    return f"{prefix}{padding}AS {alias}{sep}"


def _build_select_body(
    fields: list[dict[str, Any]],
    key_columns: list[str],
) -> str:
    """Build the ``SELECT`` body (columns + key duplications) for one record type.

    ED-S4 column-alignment polish: every ``AS`` keyword lines up
    vertically at the column immediately after the widest projection
    expression on the file (+1 space). Matches the hand-curated
    committed-SQL convention across the SHAW TRANERT
    ``expected_*.sql`` family.

    Args:
        fields: The mapping JSON's ``fields[]`` array (already
            curated by the caller per the BA's ``reconciliation``
            flags; ED-S4 takes the list verbatim).
        key_columns: The reconciliation row's ``key`` (DASH form).

    Returns:
        The ``SELECT`` body text (starting with ``"SELECT "``, ending
        just before the ``FROM`` clause). No trailing newline.
    """
    # Build a (expression, underscore_alias, dash_alias_or_None) tuple
    # per field. Insert a duplicate "dash" projection immediately after
    # any column whose DASH name appears in the reconciliation row's
    # key_columns. Matches the committed convention exactly.
    key_set = {k.strip() for k in key_columns}
    lines_data: list[tuple[str, str]] = []
    for field in fields:
        expression = _column_expression(field)
        alias = _column_alias(field)
        lines_data.append((expression, alias))
        dash_name = (field.get("name") or "").strip()
        if dash_name in key_set:
            dash_alias = _column_dash_alias(field)
            lines_data.append((expression, dash_alias))

    if not lines_data:
        # No fields projected -- emit an explicit error rather than
        # silently producing an empty SELECT (which Oracle rejects).
        raise EmitterError(
            "Cannot emit expected_*.sql: mapping artefact carries no fields."
        )

    # Compute the alignment column: max(expression length + ``SELECT ``
    # prefix on first line / ``       `` indent on continuation lines)
    # + 1 space + position of ``AS``. The ``SELECT `` prefix is 7 chars,
    # matching the 7-space indent on continuation lines so the
    # expression columns align across all rows.
    indent = 7
    max_expr_width = max(len(expr) for expr, _ in lines_data)
    alias_column = indent + max_expr_width + 2  # 1-indexed, +1 space, +1 to 1-base

    rendered_lines: list[str] = []
    for idx, (expression, alias) in enumerate(lines_data):
        is_last = idx == len(lines_data) - 1
        sep = "" if is_last else ","
        if idx == 0:
            # ``SELECT `` prefix; pad expression to alias_column - 1.
            prefix = f"SELECT {expression}"
            target_len = alias_column - 1
            padding = (
                " " * (target_len - len(prefix))
                if target_len > len(prefix)
                else " "
            )
            rendered_lines.append(f"{prefix}{padding}AS {alias}{sep}")
        else:
            rendered_lines.append(
                _build_column_line(
                    expression,
                    alias,
                    indent=indent,
                    alias_column=alias_column,
                    is_last=is_last,
                )
            )

    return "\n".join(rendered_lines)


def _derive_staging_table(
    recon_row: ReconciliationRow, file_type: str
) -> str:
    """Compute the FROM-clause table name.

    Uses the workbook's ``staging_table`` cell when non-blank. Falls
    back to the committed convention
    ``EXPECTED_<record_type_or_filetype>_TBL`` when the cell is blank.

    Args:
        recon_row: The reconciliation row being emitted.
        file_type: The uppercase file type.

    Returns:
        The unqualified staging table name (without the schema prefix).
    """
    if recon_row.staging_table:
        return recon_row.staging_table.strip()

    # Fallback to the committed convention:
    #   batch_header        -> EXPECTED_BATCH_HEADER_TBL
    #   rt_32000            -> EXPECTED_32000_TBL
    #   (flat)              -> EXPECTED_<FT>_TBL
    rt_name = recon_row.record_type_name
    if rt_name == _FLAT_RECORD_TYPE_TOKEN:
        token = file_type.upper()
    elif rt_name.startswith("rt_"):
        token = rt_name[3:].upper()
    else:
        token = rt_name.upper()
    return f"EXPECTED_{token}_TBL"


def _ctas_token(recon_row: ReconciliationRow, file_type: str) -> str:
    """Derive the ``EXPECTED_<TOKEN>_TBL`` suffix for the CTAS wrapper.

    Matches the committed convention (``EXPECTED_BATCH_HEADER_TBL``,
    ``EXPECTED_32000_TBL``):

        * ``batch_header`` -> ``BATCH_HEADER``
        * ``rt_32000`` -> ``32000`` (``rt_`` prefix stripped)
        * ``(flat)`` -> ``<FT>`` (upper-cased file type)

    Args:
        recon_row: The reconciliation row being emitted.
        file_type: The uppercase file type from the workbook.

    Returns:
        The token in upper-case.
    """
    rt_name = recon_row.record_type_name
    if rt_name == _FLAT_RECORD_TYPE_TOKEN:
        return file_type.upper()
    if rt_name.startswith("rt_"):
        return rt_name[3:].upper()
    return rt_name.upper()


def _ctas_table_qualified(
    workbook: OnboardingWorkbook,
    recon_row: ReconciliationRow,
    file_type: str,
) -> str:
    """Compute the schema-qualified target table for the CTAS wrapper.

    When the reconciliation row carries an explicit ``staging_table``
    cell that name is used verbatim under the workbook's staging
    schema; otherwise the committed ``EXPECTED_<TOKEN>_TBL`` convention
    is used.

    Args:
        workbook: The parsed workbook (for ``source.staging_schema``).
        recon_row: The reconciliation row being emitted.
        file_type: The uppercase file type from the workbook.

    Returns:
        ``"<schema_lower>.<TABLE>"`` (e.g. ``"app_int.EXPECTED_BATCH_HEADER_TBL"``).
    """
    schema = (workbook.source.staging_schema or _DEFAULT_STAGING_SCHEMA).strip()
    schema_lower = schema.lower()
    if recon_row.staging_table:
        table = recon_row.staging_table.strip()
    else:
        table = f"EXPECTED_{_ctas_token(recon_row, file_type)}_TBL"
    return f"{schema_lower}.{table}"


def _build_ctas_drop_block(qualified_table: str) -> str:
    """Render the idempotent ``DROP TABLE … PURGE`` PL/SQL block.

    Matches the committed ``00_bootstrap/030_expected_tables.sql``
    exception-trap convention but swaps the ORA-00955 trap (name
    already in use) for ORA-00942 (table or view does not exist) so
    the DROP becomes idempotent on first-run before any CTAS has
    populated the schema.

    Args:
        qualified_table: The schema-qualified target table name
            (e.g. ``"app_int.EXPECTED_BATCH_HEADER_TBL"``).

    Returns:
        The PL/SQL block text terminated with a trailing ``/`` line
        (the Oracle anonymous-PL/SQL statement separator) and a blank
        line so the subsequent CTAS block stands alone.
    """
    return (
        "BEGIN\n"
        f"  EXECUTE IMMEDIATE 'DROP TABLE {qualified_table} PURGE';\n"
        "EXCEPTION\n"
        "  WHEN OTHERS THEN\n"
        "    IF SQLCODE NOT IN (-942) THEN RAISE; END IF;\n"
        "END;\n"
        "/\n"
    )


def _build_ctas_create_block(
    qualified_table: str,
    select_text: str,
) -> str:
    """Render the idempotent ``CREATE TABLE … AS SELECT`` PL/SQL block.

    Wraps the SELECT inside ``BEGIN EXECUTE IMMEDIATE q'[ … ]'; …
    EXCEPTION WHEN OTHERS THEN IF SQLCODE != -955 THEN RAISE; END IF;
    END; /`` matching the committed ``030_expected_tables.sql`` convention
    verbatim. ORA-00955 = "name is already used by an existing object"
    so re-running the bootstrap against an already-populated schema is
    a no-op for that table.

    Uses Oracle alternative quoting ``q'[ … ]'`` so the single quotes
    inside the SELECT body (e.g. ``'MM/DD/YYYY'`` in ``TO_CHAR`` masks,
    BA-authored predicate string literals) need no escaping.

    Args:
        qualified_table: The schema-qualified target table name.
        select_text: The full inner ``SELECT`` statement (the ED-S2
            body including columns, FROM, and optional WHERE).

    Returns:
        The PL/SQL block text terminated with a trailing ``/`` line.
    """
    return (
        "BEGIN\n"
        "  EXECUTE IMMEDIATE q'[\n"
        f"    CREATE TABLE {qualified_table} AS\n"
        f"{_indent_select_body(select_text)}\n"
        "  ]';\n"
        "EXCEPTION\n"
        "  WHEN OTHERS THEN\n"
        "    IF SQLCODE != -955 THEN RAISE; END IF;\n"
        "END;\n"
        "/\n"
    )


def _indent_select_body(select_text: str) -> str:
    """Indent every line of the inner SELECT for the CTAS PL/SQL block.

    The committed ``030_expected_tables.sql`` indents the CTAS body four
    spaces inside the ``q'[ ]'`` payload for readability. We preserve
    that convention so emitted CTAS blocks look like the committed
    bootstrap.

    Args:
        select_text: The inner SELECT statement (no leading newline).

    Returns:
        The text with every non-empty line prefixed with four spaces.
    """
    indented_lines = []
    for line in select_text.splitlines():
        if line:
            indented_lines.append(f"    {line}")
        else:
            indented_lines.append(line)
    return "\n".join(indented_lines)


def _curate_fields(
    fields: list[dict[str, Any]],
    workbook_mapping_sheet: MappingSheet | None,
) -> list[dict[str, Any]]:
    """Filter + reorder ``fields`` to the BA-reconciliation subset (ED-S4).

    Looks up each mapping-JSON field's DASH ``name`` in the workbook
    mapping sheet; keeps it ONLY when the matching workbook row carries
    ``reconciliation = True``. Sheets with NO reconciliation-flagged
    rows fall back to projecting every field (the pre-ED-S4 default).

    Emission order follows the same two-bucket sort as the
    reconciliation YAML emitter: rows with an explicit
    ``reconciliation_order > 0`` come first (in that order), then rows
    without explicit order (in workbook row position). The two emitters
    stay in lock-step so the BA can edit ONE column to control the
    column order in both artefacts.

    Args:
        fields: The mapping JSON's ``fields[]`` array (full set).
        workbook_mapping_sheet: The matching workbook mapping sheet,
            or ``None`` when no cross-reference is available
            (defensive: emitter then projects the full set).

    Returns:
        The curated subset (or the full set when no curation applies).
    """
    if workbook_mapping_sheet is None:
        return fields
    sheet_rows = workbook_mapping_sheet.rows
    flagged_indexed: list[tuple[tuple[int, int, int], str]] = []
    for row_idx, row in enumerate(sheet_rows):
        if not row.reconciliation or not row.field_name:
            continue
        if row.reconciliation_order > 0:
            sort_key = (0, row.reconciliation_order, row_idx)
        else:
            sort_key = (1, row_idx, row_idx)
        flagged_indexed.append((sort_key, row.field_name))
    if not flagged_indexed:
        return fields
    flagged_indexed.sort(key=lambda pair: pair[0])
    name_order = [name for _, name in flagged_indexed]

    by_name: dict[str, dict[str, Any]] = {}
    for field in fields:
        dash = (field.get("name") or "").strip()
        if dash:
            by_name[dash] = field
    # Splice the BA-curated column-name + SQL-expression overrides onto
    # the emitted-fields dict so the per-column rendering helpers see
    # the BA values without changing their signatures. Copy first so the
    # mapping JSON dicts retain their original shape (downstream code
    # mutates them by-reference).
    overrides_by_name: dict[str, dict[str, str]] = {}
    for row in sheet_rows:
        if row.reconciliation and row.field_name:
            overrides_by_name[row.field_name] = {
                "column": row.reconciliation_column.strip(),
                "expression": row.reconciliation_sql_expression.strip(),
            }
    curated: list[dict[str, Any]] = []
    for name in name_order:
        field = by_name.get(name)
        if field is None:
            continue
        overrides = overrides_by_name.get(name)
        if overrides and (overrides["column"] or overrides["expression"]):
            spliced = dict(field)
            if overrides["column"]:
                spliced["__reconciliation_column_override"] = overrides["column"]
            if overrides["expression"]:
                spliced["__reconciliation_expression_override"] = overrides[
                    "expression"
                ]
            curated.append(spliced)
        else:
            curated.append(field)
    return curated


def _build_sql_document(
    workbook: OnboardingWorkbook,
    recon_row: ReconciliationRow,
    mapping: dict[str, Any],
    file_type: str,
    workbook_mapping_sheet: MappingSheet | None = None,
) -> str:
    """Assemble the full SQL document for one reconciliation row.

    Honours :attr:`SourceInfo.expected_table_strategy` (ED-S3) to wrap
    the inner SELECT in a CTAS / DROP+CTAS PL/SQL block when the
    target schema cannot ``CREATE VIEW``. The wrapper is the only
    difference between strategies; the SELECT body is identical
    regardless of strategy so a workbook can be toggled
    ``view`` <-> ``ctas`` <-> ``ctas_with_drop`` without losing the
    column projection alignment.

    ED-S4: when ``workbook_mapping_sheet`` is supplied AND any row on
    it carries ``reconciliation = True``, the SELECT projection is
    curated to the flagged subset. Otherwise every field is projected
    (the pre-ED-S4 default).

    Args:
        workbook: The parsed workbook (for ``source.staging_schema``
            and ``source.expected_table_strategy``).
        recon_row: The reconciliation row.
        mapping: The parsed mapping JSON dict for the row's record type.
        file_type: The uppercase file type.
        workbook_mapping_sheet: The matching workbook mapping sheet
            for ED-S4 curation (optional).

    Returns:
        The full SQL text, terminated with a single trailing newline.

    Raises:
        EmitterError: When the mapping carries no ``fields`` array or
            curation yields an empty projection.
    """
    fields = mapping.get("fields") or []
    if not isinstance(fields, list) or not fields:
        raise EmitterError(
            f"Mapping for record_type {recon_row.record_type_name!r} "
            "has no fields[]; cannot emit SQL."
        )

    curated_fields = _curate_fields(fields, workbook_mapping_sheet)
    if not curated_fields:
        raise EmitterError(
            f"Mapping for record_type {recon_row.record_type_name!r} "
            "yields zero fields after BA reconciliation curation; "
            "cannot emit SQL."
        )

    select_body = _build_select_body(curated_fields, recon_row.key_columns)

    schema = (workbook.source.staging_schema or _DEFAULT_STAGING_SCHEMA).strip()
    # Match the committed convention's lower-case schema prefix.
    schema_lower = schema.lower()
    table = _derive_staging_table(recon_row, file_type)
    from_clause = f"  FROM {schema_lower}.{table} t"

    parts = [select_body, from_clause]
    if recon_row.predicate:
        parts.append(f" WHERE {recon_row.predicate}")

    select_text = "\n".join(parts)

    strategy = workbook.source.expected_table_strategy
    if strategy == "view":
        return select_text + "\n"

    qualified_table = _ctas_table_qualified(workbook, recon_row, file_type)
    create_block = _build_ctas_create_block(qualified_table, select_text)
    if strategy == "ctas":
        return create_block
    if strategy == "ctas_with_drop":
        drop_block = _build_ctas_drop_block(qualified_table)
        return drop_block + "\n" + create_block

    # Defensive: the reader validates the cell value, but if a caller
    # synthesises a SourceInfo with an unknown strategy we fail loudly
    # rather than silently produce a malformed file.
    raise EmitterError(
        f"Unknown expected_table_strategy {strategy!r}. Expected one of: "
        f"view, ctas, ctas_with_drop."
    )


# ---------------------------------------------------------------------------
# Public emitter class + module-level convenience function.
# ---------------------------------------------------------------------------


class SqlEmitter:
    """Emit Oracle-dialect ``expected_*.sql`` artefacts (ED-S2).

    Stateless across calls. The emitter never writes to disk -- it
    returns a list of :class:`EmittedSqlArtefact` instances so the
    orchestrator (``valdo onboard-source`` CLI) owns filesystem I/O
    and the ``--dry-run`` / ``--check`` modes can intercept.

    Usage:

        >>> from src.onboarding.workbook_reader import read_workbook
        >>> from src.onboarding.emitters.mapping_emitter import emit_mapping_artefacts
        >>> from src.onboarding.emitters.sql_emitter import SqlEmitter
        >>> wb = read_workbook("templates/SHAW_onboarding.xlsx")
        >>> mapping_artefacts = emit_mapping_artefacts(wb)
        >>> artefacts = SqlEmitter("SHAW").emit_all(wb, mapping_artefacts)

    Args:
        source_code: The source code (e.g. ``"SHAW"``). Used to derive
            both the on-disk path prefix and the layout-tagged mapping
            artefact lookup.
        output_dir: The base directory for emitted artefacts. Defaults
            to ``config/e2e/sources`` matching the committed convention.

    Raises:
        EmitterError: When ``source_code`` is empty or whitespace.
    """

    def __init__(
        self,
        source_code: str,
        output_dir: str = "config/e2e/sources",
    ) -> None:
        if not source_code or not source_code.strip():
            raise EmitterError(
                "SqlEmitter requires a non-empty source_code"
            )
        self._source_code = source_code.strip()
        self._output_dir = output_dir

    def emit_all(
        self,
        workbook: OnboardingWorkbook,
        mapping_artefacts: list[EmittedMappingArtefact],
    ) -> list[EmittedSqlArtefact]:
        """Emit one ``expected_*.sql`` artefact per reconciliation row WITHOUT an override.

        Args:
            workbook: The parsed onboarding workbook from EC-S2.
            mapping_artefacts: The list returned by EC-S4's
                :func:`~src.onboarding.emitters.mapping_emitter.emit_mapping_artefacts`.
                Carries the ``target_name`` / ``data_type`` / ``format``
                per field that the emitter needs to pick Oracle wrappers.

        Returns:
            Ordered list of :class:`EmittedSqlArtefact`. Empty when every
            reconciliation row carries an ``expected_sql_override``
            (the EC-S9-reverse-engineered SHAW state).

        Raises:
            EmitterError: When a row WITHOUT an override cannot be
                emitted (e.g. mapping cross-reference fails or the
                mapping carries no ``fields[]``). Override-carrying
                rows are silently skipped (the BA's hand-authored SQL
                is the authority).
        """
        mapping_index = _index_mapping_artefacts(mapping_artefacts)
        artefacts: list[EmittedSqlArtefact] = []

        for file_type, sheet in workbook.reconciliation_sheets.items():
            artefacts.extend(
                self._emit_for_sheet(workbook, mapping_index, file_type, sheet)
            )

        return artefacts

    def _emit_for_sheet(
        self,
        workbook: OnboardingWorkbook,
        mapping_index: dict[str, dict[str, Any]],
        file_type: str,
        sheet: ReconciliationSheet,
    ) -> list[EmittedSqlArtefact]:
        """Emit SQL artefacts for every override-free row in one reconciliation sheet."""
        multi_record_index = _index_multi_record_rows(workbook, file_type)
        artefacts: list[EmittedSqlArtefact] = []

        for recon_row in sheet.rows:
            if not recon_row.record_type_name:
                continue
            if self._row_has_override(recon_row):
                continue

            multi_record_row = (
                multi_record_index.get(recon_row.record_type_name)
                if recon_row.record_type_name != _FLAT_RECORD_TYPE_TOKEN
                else None
            )
            mapping = _resolve_mapping_for_record_type(
                self._source_code,
                file_type,
                recon_row,
                multi_record_row,
                mapping_index,
            )
            if mapping is None:
                raise EmitterError(
                    f"Cannot emit expected SQL for "
                    f"record_type={recon_row.record_type_name!r} "
                    f"(file_type={file_type!r}): no matching mapping "
                    f"artefact found. Available mappings: "
                    f"{sorted(mapping_index)}"
                )

            workbook_mapping_sheet = _resolve_workbook_mapping_sheet(
                workbook, recon_row, multi_record_row
            )
            content = _build_sql_document(
                workbook,
                recon_row,
                mapping,
                file_type,
                workbook_mapping_sheet=workbook_mapping_sheet,
            )
            artefacts.append(
                EmittedSqlArtefact(
                    path=_derive_output_path(
                        self._source_code,
                        file_type,
                        recon_row.record_type_name,
                        self._output_dir,
                    ),
                    content=content,
                )
            )

        return artefacts

    @staticmethod
    def _row_has_override(recon_row: ReconciliationRow) -> bool:
        """Decide whether a reconciliation row's SQL is hand-authored.

        Returns ``True`` when ``expected_sql_override`` is non-blank
        AND not the ED-S1 ``auto`` sentinel. Both states defer to ED-S2:

            * blank -> generate SQL
            * ``auto`` -> generate SQL
            * any other non-blank string -> SKIP (BA owns it)

        Args:
            recon_row: The reconciliation row being inspected.

        Returns:
            ``True`` when the row carries a real override filename.
        """
        override = (recon_row.expected_sql_override or "").strip()
        if not override:
            return False
        if override == _EXPECTED_SQL_AUTO_MARKER:
            return False
        return True


def emit_sql_artefacts(
    workbook: OnboardingWorkbook,
    mapping_artefacts: list[EmittedMappingArtefact],
    source_code: str | None = None,
    output_dir: str = "config/e2e/sources",
) -> list[EmittedSqlArtefact]:
    """Module-level convenience wrapper around :meth:`SqlEmitter.emit_all`.

    Args:
        workbook: The parsed ``OnboardingWorkbook`` tree from EC-S2.
        mapping_artefacts: EC-S4 mapping artefacts.
        source_code: Override for the source code. When ``None`` (the
            common case), the value is read from
            :attr:`OnboardingWorkbook.source.source_code`.
        output_dir: The base directory for emitted artefacts. Defaults
            to ``config/e2e/sources``.

    Returns:
        Ordered list of :class:`EmittedSqlArtefact`.

    Raises:
        EmitterError: When a row without an override cannot be emitted.
    """
    effective_source_code = (
        source_code if source_code is not None else workbook.source.source_code
    )
    return SqlEmitter(
        source_code=effective_source_code,
        output_dir=output_dir,
    ).emit_all(workbook, mapping_artefacts)


__all__ = [
    "EmittedSqlArtefact",
    "SqlEmitter",
    "emit_sql_artefacts",
]
