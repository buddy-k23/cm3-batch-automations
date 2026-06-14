#!/usr/bin/env python3
"""Validate all files in data/files using mapping documents defined in a manifest CSV."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import subprocess
import sys
from datetime import datetime


def parse_bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def resolve_paths(project_root: Path, row: dict) -> tuple[Path, Path, Path | None, Path]:
    """Resolve manifest paths into absolute filesystem paths."""
    data_file = Path(row["data_file"])
    mapping_file = Path(row["mapping_file"])
    rules_file = Path(row["rules_file"]) if row.get("rules_file") else None

    if not data_file.is_absolute():
        data_file = project_root / data_file
    if not mapping_file.is_absolute():
        mapping_file = project_root / mapping_file
    if rules_file and not rules_file.is_absolute():
        rules_file = project_root / rules_file

    report_name = row.get("report_file") or f"{data_file.stem}_validation.json"
    report_path = Path(report_name)
    if not report_path.is_absolute():
        report_path = project_root / "reports" / report_path

    return data_file, mapping_file, rules_file, report_path


def run_validate(project_root: Path, row: dict, default_chunked: bool, default_chunk_size: int) -> tuple[bool, str]:
    data_file, mapping_file, rules_file, report_path = resolve_paths(project_root, row)

    report_path.parent.mkdir(parents=True, exist_ok=True)

    use_chunked = parse_bool(row.get("chunked"), default_chunked)
    chunk_size = int(row.get("chunk_size") or default_chunk_size)

    cmd = [
        sys.executable,
        "-m",
        "src.main",
        "validate",
        "-f",
        str(data_file),
        "-m",
        str(mapping_file),
        "-o",
        str(report_path),
    ]

    if rules_file:
        cmd.extend(["-r", str(rules_file)])

    if use_chunked:
        cmd.extend(["--use-chunked", "--chunk-size", str(chunk_size), "--no-progress"])

    proc = subprocess.run(cmd, cwd=project_root, capture_output=True, text=True)
    ok = proc.returncode == 0
    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return ok, output


def auto_discover_rows(project_root: Path) -> list[dict]:
    """Auto-discover data files and matching mapping files by stem name."""
    data_dir = project_root / "data" / "files"
    mapping_dir = project_root / "config" / "mappings"

    if not data_dir.exists():
        return []

    rows = []
    for data_file in sorted(p for p in data_dir.iterdir() if p.is_file()):
        mapping_file = mapping_dir / f"{data_file.stem}.json"
        if mapping_file.exists():
            rows.append({
                "data_file": str(data_file.relative_to(project_root)),
                "mapping_file": str(mapping_file.relative_to(project_root)),
                "report_file": f"{data_file.stem}_validation.json",
                "chunked": "true",
            })

    return rows


def validate_manifest_rows(project_root: Path, rows: list[dict]) -> tuple[list[dict], list[str]]:
    """Validate manifest row paths before execution."""
    valid_rows = []
    errors = []

    for idx, row in enumerate(rows, start=2):
        try:
            data_file, mapping_file, rules_file, _ = resolve_paths(project_root, row)
        except Exception as exc:
            errors.append(f"Row {idx}: invalid row format ({exc})")
            continue

        row_errors = []
        if not data_file.exists():
            row_errors.append(f"data_file not found: {data_file}")
        if not mapping_file.exists():
            row_errors.append(f"mapping_file not found: {mapping_file}")
        if rules_file and not rules_file.exists():
            row_errors.append(f"rules_file not found: {rules_file}")

        if row_errors:
            errors.append(f"Row {idx}: " + "; ".join(row_errors))
        else:
            valid_rows.append(row)

    return valid_rows, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="config/validation_manifest.csv",
                        help="CSV manifest with columns: data_file,mapping_file[,rules_file,report_file,chunked,chunk_size]")
    parser.add_argument("--default-chunked", action="store_true", help="Use chunked validation by default")
    parser.add_argument("--default-chunk-size", type=int, default=100000, help="Default chunk size")
    parser.add_argument("--auto-discover", action="store_true",
                        help="Auto-discover data/files/* and config/mappings/<stem>.json pairs")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]

    rows = []
    manifest_path = Path(args.manifest)
    if not manifest_path.is_absolute():
        manifest_path = project_root / manifest_path

    if manifest_path.exists():
        with manifest_path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        required_cols = {"data_file", "mapping_file"}
        if rows and not required_cols.issubset(set(rows[0].keys())):
            print(f"❌ Manifest must include columns: {sorted(required_cols)}")
            return 1
    elif args.auto_discover:
        print(f"ℹ️ Manifest not found ({manifest_path}), using auto-discovery...")
        rows = auto_discover_rows(project_root)
    else:
        print(f"❌ Manifest not found: {manifest_path}")
        print("Create it with columns: data_file,mapping_file,rules_file,report_file,chunked,chunk_size")
        print("Or rerun with --auto-discover")
        return 1

    if not rows:
        print("⚠️ No validation jobs found.")
        return 0

    rows, precheck_errors = validate_manifest_rows(project_root, rows)
    if precheck_errors:
        print("❌ Pre-validation failed:")
        for err in precheck_errors:
            print(f"  - {err}")
        return 1

    if not rows:
        print("⚠️ No valid jobs remain after pre-validation.")
        return 1

    summary = []
    for row in rows:
        label = f"{row.get('data_file')} -> {row.get('mapping_file')}"
        print(f"\n▶ Validating {label}")
        ok, output = run_validate(project_root, row, args.default_chunked, args.default_chunk_size)
        print(output.strip())
        summary.append({"label": label, "ok": ok})

    passed = sum(1 for s in summary if s["ok"])
    failed = len(summary) - passed

    print("\n" + "=" * 60)
    print("VALIDATION BATCH SUMMARY")
    print("=" * 60)
    print(f"Timestamp: {datetime.now().isoformat()}")
    print(f"Total:  {len(summary)}")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
