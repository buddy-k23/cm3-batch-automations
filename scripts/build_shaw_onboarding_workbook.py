#!/usr/bin/env python3
"""Build the SHAW onboarding workbook + the blank canonical template (EC-S1 / EC-S9).

This script is the **canonical reference** for BAs who want to scaffold a new
onboarding workbook from existing Valdo configuration. It walks:

    - config/e2e/sources/SHAW.yml                  (source / inputs / outputs)
    - config/mappings/SHAW_<FILETYPE>.json|yaml    (per-file mappings + umbrellas)
    - config/mappings/SHAW_<FILETYPE>_<RT>_mapping.json (per-record-type mappings)
    - config/rules/SHAW_<FILETYPE>_<RT>_rules.json (per-record-type rules)
    - config/e2e/sources/SHAW/reconciliation/*.yml (L2b SQL-truth specs)

…and emits two workbooks:

    templates/source_onboarding_template.xlsx   (blank canonical template)
    templates/SHAW_onboarding.xlsx              (worked SHAW example)

Both workbooks pass ``src.onboarding.workbook_schema.assert_workbook_valid``.

Usage::

    python scripts/build_shaw_onboarding_workbook.py

The script is idempotent — running it again overwrites both workbooks. It is
NOT wired into CI; EC-S1 ships the artefacts directly. Sprint-2 / Sprint-3
follow-up stories (EC-S2 ... EC-S6) consume the workbooks via the schema
validator + readers built on top of them.

EC-S9 reverse-engineer contract
-------------------------------
Sprint 3's EC-S9 hardens the SHAW workbook to be a 1:1 reverse-engineering of
the currently committed ``config/mappings/`` + ``config/rules/`` artefacts:

    * For each ``output_files[].mapping`` path that points to a committed
      JSON file on disk, the corresponding ``<FILETYPE>_Mapping`` sheet is
      built by walking the JSON's ``fields:`` array and reverse-mapping
      each field entry to a workbook row. ``key_columns: ['BK-NUM-ERT']``
      survives intact (vs the prior BA-CSV pipeline which silently
      dropped the first field-name column because of a UTF-8 BOM bug in
      ``SHAW_TRANERT_CUS_mapping.csv``).
    * For each ``output_files[].rules`` path that points to a committed
      JSON file, the corresponding ``<FILETYPE>_Rules`` sheet is built
      by walking the JSON's ``rules:`` array and reverse-mapping each
      rule entry to a BA-style workbook row (using the rule's
      ``source_template_rule_type`` to drive the reverse mapping).
    * Hand-authored rules without ``source_template_rule_type`` (today:
      ``SHAW_TRANERT_CUS_rules.json::R028B`` — the
      ``cross_row:sequential`` countdown rule with the engine-native
      ``sequence_field``/``start``/``step`` shape rather than the
      converter's ``target_field``/``value`` shape) are SKIPPED in the
      workbook. The committed JSON keeps them; the regression test
      explicitly tolerates the resulting structural drift on those
      specific rule IDs (documented as EC-S9 carve-out).
    * For multi-record umbrella YAMLs, the discriminator block and each
      ``record_types.<name>`` entry drive a ``MultiRecord_<FILETYPE>``
      sheet and the per-record-type ``<FT>_<LAYOUT>_Mapping`` /
      ``<FT>_<LAYOUT>_Rules`` sheets via the same reverse-engineer
      flows.
    * Cross-type-rules overlays (``cross_type_rules:`` in the umbrella
      YAML) drive the EC-S8 ``CrossTypeRules_<FILETYPE>`` sheet.
    * Reconciliation YAMLs under
      ``config/e2e/sources/SHAW/reconciliation/`` drive the
      ``Reconciliation_<FILETYPE>`` sheets verbatim (EC-S1 shape).
    * Mapping / rules paths whose target file does NOT exist on disk
      (typical: SHAW input files, CDSTRANS_*, CONTACT_*, P327 in the
      current SHAW state) emit a single ``TODO_FIELD_1`` / ``TODO_RULE_1``
      placeholder row matching the EC-S1 stub convention. These are
      intentional BA-fill-in markers; the emitter writes them to disk
      as well-formed TODO-stub JSONs.

After EC-S9 runs, ``valdo onboard-source --check`` against committed
SHAW state reports drift ONLY on:
    1. ``metadata.created_date`` / ``metadata.last_modified`` (EC-S10
       will land deterministic timestamps to close this gap).
    2. ``SHAW_TRANERT_CUS_rules.json`` rule count (56 vs 57) due to the
       hand-authored R028B carve-out documented above.
    3. The 34 TODO-stub mappings + rules listed in SHAW.yml whose
       backing JSONs are committed alongside this regeneration so the
       artefact COUNT matches 65 of 65.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import yaml
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

# ---------------------------------------------------------------------------
# Paths anchored to repo root (this file lives in scripts/).
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"
MAPPINGS_DIR = CONFIG_DIR / "mappings"
RULES_DIR = CONFIG_DIR / "rules"
SHAW_YML = CONFIG_DIR / "e2e" / "sources" / "SHAW.yml"
SHAW_RECONCILIATION_DIR = CONFIG_DIR / "e2e" / "sources" / "SHAW" / "reconciliation"
BA_CSV_ROOT = REPO_ROOT / "mappings" / "csv"
TEMPLATES_DIR = REPO_ROOT / "templates"

# ---------------------------------------------------------------------------
# Schema columns (mirrored from src/onboarding/workbook_schema.py — kept here
# as a single source of truth to avoid a forward import problem when this
# script is invoked from a fresh checkout).
# ---------------------------------------------------------------------------

SOURCE_COLUMNS = [
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
]

INPUT_FILES_COLUMNS = [
    "file_type",
    "glob",
    "mapping_sheet",
    "target_staging_table",
    "thresholds_max_errors",
]

OUTPUT_FILES_COLUMNS = [
    "file_type",
    "glob",
    "mapping_sheet",
    "rules_sheet",
    "tolerance_max_errors",
    "tolerance_max_error_pct",
    "tolerance_ignore_fields",
]

MULTI_RECORD_COLUMNS = [
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

RECONCILIATION_COLUMNS = [
    "record_type_name",
    "key_columns",
    "staging_table",
    "predicate",
    "ignored_fields",
    "assertions",
    "expected_sql_override",
]

# CrossTypeRules_<FILETYPE> sheet column set (EC-S8). Mirrors
# src/onboarding/workbook_schema.CROSS_TYPE_RULES_REQUIRED_COLUMNS plus the
# optional non-canonical columns the engine's CrossTypeRule model unpacks
# for rule types beyond ``header_trailer_count`` (e.g. ``header_field``,
# ``sum_field``, ``sum_of``). Optional columns may be left blank — the
# reader records non-blank cells into CrossTypeRuleRow.extra and the
# emitter passes them through to the umbrella YAML verbatim.
CROSS_TYPE_RULES_COLUMNS = [
    "rule_id",
    "check",
    "record_type",
    "trailer_field",
    "count_of",
    "allow_empty_batch",
    "severity",
    "message",
    "enabled",
    "header_field",
    "detail_field",
    "sum_field",
    "sum_of",
    "when_type",
    "requires_type",
    "expected_order",
    "exactly",
]

MAPPING_COLUMNS = [
    "Field Name",
    "Data Type",
    "Position",
    "Length",
    "Target Name",
    "Required",
    "Format",
    "Transformation",
    "Valid Values",
    "Description",
]

RULES_COLUMNS = [
    "Rule ID",
    "Rule Name",
    "Field",
    "Rule Type",
    "Severity",
    "Enabled",
    "Message",
    "Expected / Values",
]

HEADER_FILL = PatternFill(start_color="FFD9E1F2", end_color="FFD9E1F2", fill_type="solid")
HEADER_FONT = Font(bold=True)

# Excel hard-limit. openpyxl warns at write time; we shorten preemptively
# while preserving the trailing _Mapping / _Rules suffix.
EXCEL_SHEET_NAME_MAX = 31


def _shorten_sheet_name(name: str) -> str:
    """Trim ``name`` to Excel's 31-char sheet-name limit while keeping the
    trailing suffix (``_Mapping`` / ``_Rules`` / etc) intact. The middle of
    the stem is truncated and a single ``~`` marker inserted so the BA can
    tell at a glance the name was shortened.
    """
    if len(name) <= EXCEL_SHEET_NAME_MAX:
        return name
    for suffix in ("_Mapping", "_Rules"):
        if name.endswith(suffix):
            stem = name[: -len(suffix)]
            keep = EXCEL_SHEET_NAME_MAX - len(suffix) - 1  # -1 for the ~ marker
            if keep < 4:
                return name[:EXCEL_SHEET_NAME_MAX]
            # Drop characters from the middle of the stem.
            head = stem[: keep // 2]
            tail = stem[-(keep - len(head)):]
            return f"{head}~{tail}{suffix}"
    return name[:EXCEL_SHEET_NAME_MAX]


# ---------------------------------------------------------------------------
# Generic sheet writers.
# ---------------------------------------------------------------------------


def _write_header(ws, headers: list[str]) -> None:
    """Write a styled header row + auto-size the columns to a comfortable width."""
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        col_letter = get_column_letter(col_idx)
        ws.column_dimensions[col_letter].width = max(14, min(40, len(header) + 4))


def _write_rows(ws, headers: list[str], rows: list[dict[str, Any]], start_row: int = 2) -> None:
    """Append ``rows`` to ``ws`` aligned to ``headers``. Missing keys -> blank."""
    for row_offset, row in enumerate(rows):
        for col_idx, header in enumerate(headers, start=1):
            value = row.get(header, "")
            if value is None:
                value = ""
            ws.cell(row=start_row + row_offset, column=col_idx, value=value)


def _add_source_sheet(wb: Workbook, source_row: dict[str, Any]) -> None:
    """Source sheet is single-row key/value pairs. Row 1 = headers, row 2 = values."""
    ws = wb.create_sheet("Source")
    _write_header(ws, SOURCE_COLUMNS)
    _write_rows(ws, SOURCE_COLUMNS, [source_row])


def _add_input_files_sheet(wb: Workbook, rows: list[dict[str, Any]]) -> None:
    ws = wb.create_sheet("InputFiles")
    _write_header(ws, INPUT_FILES_COLUMNS)
    _write_rows(ws, INPUT_FILES_COLUMNS, rows)


def _add_output_files_sheet(wb: Workbook, rows: list[dict[str, Any]]) -> None:
    ws = wb.create_sheet("OutputFiles")
    _write_header(ws, OUTPUT_FILES_COLUMNS)
    _write_rows(ws, OUTPUT_FILES_COLUMNS, rows)


def _add_multi_record_sheet(wb: Workbook, sheet_name: str, rows: list[dict[str, Any]]) -> None:
    ws = wb.create_sheet(_shorten_sheet_name(sheet_name))
    _write_header(ws, MULTI_RECORD_COLUMNS)
    _write_rows(ws, MULTI_RECORD_COLUMNS, rows)


def _add_reconciliation_sheet(wb: Workbook, sheet_name: str, rows: list[dict[str, Any]]) -> None:
    ws = wb.create_sheet(_shorten_sheet_name(sheet_name))
    _write_header(ws, RECONCILIATION_COLUMNS)
    _write_rows(ws, RECONCILIATION_COLUMNS, rows)


def _add_cross_type_rules_sheet(
    wb: Workbook, sheet_name: str, rows: list[dict[str, Any]]
) -> None:
    """Add an optional CrossTypeRules_<FILETYPE> sheet (EC-S8)."""
    ws = wb.create_sheet(_shorten_sheet_name(sheet_name))
    _write_header(ws, CROSS_TYPE_RULES_COLUMNS)
    _write_rows(ws, CROSS_TYPE_RULES_COLUMNS, rows)


def _add_mapping_sheet(wb: Workbook, sheet_name: str, rows: list[dict[str, Any]]) -> None:
    ws = wb.create_sheet(_shorten_sheet_name(sheet_name))
    _write_header(ws, MAPPING_COLUMNS)
    _write_rows(ws, MAPPING_COLUMNS, rows)


def _add_rules_sheet(wb: Workbook, sheet_name: str, rows: list[dict[str, Any]]) -> None:
    ws = wb.create_sheet(_shorten_sheet_name(sheet_name))
    _write_header(ws, RULES_COLUMNS)
    _write_rows(ws, RULES_COLUMNS, rows)


# ---------------------------------------------------------------------------
# CSV / JSON readers for the SHAW worked example.
# ---------------------------------------------------------------------------


def _read_ba_csv(csv_path: Path) -> list[dict[str, Any]]:
    """Read a BA-friendly CSV (mappings/csv/...) and return list-of-dicts.

    Missing files return ``[]`` — caller substitutes a TODO placeholder.

    Note: kept for backwards-compat with the EC-S1 build flow; EC-S9
    prefers ``_reverse_engineer_mapping_rows_from_json`` /
    ``_reverse_engineer_rules_rows_from_json`` which read the committed
    JSON artefacts directly (immune to UTF-8 BOM bugs in the BA CSVs).
    """
    if not csv_path.exists():
        return []
    # ``utf-8-sig`` strips a leading BOM so DictReader keys don't get a
    # phantom ``﻿`` prefix on the first column (the EC-S9 bug that
    # silently blanked TRANERT_CUS Field Name on every row).
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        return [dict(row) for row in reader]


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# EC-S9: Reverse-engineer mapping/rules workbook rows from committed JSONs.
# ---------------------------------------------------------------------------

# Inverse map of ``TemplateConverter._normalize_data_type``. The forward
# converter normalises any of {String,str,text,varchar,char} → "string",
# {Number,Numeric,num,decimal,float} → "decimal", etc. For reverse
# engineering we pick a single BA-friendly label per canonical type — the
# converter will round-trip any of the synonyms to the same canonical, so
# our choice here is purely about which label looks natural to a BA.
_REVERSE_DATA_TYPE: dict[str, str] = {
    "string": "String",
    "decimal": "Numeric",
    "integer": "Integer",
    "date": "Date",
    "boolean": "Boolean",
}


def _reverse_data_type(data_type: str) -> str:
    """Convert a committed JSON ``data_type`` to a BA-friendly workbook label.

    The forward direction is :meth:`TemplateConverter._normalize_data_type`
    which collapses synonyms; reversing is a single lookup with a sensible
    default to ``String`` for unknown labels.

    Args:
        data_type: The committed JSON's ``data_type`` value.

    Returns:
        The BA-friendly label that round-trips through the converter.
    """
    return _REVERSE_DATA_TYPE.get((data_type or "").lower(), "String")


def _reverse_engineer_mapping_rows_from_json(
    json_path: Path,
) -> list[dict[str, Any]]:
    """Build a list of workbook-mapping rows from a committed mapping JSON.

    Walks the JSON's ``fields:`` array and emits one dict per field with
    keys matching :data:`MAPPING_COLUMNS`. The fed dicts will round-trip
    cleanly through :class:`TemplateConverter._convert_dataframe` so the
    EC-S6 ``valdo onboard-source --check`` pipeline sees no structural
    drift (modulo the ``metadata`` block, which the check strips).

    Conventions:

        * ``Field Name``     <- JSON ``name``
        * ``Data Type``      <- inverse of converter's normaliser
        * ``Position`` /
          ``Length``         <- JSON ``position`` / ``length`` (or "")
        * ``Target Name``    <- JSON ``target_name`` (or "")
        * ``Required``       <- ``"Yes"`` / ``"No"`` (forward converter
                                 accepts Y/Yes/True/1; we pick ``Yes`` for
                                 BA-readability).
        * ``Format``         <- JSON ``format`` (or "")
        * ``Transformation`` <- blank: the converter always adds
                                 ``[{"type": "trim"}]`` so reverse-engineering
                                 from any committed JSON that has only the
                                 default ``trim`` transform yields a blank
                                 cell (round-trip identity).
        * ``Valid Values``   <- pipe-joined JSON ``valid_values`` (or "").
                                 Forward converter splits on pipe-or-comma;
                                 we always emit pipes for unambiguity.
        * ``Description``    <- JSON ``description`` (or "")

    Args:
        json_path: Path to a committed mapping JSON file.

    Returns:
        List of row dicts ready for :func:`_write_rows`.
    """
    data = _load_json(json_path)
    rows: list[dict[str, Any]] = []
    for field in data.get("fields", []) or []:
        valid_values = field.get("valid_values") or []
        rows.append(
            {
                "Field Name": field.get("name", "") or "",
                "Data Type": _reverse_data_type(field.get("data_type", "")),
                "Position": field.get("position", "") if field.get("position") is not None else "",
                "Length": field.get("length", "") if field.get("length") is not None else "",
                "Target Name": field.get("target_name", "") or "",
                "Required": "Yes" if field.get("required") else "No",
                "Format": field.get("format", "") or "",
                "Transformation": "",
                "Valid Values": "|".join(str(v) for v in valid_values) if valid_values else "",
                "Description": field.get("description", "") or "",
            }
        )
    return rows


def _reverse_engineer_rules_row_from_engine_rule(
    rule: dict[str, Any],
) -> dict[str, Any] | None:
    """Reverse one engine ``rules[]`` entry into a workbook RulesSheet row.

    Uses ``source_template_rule_type`` as the authoritative reverse-key
    because that field is exactly the BA-typed ``Rule Type`` label the
    converter received before normalisation. Rules without it
    (hand-authored, e.g. the SHAW_TRANERT_CUS R028B countdown rule with
    engine-native ``sequence_field``/``start``/``step`` shape) cannot be
    expressed in BA columns and are returned as ``None`` — the caller
    drops them from the workbook. This produces a documented carve-out
    in the EC-S9 regression test (the committed JSON has 57 rules but
    the workbook → emitted JSON has 56).

    Args:
        rule: One element of the committed rules JSON's ``rules:`` array.

    Returns:
        A workbook-row dict keyed by :data:`RULES_COLUMNS`, or ``None``
        when the rule has no ``source_template_rule_type`` (hand-authored
        engine-native rule that cannot be expressed in BA columns).
    """
    rule_type_text = rule.get("source_template_rule_type")
    if not rule_type_text:
        return None

    rule_type_text = str(rule_type_text)
    enabled = rule.get("enabled", True)
    enabled_text = "Yes" if enabled else "No"

    # Field reverse-mapping: cross-row rules with ``key_field``/``target_field``
    # / ``fields`` need special handling so the converter's forward path
    # reassembles them correctly (see ``BARulesTemplateConverter._convert_row``
    # which parses ``KEY>TARGET`` / ``F1|F2`` syntaxes).
    if rule_type_text.startswith("cross_row:"):
        if "key_field" in rule and "target_field" in rule:
            field = f"{rule['key_field']}>{rule['target_field']}"
        elif "fields" in rule:
            field = "|".join(rule["fields"])
        else:
            field = rule.get("field", "") or ""
    elif rule.get("type") == "cross_field":
        # Cross-field forward path: field = left_field, expected = "<op> <right>"
        field = rule.get("left_field", "") or ""
    else:
        field = rule.get("field", "") or ""

    # Expected / Values reverse-mapping by rule type.
    expected = ""
    if rule_type_text == "allowed values" or rule_type_text == "valid_values":
        values = rule.get("values") or []
        expected = "|".join(str(v) for v in values)
    elif rule_type_text == "range":
        # Forward: expected = "min..max"; engine fields: min, max.
        min_v = rule.get("min", "")
        max_v = rule.get("max", "")
        expected = f"{min_v if min_v != '' else ''}..{max_v if max_v != '' else ''}"
    elif rule_type_text == "length":
        # Forward: expected = "min..max"; engine fields: min_length, max_length.
        min_v = rule.get("min_length", "")
        max_v = rule.get("max_length", "")
        expected = f"{min_v if min_v != '' else ''}..{max_v if max_v != '' else ''}"
    elif rule_type_text == "regex":
        expected = rule.get("pattern", "") or ""
    elif rule_type_text == "date format":
        expected = rule.get("expected_format", "") or ""
    elif rule_type_text == "date_format":
        expected = rule.get("format", "") or ""
    elif rule_type_text in ("min_value", "max_value", "exact_length", "min_length"):
        expected = str(rule.get("value", "")) if rule.get("value") != "" else ""
    elif rule.get("type") == "cross_field":
        # Forward expected = "<op> <right_field>"
        op = rule.get("operator", "")
        rf = rule.get("right_field", "")
        expected = f"{op} {rf}".strip()
    elif rule_type_text.startswith("cross_row:"):
        v = rule.get("value")
        if v is not None and v != "":
            expected = str(v)

    return {
        "Rule ID": rule.get("id", "") or "",
        "Rule Name": rule.get("name", "") or "",
        "Field": field,
        "Rule Type": rule_type_text,
        "Severity": rule.get("severity", "") or "",
        "Enabled": enabled_text,
        "Message": rule.get("message", "") or "",
        "Expected / Values": expected,
    }


def _reverse_engineer_rules_rows_from_json(
    json_path: Path,
) -> list[dict[str, Any]]:
    """Build workbook-rules rows from a committed rules JSON.

    Walks the JSON's ``rules:`` array and emits one dict per rule whose
    ``source_template_rule_type`` allows BA-column round-trip. Engine-native
    hand-authored rules are SILENTLY DROPPED (returns no row for them) —
    the workbook cannot express them and the test allows the resulting
    drift on the affected ``rules`` list length.

    Args:
        json_path: Path to a committed rules JSON file.

    Returns:
        List of row dicts ready for :func:`_write_rows`.
    """
    data = _load_json(json_path)
    rows: list[dict[str, Any]] = []
    for rule in data.get("rules", []) or []:
        row = _reverse_engineer_rules_row_from_engine_rule(rule)
        if row is not None:
            rows.append(row)
    return rows


def _mapping_rows_for_path(mapping_repo_path: str, file_type: str) -> list[dict[str, Any]]:
    """Resolve a SHAW.yml mapping path to workbook rows.

    If the committed file exists on disk, reverse-engineer rows from it
    (EC-S9). Otherwise fall back to a single TODO placeholder so the
    workbook stays schema-valid and the BA can fill the layout in later.

    Args:
        mapping_repo_path: The repo-relative ``mapping:`` cell from
            SHAW.yml (e.g. ``"config/mappings/SHAW_CDSTRANS_EFB.json"``).
        file_type: The output / input file type (drives the TODO message).

    Returns:
        Workbook row dicts.
    """
    if not mapping_repo_path:
        return _todo_mapping_rows(file_type)
    abs_path = REPO_ROOT / mapping_repo_path
    if not abs_path.exists():
        return _todo_mapping_rows(file_type)
    return _reverse_engineer_mapping_rows_from_json(abs_path)


def _rules_rows_for_path(rules_repo_path: str, file_type: str) -> list[dict[str, Any]]:
    """Resolve a SHAW.yml rules path to workbook rows.

    Same pattern as :func:`_mapping_rows_for_path`. ``rules_repo_path=""``
    (BA opted out of rules for this file) returns ``[]`` so the caller
    can skip emitting a Rules sheet entirely. ``rules_repo_path`` that
    points to a non-existent file returns a TODO placeholder row.

    Args:
        rules_repo_path: The repo-relative ``rules:`` cell from SHAW.yml
            or the umbrella YAML's per-record-type entry.
        file_type: Drives the TODO message.

    Returns:
        Workbook row dicts, or ``[]`` when ``rules_repo_path`` is empty.
    """
    if not rules_repo_path:
        return []
    abs_path = REPO_ROOT / rules_repo_path
    if not abs_path.exists():
        return _todo_rules_rows(file_type)
    return _reverse_engineer_rules_rows_from_json(abs_path)


# ---------------------------------------------------------------------------
# Per-source builders.
# ---------------------------------------------------------------------------


def _todo_mapping_rows(file_type: str) -> list[dict[str, Any]]:
    """Return a single-row placeholder for a mapping sheet whose source spec
    has not been authored yet. Keeps the workbook schema-valid while still
    signalling to the BA that real content is owed.
    """
    return [
        {
            "Field Name": f"TODO_FIELD_1_{file_type}",
            "Data Type": "String",
            "Position": 1,
            "Length": 1,
            "Target Name": "todo_field_1",
            "Required": "Yes",
            "Format": "",
            "Transformation": "",
            "Valid Values": "",
            "Description": (
                f"TODO(mapping-pending): {file_type} layout not yet authored. "
                f"Replace this row with real fields before onboarding."
            ),
        }
    ]


def _todo_rules_rows(file_type: str) -> list[dict[str, Any]]:
    return [
        {
            "Rule ID": "R001",
            "Rule Name": f"TODO {file_type} rule",
            "Field": f"TODO_FIELD_1_{file_type}",
            "Rule Type": "not_empty",
            "Severity": "error",
            "Enabled": "Yes",
            "Message": f"TODO(rules-pending): {file_type} rules not yet authored.",
            "Expected / Values": "",
        }
    ]


def _classify_atoctran_record_type(rt_key: str) -> str:
    """Map ATOCTRAN rt_<code> key to canonical record-type-name + cardinality."""
    # ATOCTRAN has no batch-header; all detail rows. The umbrella sets
    # expect: any; we encode this as zero_or_one_per_driver_row for the
    # canonical workbook (matches the SHAW reconciliation convention).
    return "many_per_driver_row"


def _input_files_from_shaw_yml(src: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in src.get("input_files", []):
        ftype = entry["file_type"]
        rows.append(
            {
                "file_type": ftype,
                "glob": entry["glob"],
                "mapping_sheet": f"{ftype}_Mapping",
                "target_staging_table": entry["target_staging_table"],
                "thresholds_max_errors": "",  # default 0
            }
        )
    return rows


def _output_files_from_shaw_yml(src: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in src.get("output_files", []):
        ftype = entry["file_type"]
        mapping_path = entry.get("mapping", "")
        is_multi_record = mapping_path.endswith(".yaml")
        if is_multi_record:
            mapping_sheet = "(umbrella)"
            rules_sheet = "(umbrella)"
        else:
            mapping_sheet = f"{ftype}_Mapping"
            rules_sheet = f"{ftype}_Rules" if entry.get("rules") else ""
        rows.append(
            {
                "file_type": ftype,
                "glob": entry["glob"],
                "mapping_sheet": mapping_sheet,
                "rules_sheet": rules_sheet,
                "tolerance_max_errors": "",
                "tolerance_max_error_pct": "",
                "tolerance_ignore_fields": "",
            }
        )
    return rows


def _multi_record_rows_from_umbrella(
    file_type: str, umbrella: dict[str, Any]
) -> list[dict[str, Any]]:
    """Walk an umbrella YAML's record_types and emit one MultiRecord sheet row
    per record type."""
    discriminator = umbrella.get("discriminator", {})
    disc_field = discriminator.get("field", "")
    disc_pos = discriminator.get("position", "")
    disc_len = discriminator.get("length", "")

    rows: list[dict[str, Any]] = []
    for rt_name, rt_cfg in umbrella.get("record_types", {}).items():
        if rt_cfg.get("position") == "first":
            match_kind = "position_first"
            match_value = "first"
        else:
            match_value = str(rt_cfg.get("match", ""))
            match_kind = (
                "discriminator_in" if "," in match_value else "discriminator_equals"
            )

        # Mapping sheet name = derive from the mapping JSON filename so it
        # round-trips. Use the umbrella's mapping path basename stem,
        # converted to the per-record-type sheet naming convention. We also
        # apply the Excel 31-char shortener so the referenced sheet name
        # actually matches the tab the BA sees.
        mapping_path = rt_cfg.get("mapping", "")
        rules_path = rt_cfg.get("rules", "")
        mapping_sheet = _shorten_sheet_name(
            _mapping_path_to_sheet_name(mapping_path, file_type, rt_name)
        )
        rules_sheet_raw = _rules_path_to_sheet_name(rules_path, file_type, rt_name)
        rules_sheet = _shorten_sheet_name(rules_sheet_raw) if rules_sheet_raw else ""

        cardinality = _expect_to_cardinality(rt_cfg.get("expect", "any"))
        # batch_header in TRANERT is one_per_driver_row by convention.
        if rt_cfg.get("position") == "first":
            cardinality = "one_per_driver_row"

        rows.append(
            {
                "record_type_name": rt_name,
                "discriminator_field": disc_field,
                "discriminator_position": disc_pos,
                "discriminator_length": disc_len,
                "match_kind": match_kind,
                "match_value": match_value,
                "mapping_sheet": mapping_sheet,
                "rules_sheet": rules_sheet,
                "cardinality": cardinality,
            }
        )
    return rows


def _expect_to_cardinality(expect: str) -> str:
    """Translate umbrella ``expect:`` clause to workbook ``cardinality``."""
    mapping = {
        "at_least_one": "one_per_driver_row",
        "any": "many_per_driver_row",
        "none": "zero_or_one_per_driver_row",
        "exactly_one": "one_per_driver_row",
    }
    return mapping.get(expect, "many_per_driver_row")


def _mapping_path_to_sheet_name(mapping_path: str, file_type: str, rt_name: str) -> str:
    """Convert a config/mappings/SHAW_TRANERT_BATCH_HEADER_mapping.json path to
    the workbook sheet name ``TRANERT_BATCH_HEADER_Mapping``.
    """
    if not mapping_path:
        return f"{file_type}_{rt_name.upper()}_Mapping"
    stem = Path(mapping_path).stem  # SHAW_TRANERT_BATCH_HEADER_mapping
    # Strip leading SOURCE_ + trailing _mapping
    parts = stem.split("_")
    if parts[0].upper() == "SHAW":
        parts = parts[1:]
    if parts and parts[-1].lower() == "mapping":
        parts = parts[:-1]
    return "_".join(parts) + "_Mapping"


def _rules_path_to_sheet_name(rules_path: str, file_type: str, rt_name: str) -> str:
    if not rules_path:
        return ""
    stem = Path(rules_path).stem
    parts = stem.split("_")
    if parts[0].upper() == "SHAW":
        parts = parts[1:]
    if parts and parts[-1].lower() == "rules":
        parts = parts[:-1]
    return "_".join(parts) + "_Rules"


def _cross_type_rules_rows_from_umbrella(
    umbrella: dict[str, Any],
) -> list[dict[str, Any]]:
    """Convert a committed umbrella YAML's ``cross_type_rules`` array into
    workbook-row dicts for the EC-S8 ``CrossTypeRules_<FILETYPE>`` sheet.

    Each emitted dict carries the canonical CrossTypeRules columns plus
    any non-canonical engine fields the original rule specified
    (``header_field``, ``sum_of``, etc.). ``rule_id`` is generated
    sequentially (``CT001``, ``CT002``, ...) since the committed YAML
    overlay does not carry rule IDs (operators rely on order alone).

    Args:
        umbrella: Parsed umbrella YAML dict.

    Returns:
        List of row-dicts keyed by ``CROSS_TYPE_RULES_COLUMNS`` entries.
        Empty list when the umbrella carries no ``cross_type_rules:``
        block (so the sheet is omitted entirely).
    """
    rules = umbrella.get("cross_type_rules") or []
    return [
        _materialise_cross_type_rule_row(rule, idx)
        for idx, rule in enumerate(rules, start=1)
        if isinstance(rule, dict)
    ]


def _materialise_cross_type_rule_row(
    rule: dict[str, Any], rule_id_idx: int
) -> dict[str, Any]:
    """Build one CrossTypeRules sheet row dict from a committed umbrella
    YAML ``cross_type_rules`` array entry. Helper for
    :func:`_cross_type_rules_rows_from_umbrella` so the iteration stays
    flat and readable.
    """
    row: dict[str, Any] = {col: "" for col in CROSS_TYPE_RULES_COLUMNS}
    row["rule_id"] = f"CT{rule_id_idx:03d}"
    row["enabled"] = True
    for key, value in rule.items():
        if key in {"sum_of", "expected_order"} and isinstance(value, list):
            row[key] = "|".join(str(v) for v in value)
        elif isinstance(value, bool):
            row[key] = value
        elif value is None:
            row[key] = ""
        else:
            row[key] = value
    return row


def _reconciliation_rows_from_yaml(spec: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    assertions = spec.get("assertions") or []
    assertion_strs = [a.get("expr", "") for a in assertions if a.get("expr")]
    # Assertions live once on the file, not per-record-type. Place them on the
    # first row so the BA can find them; subsequent rows leave assertions blank.
    assertion_pipe = "|".join(assertion_strs)

    for idx, (rt_name, rt_cfg) in enumerate(spec.get("record_types", {}).items()):
        keys = rt_cfg.get("key", [])
        ignored = rt_cfg.get("ignored_fields", []) or []
        rows.append(
            {
                "record_type_name": rt_name,
                "key_columns": "|".join(keys),
                "staging_table": rt_cfg.get("staging_table", ""),
                "predicate": rt_cfg.get("predicate", ""),
                "ignored_fields": "|".join(ignored),
                "assertions": assertion_pipe if idx == 0 else "",
                "expected_sql_override": rt_cfg.get("expected_sql", ""),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Source-sheet row assemblers.
# ---------------------------------------------------------------------------


def _shaw_source_row(src: dict[str, Any]) -> dict[str, Any]:
    gates = src.get("gates", {})
    return {
        "source_code": src.get("source", "SHAW"),
        "schema_version": src.get("schema_version", 1),
        "release_tag": src.get("release_tag", ""),
        "description": src.get("description", ""),
        "staging_schema": src.get("staging_schema", ""),
        "output_root": src.get("output_root", ""),
        "java_load_script": src.get("java_scripts", {}).get("load", ""),
        "java_generate_script": src.get("java_scripts", {}).get("generate", ""),
        "gate_load_blocking": gates.get("load_step", {}).get("blocking", True),
        "gate_load_invoke_java": gates.get("load_step", {}).get("invoke_java", True),
        "gate_f2s_blocking": gates.get("file_to_staging", {}).get("blocking", True),
        "gate_generate_blocking": gates.get("generate_step", {}).get("blocking", False),
        "gate_generate_invoke_java": gates.get("generate_step", {}).get("invoke_java", False),
        "gate_l1_blocking": gates.get("L1_structural", {}).get("blocking", True),
        "gate_l2b_blocking": gates.get("L2b_sql_truth", {}).get("blocking", True),
        "gate_l3_blocking": gates.get("L3_baseline_diff", {}).get("blocking", True),
        "gate_mr_report_blocking": gates.get("multi_record_report", {}).get("blocking", False),
    }


def _blank_source_row() -> dict[str, Any]:
    """Default-populated Source row for the canonical template — values match
    the validator's documented defaults."""
    return {
        "source_code": "NEW_SOURCE",
        "schema_version": 1,
        "release_tag": "2026.M0X",
        "description": "Describe this source.",
        "staging_schema": "APP_INT",
        "output_root": "/app/software/APPS/ftp/input/new_source",
        "java_load_script": "/app/software/APPS/valdo/scripts/wrappers/load_NEW_SOURCE.sh",
        "java_generate_script": "",
        "gate_load_blocking": True,
        "gate_load_invoke_java": True,
        "gate_f2s_blocking": True,
        "gate_generate_blocking": False,
        "gate_generate_invoke_java": False,
        "gate_l1_blocking": True,
        "gate_l2b_blocking": True,
        "gate_l3_blocking": True,
        "gate_mr_report_blocking": False,
    }


