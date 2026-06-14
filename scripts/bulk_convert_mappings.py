#!/usr/bin/env python3
"""Bulk convert mapping templates (CSV/XLS/XLSX) to mapping JSON files."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from src.config.template_converter import TemplateConverter
import pandas as pd

SUPPORTED_EXTS = {".csv", ".xlsx", ".xls"}


def load_template_df(template_path: Path) -> pd.DataFrame:
    if template_path.suffix.lower() == ".csv":
        return pd.read_csv(template_path, dtype=str)
    return pd.read_excel(template_path, dtype=str)


def validate_template_strict(template_path: Path) -> list[dict]:
    """Return row-level validation errors for strict template conversion."""
    df = load_template_df(template_path)
    df.columns = [c.strip() for c in df.columns]

    required_columns = ["Field Name", "Data Type"]
    fixed_width_columns = ["Position", "Length"]

    issues: list[dict] = []

    missing_headers = [c for c in required_columns if c not in df.columns]
    if missing_headers:
        issues.append({
            "row": "HEADER",
            "field": "<headers>",
            "issue": f"Missing required headers: {missing_headers}",
            "value": "",
        })
        return issues

    is_fixed_width = all(c in df.columns for c in fixed_width_columns)

    for idx, row in df.iterrows():
        row_no = idx + 2  # +1 for 1-index +1 for header

        for c in required_columns:
            v = (row.get(c) or "").strip() if pd.notna(row.get(c)) else ""
            if not v:
                issues.append({"row": row_no, "field": c, "issue": "Required value is empty", "value": ""})

        if is_fixed_width:
            for c in fixed_width_columns:
                v = (row.get(c) or "").strip() if pd.notna(row.get(c)) else ""
                if not v.isdigit():
                    issues.append({
                        "row": row_no,
                        "field": c,
                        "issue": "Expected numeric value for fixed-width template",
                        "value": v,
                    })

    return issues


def write_error_report(report_dir: Path, template_path: Path, issues: list[dict]) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{template_path.stem}.errors.csv"
    pd.DataFrame(issues, columns=["row", "field", "issue", "value"]).to_csv(report_path, index=False)
    return report_path


def convert_file(template_path: Path, output_dir: Path, file_format: str | None = None) -> Path:
    converter = TemplateConverter()
    mapping_name = template_path.stem

    if template_path.suffix.lower() == ".csv":
        converter.from_csv(str(template_path), mapping_name=mapping_name, file_format=file_format)
    else:
        converter.from_excel(str(template_path), mapping_name=mapping_name, file_format=file_format)

    out_path = output_dir / f"{mapping_name}.json"
    converter.save(str(out_path))
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", default="mappings/csv", help="Directory containing mapping CSV/Excel templates")
    parser.add_argument("--output-dir", default="config/mappings", help="Directory to write mapping JSON files")
    parser.add_argument("--format", choices=["pipe_delimited", "fixed_width", "csv", "tsv"],
                        help="Optional mapping source format override")
    parser.add_argument("--error-report-dir", default="reports/template_validation",
                        help="Directory to write strict validation error reports")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)

    if not input_dir.exists():
        print(f"❌ Input directory not found: {input_dir}")
        return 1

    templates = sorted(p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS)
    if not templates:
        print(f"⚠️ No mapping templates found in {input_dir} (supported: {sorted(SUPPORTED_EXTS)})")
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    error_report_dir = Path(args.error_report_dir)

    success = 0
    failed = 0
    for template in templates:
        issues = validate_template_strict(template)
        if issues:
            report = write_error_report(error_report_dir, template, issues)
            print(f"❌ Validation failed for {template.name}. Report: {report}")
            failed += 1
            continue

        try:
            out_path = convert_file(template, output_dir, file_format=args.format)
            print(f"✅ {template.name} -> {out_path}")
            success += 1
        except Exception as exc:
            print(f"❌ Failed to convert {template}: {exc}")
            failed += 1

    print(f"\nDone. Converted: {success}, Failed: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
