"""Canonical onboarding workbook schema definition + validator (EC-S1).

A BA onboards a new source by filling in a single Excel workbook conforming
to the schema declared in this module. The workbook contains:

    - ``Source`` (1 row of key/value pairs)
    - ``InputFiles`` (one row per input file)
    - ``OutputFiles`` (one row per generated output file)
    - ``MultiRecord_<FILETYPE>`` (one sheet per multi-record output file)
    - ``Reconciliation_<FILETYPE>`` (one sheet per L2b-reconciled output)
    - ``<FILETYPE>_Mapping`` or ``<FILETYPE>_<RECTYPE>_Mapping`` (one per layout)
    - ``<FILETYPE>_Rules`` or ``<FILETYPE>_<RECTYPE>_Rules`` (one per ruleset)

Two public entry points:

    validate_workbook(path) -> list[WorkbookSchemaError]
        Returns errors + warnings. Empty list means schema-clean.

    assert_workbook_valid(path) -> None
        Raises ``WorkbookSchemaError`` (multi-line message) if any errors
        are found. Warnings do not raise.

Design notes (EC-S1):
    - Sheet-name matching is CASE-INSENSITIVE for the dynamic patterns
      (``MultiRecord_*``, ``Reconciliation_*``, ``*_Mapping``, ``*_Rules``)
      but the fixed sheet names (``Source``, ``InputFiles``, ``OutputFiles``)
      must match exactly so the BA cannot ship two flavours by accident.
    - DASH-style field names (``LN-NUM-ERT``) are preserved verbatim through
      to the emitted SQL — see umbrella YAML at
      ``config/mappings/SHAW_TRANERT.yaml`` for the worked SHAW example.
    - Required columns are matched case-insensitively against trimmed header
      cells (BAs frequently re-type "Field Name" as "field name" or
      "Field name"). Unknown columns are tolerated — emitters skip them.
    - Unknown sheet names (e.g. ``Notes``, ``Glossary``) are reported as
      warnings, not errors, so BAs can keep scratch sheets in the workbook.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

# ---------------------------------------------------------------------------
# Required sheet names + dynamic-name patterns.
# ---------------------------------------------------------------------------

REQUIRED_FIXED_SHEETS: tuple[str, ...] = ("Source", "InputFiles", "OutputFiles")
"""Sheets that MUST be present in every onboarding workbook (exact match)."""

DYNAMIC_SHEET_PREFIXES: tuple[str, ...] = (
    "MultiRecord_",
    "Reconciliation_",
    "CrossTypeRules_",
)
"""Dynamic sheets keyed by file type (case-sensitive prefix).

``CrossTypeRules_<FILETYPE>`` (EC-S8) is optional — multi-record output files
without cross-record-type assertions simply omit it, and the emitter renders
``cross_type_rules: []`` in the umbrella YAML."""

DYNAMIC_SHEET_SUFFIXES: tuple[str, ...] = ("_Mapping", "_Rules")
"""Dynamic per-layout sheets (case-sensitive suffix). Per-record-type variants
match by suffix too: e.g. ``TRANERT_BATCH_HEADER_Mapping``."""

# ---------------------------------------------------------------------------
# Required columns per sheet type. Header-cell matching is case-insensitive
# (we lowercase + strip before comparing) so the BA does not have to be
# pixel-perfect on case.
# ---------------------------------------------------------------------------

SOURCE_SHEET_REQUIRED_COLUMNS: tuple[str, ...] = (
    "source_code",
    "schema_version",
    "release_tag",
    "description",
    "staging_schema",
    "output_root",
    "java_load_script",
    "java_generate_script",
    "gate_load_blocking",
    "gate_load_invoke_java",
    "gate_f2s_blocking",
    "gate_generate_blocking",
    "gate_generate_invoke_java",
    "gate_l1_blocking",
    "gate_l2b_blocking",
    "gate_l3_blocking",
    "gate_mr_report_blocking",
)

INPUT_FILES_REQUIRED_COLUMNS: tuple[str, ...] = (
    "file_type",
    "glob",
    "mapping_sheet",
    "target_staging_table",
    "thresholds_max_errors",
)

OUTPUT_FILES_REQUIRED_COLUMNS: tuple[str, ...] = (
    "file_type",
    "glob",
    "mapping_sheet",
    "rules_sheet",
    "tolerance_max_errors",
    "tolerance_max_error_pct",
    "tolerance_ignore_fields",
)

MULTI_RECORD_REQUIRED_COLUMNS: tuple[str, ...] = (
    "record_type_name",
    "discriminator_field",
    "discriminator_position",
    "discriminator_length",
    "match_kind",
    "match_value",
    "mapping_sheet",
    "rules_sheet",
    "cardinality",
)

RECONCILIATION_REQUIRED_COLUMNS: tuple[str, ...] = (
    "record_type_name",
    "key_columns",
    "staging_table",
    "predicate",
    "ignored_fields",
    "assertions",
    "expected_sql_override",
)

CROSS_TYPE_RULES_REQUIRED_COLUMNS: tuple[str, ...] = (
    "rule_id",
    "check",
    "record_type",
    "trailer_field",
    "count_of",
    "allow_empty_batch",
    "severity",
    "message",
)
"""Required columns for a ``CrossTypeRules_<FILETYPE>`` sheet (EC-S8).