# ---------------------------------------------------------------------------
# Top-level builders.
# ---------------------------------------------------------------------------


def build_blank_template(out_path: Path) -> None:
    """Build the canonical empty template at ``out_path``."""
    wb = Workbook()
    # Remove the auto-created default sheet.
    default = wb.active
    wb.remove(default)

    _add_source_sheet(wb, _blank_source_row())

    # InputFiles + OutputFiles: one example placeholder row so BAs see the
    # shape, but flagged as TODO.
    _add_input_files_sheet(
        wb,
        [
            {
                "file_type": "EXAMPLE_INPUT",
                "glob": "example_*.txt",
                "mapping_sheet": "EXAMPLE_INPUT_Mapping",
                "target_staging_table": "NEW_SOURCE_EXAMPLE",
                "thresholds_max_errors": "",
            }
        ],
    )
    _add_output_files_sheet(
        wb,
        [
            {
                "file_type": "EXAMPLE_OUTPUT",
                "glob": "example_output_*.txt",
                "mapping_sheet": "EXAMPLE_OUTPUT_Mapping",
                "rules_sheet": "EXAMPLE_OUTPUT_Rules",
                "tolerance_max_errors": "",
                "tolerance_max_error_pct": "",
                "tolerance_ignore_fields": "",
            },
            {
                "file_type": "EXAMPLE_MR_OUTPUT",
                "glob": "example_mr_output_*.txt",
                "mapping_sheet": "(umbrella)",
                "rules_sheet": "(umbrella)",
                "tolerance_max_errors": "",
                "tolerance_max_error_pct": "",
                "tolerance_ignore_fields": "",
            },
        ],
    )

    # MultiRecord example (umbrella reference for EXAMPLE_MR_OUTPUT above).
    # We pre-shorten the sheet-name cells here so they round-trip exactly to
    # the (also shortened) tab names emitted below.
    mr_header_mapping = _shorten_sheet_name("EXAMPLE_MR_OUTPUT_BATCH_HEADER_Mapping")
    mr_header_rules = _shorten_sheet_name("EXAMPLE_MR_OUTPUT_BATCH_HEADER_Rules")
    mr_detail_mapping = _shorten_sheet_name("EXAMPLE_MR_OUTPUT_DETAIL_Mapping")
    mr_detail_rules = _shorten_sheet_name("EXAMPLE_MR_OUTPUT_DETAIL_Rules")
    _add_multi_record_sheet(
        wb,
        "MultiRecord_EXAMPLE_MR_OUTPUT",
        [
            {
                "record_type_name": "batch_header",
                "discriminator_field": "RECORD-CODE",
                "discriminator_position": 1,
                "discriminator_length": 3,
                "match_kind": "position_first",
                "match_value": "first",
                "mapping_sheet": mr_header_mapping,
                "rules_sheet": mr_header_rules,
                "cardinality": "one_per_driver_row",
            },
            {
                "record_type_name": "rt_detail",
                "discriminator_field": "RECORD-CODE",
                "discriminator_position": 1,
                "discriminator_length": 3,
                "match_kind": "discriminator_equals",
                "match_value": "DTL",
                "mapping_sheet": mr_detail_mapping,
                "rules_sheet": mr_detail_rules,
                "cardinality": "many_per_driver_row",
            },
        ],
    )

    # Reconciliation example.
    _add_reconciliation_sheet(
        wb,
        "Reconciliation_EXAMPLE_OUTPUT",
        [
            {
                "record_type_name": "(flat)",
                "key_columns": "ACCOUNT-ID",
                "staging_table": "cm3_int.EXPECTED_EXAMPLE_TBL",
                "predicate": "",
                "ignored_fields": "FILE_CREATE_TS",
                "assertions": "",
                "expected_sql_override": "",
            }
        ],
    )

    # Mapping + Rules sheets for the example output (single layout each, plus
    # the multi-record record-type sheets). Use one TODO row each.
    for sheet, rows in [
        ("EXAMPLE_INPUT_Mapping", _todo_mapping_rows("EXAMPLE_INPUT")),
        ("EXAMPLE_OUTPUT_Mapping", _todo_mapping_rows("EXAMPLE_OUTPUT")),
        (
            "EXAMPLE_MR_OUTPUT_BATCH_HEADER_Mapping",
            _todo_mapping_rows("EXAMPLE_MR_OUTPUT_BATCH_HEADER"),
        ),
        (
            "EXAMPLE_MR_OUTPUT_DETAIL_Mapping",
            _todo_mapping_rows("EXAMPLE_MR_OUTPUT_DETAIL"),
        ),
    ]:
        _add_mapping_sheet(wb, sheet, rows)

    for sheet, rows in [
        ("EXAMPLE_OUTPUT_Rules", _todo_rules_rows("EXAMPLE_OUTPUT")),
        (
            "EXAMPLE_MR_OUTPUT_BATCH_HEADER_Rules",
            _todo_rules_rows("EXAMPLE_MR_OUTPUT_BATCH_HEADER"),
        ),
        (
            "EXAMPLE_MR_OUTPUT_DETAIL_Rules",
            _todo_rules_rows("EXAMPLE_MR_OUTPUT_DETAIL"),
        ),
    ]:
        _add_rules_sheet(wb, sheet, rows)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(out_path))


