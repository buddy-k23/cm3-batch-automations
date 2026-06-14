#!/usr/bin/env python3
"""Generate 10 fixed-width mapping/data scenarios, build manifest, run validation, and capture telemetry."""

from __future__ import annotations

import csv
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[1]

MAPPING_CSV_HEADERS = [
    "Field Name", "Data Type", "Target Name", "Required", "Position", "Length",
    "Format", "Description", "Default Value", "Valid Values"
]


@dataclass
class FieldDef:
    name: str
    dtype: str
    length: int
    fmt: str = ""
    required: bool = False
    valid_values: str = ""


FIELDS: List[FieldDef] = [
    FieldDef("REC_TYPE", "string", 2, required=True, valid_values="HD,DT"),
    FieldDef("ACCOUNT_ID", "integer", 10, "9(10)", required=True),
    FieldDef("CUSTOMER_ID", "integer", 8, "9(8)", required=True),
    FieldDef("COUNTRY", "string", 3, required=True, valid_values="USA,CAN,MEX"),
    FieldDef("STATE", "string", 2, required=True, valid_values="NY,CA,TX,FL"),
    FieldDef("POSTAL", "integer", 5, "9(5)", required=True),
    FieldDef("OPEN_DATE", "date", 8, "CCYYMMDD", required=True),
    FieldDef("CLOSE_DATE", "date", 8, "CCYYMMDD", required=False),
    FieldDef("BALANCE", "decimal", 13, "+9(7)V9(5)", required=True),
    FieldDef("CREDIT_LIMIT", "decimal", 13, "+9(7)V9(5)", required=True),
    FieldDef("STATUS", "string", 1, required=True, valid_values="A,I,C"),
    FieldDef("RISK_SCORE", "integer", 3, "9(3)", required=True),
    FieldDef("RATE", "decimal", 5, "9(2)V9(3)", required=True),
    FieldDef("AMOUNT_DUE", "decimal", 11, "+9(6)V9(4)", required=True),
    FieldDef("LAST_PMT_DATE", "date", 8, "CCYYMMDD", required=False),
    FieldDef("DELQ_DAYS", "integer", 3, "9(3)", required=True),
    FieldDef("CODE3", "string", 3, "XXX", required=False),
    FieldDef("CURRENCY", "string", 3, required=True, valid_values="USD,CAD,MXN"),
    FieldDef("FLAG", "string", 1, required=False, valid_values="Y,N"),
    FieldDef("SEGMENT", "string", 2, required=False, valid_values="R1,R2,R3"),
    FieldDef("EMAIL_OPT_IN", "string", 1, required=False, valid_values="Y,N"),
    FieldDef("PHONE_AREA", "integer", 3, "9(3)", required=False),
    FieldDef("PHONE_NUM", "integer", 7, "9(7)", required=False),
    FieldDef("FILLER1", "string", 5, required=False),
    FieldDef("FILLER2", "string", 5, required=False),
]

SCENARIOS = [
    "all_valid",
    "short_rows",
    "long_rows",
    "invalid_valid_values",
    "required_empty",
    "invalid_dates",
    "invalid_numeric_format",
    "mixed_errors",
    "invalid_postal",
    "invalid_currency_and_status",
]


