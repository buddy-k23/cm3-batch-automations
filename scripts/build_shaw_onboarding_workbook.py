#!/usr/bin/env python3
"""Build the SHAW onboarding workbook + the blank canonical template (EC-S1).

This script is the **canonical reference** for BAs who want to scaffold a new
onboarding workbook from existing Valdo configuration. It walks:

    - config/e2e/sources/SHAW.yml                  (source / inputs / outputs)
    - config/mappings/SHAW_<FILETYPE>.json|yaml    (per-file mappings + umbrellas)
    - config/mappings/SHAW_<FILETYPE>_<RT>_mapping.json (per-record-type mappings)
    - config/rules/SHAW_<FILETYPE>_<RT>_rules.json (per-record-type rules)
    - mappings/csv/shaw_*/                         (BA-friendly CSV templates,
                                                    used as the source of truth
                                                    for mapping/rules sheet rows
                                                    because they preserve the
                                                    BA-facing column shape)
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
    """
    if not csv_path.exists():
        return []
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        return [dict(row) for row in reader]


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


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
    """Build the SHAW worked-example workbook at ``out_path``."""
    src = _load_yaml(SHAW_YML)
    wb = Workbook()
    wb.remove(wb.active)

    # --- 1. Source / InputFiles / OutputFiles ---
    _add_source_sheet(wb, _shaw_source_row(src))
    _add_input_files_sheet(wb, _input_files_from_shaw_yml(src))
    _add_output_files_sheet(wb, _output_files_from_shaw_yml(src))

    # --- 2. MultiRecord sheets for each .yaml-mapping output. ---
    multi_record_record_types: dict[str, list[str]] = {}
    for entry in src.get("output_files", []):
        mapping_path = entry.get("mapping", "")
        if not mapping_path.endswith(".yaml"):
            continue
        file_type = entry["file_type"]
        umbrella_path = REPO_ROOT / mapping_path
        if not umbrella_path.exists():
            continue
        umbrella = _load_yaml(umbrella_path)
        mr_rows = _multi_record_rows_from_umbrella(file_type, umbrella)
        _add_multi_record_sheet(wb, f"MultiRecord_{file_type}", mr_rows)
        multi_record_record_types[file_type] = [r["record_type_name"] for r in mr_rows]

    # --- 3. Reconciliation sheets (anything under SHAW/reconciliation/). ---
    if SHAW_RECONCILIATION_DIR.exists():
        for recon_yml in sorted(SHAW_RECONCILIATION_DIR.glob("*.yml")):
            spec = _load_yaml(recon_yml)
            file_type = spec.get("file_type") or recon_yml.stem.upper()
            recon_rows = _reconciliation_rows_from_yaml(spec)
            _add_reconciliation_sheet(wb, f"Reconciliation_{file_type}", recon_rows)

    # --- 4. Per-mapping + per-rules sheets. ---
    # 4a. Input-file mapping sheets (all SHAW input mappings are TODO).
    for entry in src.get("input_files", []):
        ftype = entry["file_type"]
        sheet_name = f"{ftype}_Mapping"
        # SHAW input CSVs do not exist for any input file yet — all TODO.
        _add_mapping_sheet(wb, sheet_name, _todo_mapping_rows(ftype))

    # 4b. Output-file mapping + rules sheets.
    for entry in src.get("output_files", []):
        ftype = entry["file_type"]
        mapping_path = entry.get("mapping", "")
        rules_path = entry.get("rules", "")
        if mapping_path.endswith(".yaml"):
            # Multi-record: emit one Mapping + one Rules sheet per record type.
            umbrella = _load_yaml(REPO_ROOT / mapping_path)
            for rt_name, rt_cfg in umbrella.get("record_types", {}).items():
                rt_mapping_path = rt_cfg.get("mapping", "")
                rt_rules_path = rt_cfg.get("rules", "")
                rt_mapping_sheet = _mapping_path_to_sheet_name(rt_mapping_path, ftype, rt_name)
                rt_rules_sheet = _rules_path_to_sheet_name(rt_rules_path, ftype, rt_name)
                # Skip if we already added it (e.g. rt_32000 + rt_32001 share NEW1).
                if _shorten_sheet_name(rt_mapping_sheet) in wb.sheetnames:
                    continue
                csv_rows = _ba_csv_rows_for_record_type(ftype, rt_mapping_path, kind="mapping")
                if csv_rows:
                    _add_mapping_sheet(wb, rt_mapping_sheet, csv_rows)
                else:
                    _add_mapping_sheet(wb, rt_mapping_sheet, _todo_mapping_rows(rt_name))
                if rt_rules_sheet and _shorten_sheet_name(rt_rules_sheet) not in wb.sheetnames:
                    rule_rows = _ba_csv_rows_for_record_type(ftype, rt_rules_path, kind="rules")
                    if rule_rows:
                        _add_rules_sheet(wb, rt_rules_sheet, rule_rows)
                    else:
                        _add_rules_sheet(wb, rt_rules_sheet, _todo_rules_rows(rt_name))
        else:
            sheet_name = f"{ftype}_Mapping"
            if sheet_name not in wb.sheetnames:
                _add_mapping_sheet(wb, sheet_name, _todo_mapping_rows(ftype))
            if rules_path:
                rules_sheet_name = f"{ftype}_Rules"
                if rules_sheet_name not in wb.sheetnames:
                    _add_rules_sheet(wb, rules_sheet_name, _todo_rules_rows(ftype))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(out_path))


def _ba_csv_rows_for_record_type(
    file_type: str, mapping_or_rules_path: str, kind: str
) -> list[dict[str, Any]]:
    """Locate the BA-friendly CSV under mappings/csv/shaw_<ftype>/ matching the
    JSON path; return its rows. Empty list if the CSV does not exist (caller
    substitutes a TODO placeholder).
    """
    if not mapping_or_rules_path:
        return []
    stem = Path(mapping_or_rules_path).stem  # SHAW_TRANERT_BATCH_HEADER_mapping
    folder = f"shaw_{file_type.lower()}"
    csv_path = BA_CSV_ROOT / folder / f"{stem}.csv"
    rows = _read_ba_csv(csv_path)
    if not rows:
        return []
    # Normalise key casing — BA CSVs already use the same headers as our
    # MAPPING_COLUMNS / RULES_COLUMNS, but be defensive about whitespace.
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        cleaned.append({(k or "").strip(): (v or "").strip() for k, v in row.items()})
    _ = kind  # accepted for future per-kind normalisation; currently unused.
    return cleaned


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