The column set is the minimal cross-cut of fields the engine's
:class:`src.config.multi_record_config.CrossTypeRule` model actually unpacks
for the rule types currently emitted by operators in the SHAW worked example
(``header_trailer_count``). Additional fields used by other rule types
(``header_field``, ``detail_field``, ``sum_field``, ``sum_of``, ``when_type``,
``requires_type``, ``expected_order``, ``exactly``, ``enabled``) are accepted
as OPTIONAL columns (unknown columns are tolerated — emitters skip them) so
the sheet can grow over time without a schema-version bump.
"""

MAPPING_SHEET_REQUIRED_COLUMNS: tuple[str, ...] = (
    "Field Name",
    "Data Type",
)
"""Minimum required for any mapping sheet — matches the existing strict
template check in ``scripts/bulk_convert_mappings.py``. Position/Length are
*conditionally* required (only if format is fixed-width); enforced downstream
by the EC-S4 mapping emitter, not at the schema layer."""

RULES_SHEET_REQUIRED_COLUMNS: tuple[str, ...] = (
    "Rule ID",
    "Field",
    "Rule Type",
)
"""Minimum required for any rules sheet — matches the BA-friendly rules
template used by ``BARulesTemplateConverter``."""

# Aggregate map for diagnostic / introspection use (e.g. README generation).
REQUIRED_COLUMNS_BY_SHEET_TYPE: dict[str, tuple[str, ...]] = {
    "Source": SOURCE_SHEET_REQUIRED_COLUMNS,
    "InputFiles": INPUT_FILES_REQUIRED_COLUMNS,
    "OutputFiles": OUTPUT_FILES_REQUIRED_COLUMNS,
    "MultiRecord_*": MULTI_RECORD_REQUIRED_COLUMNS,
    "Reconciliation_*": RECONCILIATION_REQUIRED_COLUMNS,
    "CrossTypeRules_*": CROSS_TYPE_RULES_REQUIRED_COLUMNS,
    "*_Mapping": MAPPING_SHEET_REQUIRED_COLUMNS,
    "*_Rules": RULES_SHEET_REQUIRED_COLUMNS,
}


# ---------------------------------------------------------------------------
# Error dataclass.
# ---------------------------------------------------------------------------


@dataclass
class WorkbookSchemaError(Exception):
    """A schema validation finding (error or warning).

    Attributes:
        sheet: Sheet name where the issue was found (empty for workbook-level).
        cell: Excel cell address (e.g. ``A1``); empty for sheet-level issues.
        reason: Human-readable explanation including a remediation hint.
        severity: ``error`` (default) or ``warning``. Warnings do not raise.
    """

    sheet: str = ""
    cell: str = ""
    reason: str = ""
    severity: str = "error"
    # Optional secondary findings (used when bundling a list into a single
    # raisable exception via ``assert_workbook_valid``).
    findings: list["WorkbookSchemaError"] = field(default_factory=list)

    def __str__(self) -> str:  # pragma: no cover - trivial formatting
        loc = self.sheet or "<workbook>"
        if self.cell:
            loc = f"{loc}!{self.cell}"
        return f"[{self.severity}] {loc}: {self.reason}"


# ---------------------------------------------------------------------------
# Internal helpers.
# ---------------------------------------------------------------------------


def _normalize_header(value: object) -> str:
    """Lowercase + strip a header cell value for case-insensitive comparison."""
    if value is None:
        return ""
    return str(value).strip().lower()


def _classify_sheet(name: str) -> str | None:
    """Return the sheet type key (e.g. ``Source``, ``MultiRecord_*``,
    ``*_Mapping``) or ``None`` if the sheet name does not match any known
    pattern.

    Fixed sheet names match case-sensitively; dynamic prefixes/suffixes
    also match case-sensitively (we want ``Multirecord_X`` rejected with
    a "wrong case" hint rather than silently accepted).
    """
    if name in REQUIRED_FIXED_SHEETS:
        return name
    for prefix in DYNAMIC_SHEET_PREFIXES:
        if name.startswith(prefix) and len(name) > len(prefix):
            return f"{prefix}*"
    # Suffixes: check longest first to avoid masking (currently both are 8
    # chars but future-proofing for e.g. _MappingV2).
    for suffix in sorted(DYNAMIC_SHEET_SUFFIXES, key=len, reverse=True):
        if name.endswith(suffix) and len(name) > len(suffix):
            return f"*{suffix}"
    return None


def _case_insensitive_sheet_match_hint(name: str) -> str | None:
    """If ``name`` matches a known pattern when lowercased but not as-is,
    return a hint explaining the case fix. Used to give BAs an actionable
    error when they type ``multirecord_tranert`` instead of ``MultiRecord_TRANERT``.
    """
    lower = name.lower()
    for fixed in REQUIRED_FIXED_SHEETS:
        if lower == fixed.lower() and name != fixed:
            return f"sheet name '{name}' matches required sheet '{fixed}' only when case is normalised; rename the tab to '{fixed}' exactly (case-sensitive)"
    for prefix in DYNAMIC_SHEET_PREFIXES:
        if lower.startswith(prefix.lower()) and not name.startswith(prefix):
            return f"sheet name '{name}' matches dynamic prefix '{prefix}*' only when case is normalised; rename to '{prefix}{name[len(prefix):].upper()}' (prefix is case-sensitive)"
    for suffix in DYNAMIC_SHEET_SUFFIXES:
        if lower.endswith(suffix.lower()) and not name.endswith(suffix):
            stem = name[: -len(suffix)]
            return f"sheet name '{name}' matches dynamic suffix '*{suffix}' only when case is normalised; rename to '{stem}{suffix}' (suffix is case-sensitive)"
    return None


def _read_header_row(worksheet) -> list[tuple[str, str]]:
    """Return a list of ``(normalized_header, cell_address)`` for row 1 of
    ``worksheet``. Empty header cells are skipped.
    """
    headers: list[tuple[str, str]] = []
    for col_idx, cell in enumerate(worksheet[1], start=1):
        normalized = _normalize_header(cell.value)
        if normalized:
            address = f"{get_column_letter(col_idx)}1"
            headers.append((normalized, address))
    return headers


def _check_required_columns(
    worksheet,
    sheet_name: str,
    required: Iterable[str],
) -> list[WorkbookSchemaError]:
    """Verify the worksheet's header row contains every required column
    (case-insensitive). Returns one error per missing column.
    """
    headers = _read_header_row(worksheet)
    header_set = {h for h, _ in headers}
    findings: list[WorkbookSchemaError] = []
    next_col = len(headers) + 1
    for col in required:
        if col.lower() not in header_set:
            # Point at the next free column where the BA should add the header.
            cell_addr = f"{get_column_letter(next_col)}1"
            findings.append(
                WorkbookSchemaError(
                    sheet=sheet_name,
                    cell=cell_addr,
                    reason=(
                        f"missing required column '{col}'. Add it as a header "
                        f"cell (case-insensitive match) before re-running the validator."
                    ),
                )
            )
            next_col += 1
    return findings


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------


def validate_workbook(path: Path | str) -> list[WorkbookSchemaError]:
    """Validate an onboarding workbook against the canonical EC-S1 schema.

    Args:
        path: Path to the ``.xlsx`` workbook.

    Returns:
        List of ``WorkbookSchemaError`` findings. ``severity='error'`` entries
        block ``assert_workbook_valid``; ``severity='warning'`` entries are
        informational (e.g. unrecognised sheet names like ``Notes``).
        An empty list means the workbook is schema-clean.

    Raises:
        WorkbookSchemaError: If the workbook file cannot be opened at all
            (missing file, not a valid .xlsx). Per-sheet/per-column issues
            are returned, not raised.
    """
    path = Path(path)
    if not path.exists():
        raise WorkbookSchemaError(
            sheet="",
            cell="",
            reason=f"workbook file does not exist: {path}",
        )

    try:
        wb = load_workbook(filename=str(path), read_only=True, data_only=True)
    except Exception as exc:  # pragma: no cover - openpyxl wraps in many ways
        raise WorkbookSchemaError(
            sheet="",
            cell="",
            reason=f"could not open workbook '{path}': {exc}",
        ) from exc

    findings: list[WorkbookSchemaError] = []
    sheet_names = wb.sheetnames

    # --- 1. Required fixed sheets must be present. ---
    for required in REQUIRED_FIXED_SHEETS:
        if required not in sheet_names:
            hint = ""
            # If a case-flipped version exists, surface a targeted hint.
            for actual in sheet_names:
                if actual.lower() == required.lower():
                    hint = (
                        f" Found a sheet named '{actual}' — rename it to '{required}' "
                        f"(case-sensitive)."
                    )
                    break
            findings.append(
                WorkbookSchemaError(
                    sheet=required,
                    cell="",
                    reason=f"required sheet '{required}' is missing from workbook.{hint}",
                )
            )

    # --- 2. Classify every sheet; validate its column headers; warn on unknowns. ---
    for name in sheet_names:
        sheet_type = _classify_sheet(name)
        if sheet_type is None:
            hint = _case_insensitive_sheet_match_hint(name)
            if hint is not None:
                findings.append(
                    WorkbookSchemaError(
                        sheet=name,
                        cell="",
                        reason=hint,
                    )
                )
            else:
                findings.append(
                    WorkbookSchemaError(
                        sheet=name,
                        cell="",
                        reason=(
                            f"sheet '{name}' does not match any known pattern "
                            f"(Source, InputFiles, OutputFiles, MultiRecord_*, "
                            f"Reconciliation_*, *_Mapping, *_Rules). Treated as a "
                            f"BA scratch sheet and ignored by emitters."
                        ),
                        severity="warning",
                    )
                )
            continue

        required_cols = REQUIRED_COLUMNS_BY_SHEET_TYPE.get(sheet_type)
        if required_cols is None:  # pragma: no cover - defensive
            continue

        worksheet = wb[name]
        findings.extend(_check_required_columns(worksheet, name, required_cols))

    wb.close()
    return findings


def assert_workbook_valid(path: Path | str) -> None:
    """Raise ``WorkbookSchemaError`` if the workbook has any error-level
    findings. Warnings are silently ignored — call ``validate_workbook``
    directly to inspect them.

    The raised exception's ``reason`` is a multi-line summary listing every
    error finding with its sheet, cell address, and remediation hint.
    Individual findings are also attached on the exception's ``findings``
    list for programmatic inspection.

    Args:
        path: Path to the ``.xlsx`` workbook.

    Raises:
        WorkbookSchemaError: If one or more error-level findings are present.
    """
    findings = validate_workbook(path)
    errors = [f for f in findings if f.severity == "error"]
    if not errors:
        return

    lines = [
        f"Onboarding workbook '{Path(path).name}' failed schema validation "
        f"with {len(errors)} error(s):",
        "",
    ]
    lines.extend(f"  - {finding}" for finding in errors)
    lines.append("")
    lines.append(
        "See templates/source_onboarding_template_README.md for the canonical "
        "column reference."
    )
    raise WorkbookSchemaError(
        sheet="",
        cell="",
        reason="\n".join(lines),
        findings=errors,
    )


__all__ = [
    "WorkbookSchemaError",
    "REQUIRED_FIXED_SHEETS",
    "DYNAMIC_SHEET_PREFIXES",
    "DYNAMIC_SHEET_SUFFIXES",
    "SOURCE_SHEET_REQUIRED_COLUMNS",
    "INPUT_FILES_REQUIRED_COLUMNS",
    "OUTPUT_FILES_REQUIRED_COLUMNS",
    "MULTI_RECORD_REQUIRED_COLUMNS",
    "RECONCILIATION_REQUIRED_COLUMNS",
    "CROSS_TYPE_RULES_REQUIRED_COLUMNS",
    "MAPPING_SHEET_REQUIRED_COLUMNS",
    "RULES_SHEET_REQUIRED_COLUMNS",
    "REQUIRED_COLUMNS_BY_SHEET_TYPE",
    "validate_workbook",
    "assert_workbook_valid",
]