def format_value(field: FieldDef, row_idx: int) -> str:
    if field.name == "REC_TYPE":
        v = "HD"
    elif field.name == "ACCOUNT_ID":
        v = f"{1000000000 + row_idx:010d}"
    elif field.name == "CUSTOMER_ID":
        v = f"{20000000 + row_idx:08d}"
    elif field.name == "COUNTRY":
        v = ["USA", "CAN", "MEX"][row_idx % 3]
    elif field.name == "STATE":
        v = ["NY", "CA", "TX", "FL"][row_idx % 4]
    elif field.name == "POSTAL":
        v = f"{10000 + (row_idx % 89999):05d}"
    elif field.name in {"OPEN_DATE", "CLOSE_DATE", "LAST_PMT_DATE"}:
        v = f"2025{(row_idx % 12) + 1:02d}{(row_idx % 28) + 1:02d}"
    elif field.name in {"BALANCE", "CREDIT_LIMIT"}:
        v = f"+{(100000000000 + row_idx):012d}"
    elif field.name == "STATUS":
        v = ["A", "I", "C"][row_idx % 3]
    elif field.name == "RISK_SCORE":
        v = f"{(row_idx % 999):03d}"
    elif field.name == "RATE":
        v = f"{(row_idx % 99):02d}{(row_idx % 999):03d}"
    elif field.name == "AMOUNT_DUE":
        v = f"+{(1000000000 + row_idx):010d}"
    elif field.name == "DELQ_DAYS":
        v = f"{(row_idx % 365):03d}"
    elif field.name == "CODE3":
        v = "ABC"
    elif field.name == "CURRENCY":
        v = ["USD", "CAD", "MXN"][row_idx % 3]
    elif field.name == "FLAG":
        v = "Y" if row_idx % 2 == 0 else "N"
    elif field.name == "SEGMENT":
        v = ["R1", "R2", "R3"][row_idx % 3]
    elif field.name == "EMAIL_OPT_IN":
        v = "Y" if row_idx % 2 else "N"
    elif field.name == "PHONE_AREA":
        v = f"{200 + (row_idx % 700):03d}"
    elif field.name == "PHONE_NUM":
        v = f"{1000000 + row_idx:07d}"
    elif field.name == "FILLER1":
        v = "HELLO"
    elif field.name == "FILLER2":
        v = "WORLD"
    else:
        v = ""
    return str(v).ljust(field.length)[:field.length]


def build_row(row_idx: int) -> str:
    return "".join(format_value(f, row_idx) for f in FIELDS)


def apply_scenario_mutations(lines: List[str], scenario: str) -> List[str]:
    out = lines[:]
    if scenario == "short_rows":
        for i in [0, 5, 10, 15, 20, 25]:
            out[i] = out[i][:-3]
    elif scenario == "long_rows":
        for i in [1, 6, 11, 16, 21, 26]:
            out[i] = out[i] + "XYZ"
    elif scenario == "invalid_valid_values":
        for i in [2, 12, 22]:
            row = list(out[i])
            # COUNTRY starts after REC_TYPE(2)+ACCOUNT_ID(10)+CUSTOMER_ID(8)=20
            row[20:23] = list("ZZZ")
            out[i] = "".join(row)
    elif scenario == "required_empty":
        for i in [3, 13, 23]:
            row = list(out[i])
            # ACCOUNT_ID starts at 2 len 10
            row[2:12] = list(" " * 10)
            out[i] = "".join(row)
    elif scenario == "invalid_dates":
        for i in [4, 14, 24]:
            row = list(out[i])
            # OPEN_DATE start: 2+10+8+3+2+5 = 30, len 8
            row[30:38] = list("20251340")
            out[i] = "".join(row)
    elif scenario == "invalid_numeric_format":
        for i in [5, 15, 25]:
            row = list(out[i])
            # BALANCE start: through CLOSE_DATE end => 46, len 13
            row[46:59] = list("A000000000000")
            out[i] = "".join(row)
    elif scenario == "mixed_errors":
        out = apply_scenario_mutations(out, "short_rows")
        out = apply_scenario_mutations(out, "invalid_numeric_format")
        out = apply_scenario_mutations(out, "invalid_valid_values")
    elif scenario == "invalid_postal":
        for i in [7, 17, 27]:
            row = list(out[i])
            # POSTAL start: 25 len 5
            row[25:30] = list("12A4B")
            out[i] = "".join(row)
    elif scenario == "invalid_currency_and_status":
        for i in [8, 18, 28]:
            row = list(out[i])
            # STATUS start around after CREDIT_LIMIT: 59+13=72, len1
            row[72:73] = list("X")
            # CURRENCY start around CODE3 end: compute direct by cumulative
            # REC(2)+10+8+3+2+5+8+8+13+13+1+3+5+11+8+3+3 = 106
            row[106:109] = list("EUR")
            out[i] = "".join(row)
    return out


