#!/usr/bin/env python3
"""Generate mapping JSON and business rules JSON from standardized CSV templates."""

from __future__ import annotations
import argparse
import csv
from pathlib import Path
import sys

from src.config.template_converter import TemplateConverter
from src.config.rules_template_converter import RulesTemplateConverter

MAPPING_HEADERS = [
    "Field Name", "Data Type", "Target Name", "Required", "Position", "Length",
    "Format", "Description", "Default Value"
]

RULE_HEADERS = [
    "Rule ID", "Rule Name", "Description", "Type", "Severity", "Operator",
    "Field", "Value", "Values", "Pattern", "Min", "Max", "Left Field", "Right Field",
    "Enabled", "Min Length", "Max Length"
]


def read_headers(path: Path):
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        return next(reader)


def validate_headers(path: Path, expected: list[str], kind: str):
    actual = [h.strip() for h in read_headers(path)]
    missing = [h for h in expected if h not in actual]
    if missing:
        raise ValueError(f"{kind} CSV missing required standardized headers: {missing}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mapping-csv", type=Path, help="Path to standardized mapping CSV")
    p.add_argument("--mapping-out", type=Path, help="Output path for mapping JSON")
    p.add_argument("--mapping-name", help="Mapping name override")
    p.add_argument("--mapping-format", choices=["pipe_delimited", "fixed_width", "csv", "tsv"], help="Mapping file format")

    p.add_argument("--rules-csv", type=Path, help="Path to standardized rules CSV")
    p.add_argument("--rules-out", type=Path, help="Output path for rules JSON")

    args = p.parse_args()

    if not args.mapping_csv and not args.rules_csv:
        p.error("Provide at least one of --mapping-csv or --rules-csv")

    if args.mapping_csv:
        if not args.mapping_out:
            p.error("--mapping-out is required when using --mapping-csv")
        validate_headers(args.mapping_csv, MAPPING_HEADERS, "Mapping")
        tc = TemplateConverter()
        tc.from_csv(str(args.mapping_csv), mapping_name=args.mapping_name, file_format=args.mapping_format)
        tc.save(str(args.mapping_out))
        print(f"✅ Mapping JSON generated: {args.mapping_out}")

    if args.rules_csv:
        if not args.rules_out:
            p.error("--rules-out is required when using --rules-csv")
        validate_headers(args.rules_csv, RULE_HEADERS, "Rules")
        rc = RulesTemplateConverter()
        rc.from_csv(str(args.rules_csv))
        rc.save(str(args.rules_out))
        print(f"✅ Rules JSON generated: {args.rules_out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