def build_shaw_workbook(out_path: Path) -> None:
    """Build the SHAW worked-example workbook at ``out_path``.

    EC-S9 rewrite: the workbook is now a 1:1 reverse-engineering of the
    currently committed ``config/mappings/`` + ``config/rules/`` + the
    SHAW umbrella YAMLs + the reconciliation YAMLs. Where a SHAW.yml
    mapping/rules path points to a file that does not exist on disk
    (typical for the TODO-state SHAW input files, CDSTRANS_*, CONTACT_*,
    P327) we emit a single ``TODO_FIELD_1`` / ``TODO_RULE_1`` placeholder
    row preserving the EC-S1 stub convention.
    """
    src = _load_yaml(SHAW_YML)
    wb = Workbook()
    wb.remove(wb.active)

    # --- 1. Source / InputFiles / OutputFiles ---
    _add_source_sheet(wb, _shaw_source_row(src))
    _add_input_files_sheet(wb, _input_files_from_shaw_yml(src))
    _add_output_files_sheet(wb, _output_files_from_shaw_yml(src))

    # --- 2. MultiRecord sheets for each .yaml-mapping output. ---
    multi_record_record_types: dict[str, list[str]] = {}
    # Cache committed umbrella YAMLs so the cross-type-rules pass (EC-S8)
    # below can re-use the parse without reloading from disk.
    committed_umbrellas: dict[str, dict[str, Any]] = {}
    for entry in src.get("output_files", []):
        mapping_path = entry.get("mapping", "")
        if not mapping_path.endswith(".yaml"):
            continue
        file_type = entry["file_type"]
        umbrella_path = REPO_ROOT / mapping_path
        if not umbrella_path.exists():
            continue
        umbrella = _load_yaml(umbrella_path)
        committed_umbrellas[file_type] = umbrella
        mr_rows = _multi_record_rows_from_umbrella(file_type, umbrella)
        _add_multi_record_sheet(wb, f"MultiRecord_{file_type}", mr_rows)
        multi_record_record_types[file_type] = [r["record_type_name"] for r in mr_rows]

    # --- 2b. CrossTypeRules_<FILETYPE> sheets (EC-S8). Sheet is OPTIONAL —
    # only emitted for output files whose committed umbrella YAML carries
    # an operator-authored ``cross_type_rules:`` overlay. SHAW TRANERT has
    # one; SHAW ATOCTRAN does not. The empty case (sheet absent) yields
    # ``cross_type_rules: []`` in the emitted umbrella, preserving the
    # legacy shape. ---
    for file_type, umbrella in committed_umbrellas.items():
        ctr_rows = _cross_type_rules_rows_from_umbrella(umbrella)
        if ctr_rows:
            _add_cross_type_rules_sheet(
                wb, f"CrossTypeRules_{file_type}", ctr_rows
            )

    # --- 3. Reconciliation sheets (anything under SHAW/reconciliation/). ---
    if SHAW_RECONCILIATION_DIR.exists():
        for recon_yml in sorted(SHAW_RECONCILIATION_DIR.glob("*.yml")):
            spec = _load_yaml(recon_yml)
            file_type = spec.get("file_type") or recon_yml.stem.upper()
            recon_rows = _reconciliation_rows_from_yaml(spec)
            _add_reconciliation_sheet(wb, f"Reconciliation_{file_type}", recon_rows)

    # --- 4. Per-mapping + per-rules sheets (EC-S9 reverse-engineer flow). ---
    # 4a. Input-file mapping sheets — reverse-engineered if committed JSON
    # exists, otherwise TODO placeholder.
    for entry in src.get("input_files", []):
        ftype = entry["file_type"]
        sheet_name = f"{ftype}_Mapping"
        mapping_path = entry.get("mapping", "")
        _add_mapping_sheet(
            wb, sheet_name, _mapping_rows_for_path(mapping_path, ftype)
        )

    # 4b. Output-file mapping + rules sheets.
    for entry in src.get("output_files", []):
        ftype = entry["file_type"]
        mapping_path = entry.get("mapping", "")
        rules_path = entry.get("rules", "")
        if mapping_path.endswith(".yaml"):
            # Multi-record: emit one Mapping + one Rules sheet per record type.
            # Reverse-engineer from the per-record-type committed JSONs that
            # the umbrella's record_types[].mapping / .rules entries point
            # at; fall back to TODO for any pending layouts.
            umbrella = _load_yaml(REPO_ROOT / mapping_path)
            for rt_name, rt_cfg in umbrella.get("record_types", {}).items():
                rt_mapping_path = rt_cfg.get("mapping", "")
                rt_rules_path = rt_cfg.get("rules", "")
                rt_mapping_sheet = _mapping_path_to_sheet_name(rt_mapping_path, ftype, rt_name)
                rt_rules_sheet = _rules_path_to_sheet_name(rt_rules_path, ftype, rt_name)
                # Skip if we already added it (e.g. rt_32000 + rt_32001 share NEW1).
                if _shorten_sheet_name(rt_mapping_sheet) not in wb.sheetnames:
                    _add_mapping_sheet(
                        wb,
                        rt_mapping_sheet,
                        _mapping_rows_for_path(rt_mapping_path, f"{ftype}_{rt_name}"),
                    )
                if rt_rules_sheet and _shorten_sheet_name(rt_rules_sheet) not in wb.sheetnames:
                    rule_rows = _rules_rows_for_path(
                        rt_rules_path, f"{ftype}_{rt_name}"
                    )
                    # Empty list means "no committed JSON path was supplied" —
                    # but the umbrella always has a rules path for SHAW today,
                    # so an empty list here means TODO. Fall back to TODO.
                    if not rule_rows:
                        rule_rows = _todo_rules_rows(f"{ftype}_{rt_name}")
                    _add_rules_sheet(wb, rt_rules_sheet, rule_rows)
        else:
            sheet_name = f"{ftype}_Mapping"
            if sheet_name not in wb.sheetnames:
                _add_mapping_sheet(
                    wb, sheet_name, _mapping_rows_for_path(mapping_path, ftype)
                )
            if rules_path:
                rules_sheet_name = f"{ftype}_Rules"
                if rules_sheet_name not in wb.sheetnames:
                    rule_rows = _rules_rows_for_path(rules_path, ftype)
                    if not rule_rows:
                        rule_rows = _todo_rules_rows(ftype)
                    _add_rules_sheet(wb, rules_sheet_name, rule_rows)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(out_path))