def write_mapping_csv(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(MAPPING_CSV_HEADERS)
        pos = 1
        for field in FIELDS:
            w.writerow([
                field.name,
                field.dtype,
                field.name,
                "Y" if field.required else "N",
                pos,
                field.length,
                field.fmt,
                f"Scenario field {field.name}",
                "",
                field.valid_values,
            ])
            pos += field.length


def generate_artifacts() -> Path:
    template_dir = PROJECT_ROOT / "config" / "templates" / "csv" / "manifest_scenarios"
    mapping_json_dir = PROJECT_ROOT / "config" / "mappings" / "manifest_scenarios"
    data_dir = PROJECT_ROOT / "data" / "files" / "manifest_scenarios"
    reports_dir = PROJECT_ROOT / "reports" / "manifest_scenarios"

    for d in [template_dir, mapping_json_dir, data_dir, reports_dir]:
        d.mkdir(parents=True, exist_ok=True)

    manifest_path = PROJECT_ROOT / "config" / "validation_manifest_10_scenarios.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as mf:
        w = csv.writer(mf)
        w.writerow(["data_file", "mapping_file", "rules_file", "report_file", "chunked", "chunk_size"])

        for i, scenario in enumerate(SCENARIOS, start=1):
            base = f"scenario_{i:02d}_{scenario}"
            mapping_csv = template_dir / f"{base}.csv"
            mapping_json = mapping_json_dir / f"{base}.json"
            data_file = data_dir / f"{base}.txt"
            report_file = f"manifest_scenarios/{base}_validation.html"

            write_mapping_csv(mapping_csv)

            subprocess.run([
                str(PROJECT_ROOT / ".venv" / "bin" / "python"),
                "scripts/generate_from_csv_templates.py",
                "--mapping-csv", str(mapping_csv),
                "--mapping-out", str(mapping_json),
                "--mapping-name", base,
                "--mapping-format", "fixed_width",
            ], cwd=PROJECT_ROOT, check=True)

            lines = [build_row(r) for r in range(1, 51)]
            lines = apply_scenario_mutations(lines, scenario)
            data_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

            w.writerow([
                str(data_file.relative_to(PROJECT_ROOT)),
                str(mapping_json.relative_to(PROJECT_ROOT)),
                "",
                report_file,
                "false",
                "100000",
            ])

    return manifest_path


def run_manifest_with_telemetry(manifest_path: Path) -> Path:
    telemetry_path = PROJECT_ROOT / "reports" / "manifest_scenarios" / "telemetry.csv"
    rows = []
    with manifest_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    telemetry_path.parent.mkdir(parents=True, exist_ok=True)
    with telemetry_path.open("w", newline="", encoding="utf-8") as tf:
        w = csv.writer(tf)
        w.writerow([
            "data_file", "mapping_file", "status", "exit_code", "duration_seconds",
            "error_rows", "warning_rows", "report_file"
        ])

        for row in rows:
            cmd = [
                str(PROJECT_ROOT / ".venv" / "bin" / "python"),
                "-m", "src.main", "validate",
                "-f", str(PROJECT_ROOT / row["data_file"]),
                "-m", str(PROJECT_ROOT / row["mapping_file"]),
                "--detailed",
                "-o", str(PROJECT_ROOT / "reports" / row["report_file"]),
            ]

            t0 = time.perf_counter()
            proc = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)
            dt = round(time.perf_counter() - t0, 3)

            report_html = PROJECT_ROOT / "reports" / row["report_file"]
            err_csv = report_html.with_name(f"{report_html.stem}_errors.csv")
            warn_csv = report_html.with_name(f"{report_html.stem}_warnings.csv")

            def _count_data_rows(p: Path) -> int:
                if not p.exists():
                    return 0
                with p.open(encoding="utf-8") as f2:
                    return max(sum(1 for _ in f2) - 1, 0)

            err_count = _count_data_rows(err_csv)
            warn_count = _count_data_rows(warn_csv)

            w.writerow([
                row["data_file"],
                row["mapping_file"],
                "PASS" if proc.returncode == 0 else "FAIL",
                proc.returncode,
                dt,
                err_count,
                warn_count,
                row["report_file"],
            ])

    return telemetry_path


def main() -> int:
    manifest = generate_artifacts()
    telemetry = run_manifest_with_telemetry(manifest)

    print(f"✅ Generated manifest: {manifest}")
    print(f"✅ Telemetry: {telemetry}")
    print("✅ Run complete. Use scripts/run_validate_all.sh config/validation_manifest_10_scenarios.csv for reruns.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
