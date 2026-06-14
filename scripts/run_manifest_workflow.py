#!/usr/bin/env python3
"""End-to-end manifest workflow:
1) Convert CSV mappings to JSON mappings
2) Run detailed validations and generate reports
3) Generate telemetry
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

from src.config.template_converter import TemplateConverter
from src.workflows.engine import build_validate_cmd, run_subprocess


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def resolve(p: str, root: Path) -> Path:
    pp = Path(p)
    return pp if pp.is_absolute() else (root / pp)


def convert_mapping_if_needed(mapping_path: Path, generated_dir: Path) -> Path:
    if mapping_path.suffix.lower() != ".csv":
        return mapping_path

    generated_dir.mkdir(parents=True, exist_ok=True)
    out_json = generated_dir / f"{mapping_path.stem}.json"

    tc = TemplateConverter()
    tc.from_csv(str(mapping_path), mapping_name=mapping_path.stem)
    tc.save(str(out_json))
    return out_json


def run_validate(project_root: Path, py: Path, data_file: Path, mapping_file: Path, rules_file: Path | None,
                 report_file: Path, chunked: bool, chunk_size: int) -> tuple[int, str]:
    report_file.parent.mkdir(parents=True, exist_ok=True)

    cmd = build_validate_cmd(
        py=str(py),
        input_file=str(data_file),
        mapping=str(mapping_file),
        rules=str(rules_file) if rules_file else None,
        output=str(report_file),
        detailed=True,
        use_chunked=chunked,
        chunk_size=chunk_size,
        progress=False,
    )

    res = run_subprocess(cmd, project_root)
    return int(res["exit_code"]), str(res["output"])


def count_csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as f:
        return max(sum(1 for _ in f) - 1, 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="Manifest CSV path")
    parser.add_argument("--default-chunked", action="store_true")
    parser.add_argument("--default-chunk-size", type=int, default=100000)
    parser.add_argument("--generated-mapping-dir", default="config/mappings/generated_from_manifest")
    parser.add_argument("--reports-dir", default="reports/manifest_workflow")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    py = project_root / ".venv" / "bin" / "python"

    manifest = resolve(args.manifest, project_root)
    if not manifest.exists():
        print(f"❌ Manifest not found: {manifest}")
        return 1

    generated_mapping_dir = resolve(args.generated_mapping_dir, project_root)
    reports_dir = resolve(args.reports_dir, project_root)
    reports_dir.mkdir(parents=True, exist_ok=True)

    with manifest.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    required = {"data_file", "mapping_file"}
    if not rows:
        print("⚠️ Manifest has no rows")
        return 0
    if not required.issubset(rows[0].keys()):
        print("❌ Manifest must include: data_file,mapping_file")
        return 1

    telemetry_path = reports_dir / f"telemetry_{manifest.stem}.csv"
    with telemetry_path.open("w", newline="", encoding="utf-8") as tf:
        w = csv.writer(tf)
        w.writerow([
            "data_file", "mapping_input", "mapping_json_used", "status", "exit_code",
            "duration_seconds", "error_rows", "warning_rows", "report_file"
        ])

        for row in rows:
            data_file = resolve(row["data_file"], project_root)
            mapping_input = resolve(row["mapping_file"], project_root)
            rules_file = resolve(row["rules_file"], project_root) if row.get("rules_file") else None

            if not data_file.exists():
                print(f"❌ data_file not found: {data_file}")
                w.writerow([row.get("data_file"), row.get("mapping_file"), "", "FAIL", 2, 0, 0, 0, ""])
                continue
            if not mapping_input.exists():
                print(f"❌ mapping_file not found: {mapping_input}")
                w.writerow([row.get("data_file"), row.get("mapping_file"), "", "FAIL", 2, 0, 0, 0, ""])
                continue

            mapping_json = convert_mapping_if_needed(mapping_input, generated_mapping_dir)

            report_name = row.get("report_file") or f"{data_file.stem}_validation.html"
            report_file = resolve(report_name, project_root)
            if not report_file.is_absolute() or project_root in report_file.parents:
                # Keep workflow outputs grouped unless absolute path is provided.
                report_file = reports_dir / Path(report_name).name

            chunked = parse_bool(row.get("chunked"), args.default_chunked)
            chunk_size = int(row.get("chunk_size") or args.default_chunk_size)

            print(f"▶ {data_file.name} with {mapping_json.name}")
            t0 = time.perf_counter()
            rc, out = run_validate(project_root, py, data_file, mapping_json, rules_file, report_file, chunked, chunk_size)
            dt = round(time.perf_counter() - t0, 3)
            print(out.strip())

            err_csv = report_file.with_name(f"{report_file.stem}_errors.csv")
            warn_csv = report_file.with_name(f"{report_file.stem}_warnings.csv")

            w.writerow([
                str(data_file.relative_to(project_root)) if data_file.is_relative_to(project_root) else str(data_file),
                str(mapping_input.relative_to(project_root)) if mapping_input.is_relative_to(project_root) else str(mapping_input),
                str(mapping_json.relative_to(project_root)) if mapping_json.is_relative_to(project_root) else str(mapping_json),
                "PASS" if rc == 0 else "FAIL",
                rc,
                dt,
                count_csv_rows(err_csv),
                count_csv_rows(warn_csv),
                str(report_file.relative_to(project_root)) if report_file.is_relative_to(project_root) else str(report_file),
            ])

    print(f"\n✅ Telemetry written: {telemetry_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