def _ba_csv_rows_for_record_type(
    file_type: str, mapping_or_rules_path: str, kind: str
) -> list[dict[str, Any]]:
    """Locate the BA-friendly CSV under mappings/csv/shaw_<ftype>/ matching the
    JSON path; return its rows. Empty list if the CSV does not exist.

    DEPRECATED (EC-S9): superseded by
    :func:`_reverse_engineer_mapping_rows_from_json` /
    :func:`_reverse_engineer_rules_rows_from_json`, which read the
    committed JSON artefacts directly. Kept as a no-op shim so any
    out-of-tree caller (or test mock) that imports it continues to
    import cleanly. Returns ``[]`` so the caller falls back to TODO.
    """
    _ = file_type
    _ = mapping_or_rules_path
    _ = kind
    return []


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------


def main() -> int:
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    blank_path = TEMPLATES_DIR / "source_onboarding_template.xlsx"
    shaw_path = TEMPLATES_DIR / "SHAW_onboarding.xlsx"

    print(f"Building canonical template at {blank_path} ...")
    build_blank_template(blank_path)

    print(f"Building SHAW worked example at {shaw_path} ...")
    build_shaw_workbook(shaw_path)

    # Sanity-check both via the schema validator.
    from src.onboarding.workbook_schema import (  # local import to avoid hard dep on build
        assert_workbook_valid,
        validate_workbook,
    )

    for path in (blank_path, shaw_path):
        findings = validate_workbook(path)
        warnings = [f for f in findings if f.severity == "warning"]
        errors = [f for f in findings if f.severity == "error"]
        print(
            f"  {path.name}: {len(errors)} error(s), {len(warnings)} warning(s)"
        )
        if errors:
            for err in errors:
                print(f"    - {err}")
        assert_workbook_valid(path)

    print("Both workbooks validated cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
