"""Driver that generates mapping+rules CSVs for one SHAW source.

Takes a declarative :class:`SourceCsvConfig` and runs the full pipeline:
read the workbook → for each target sheet, build mapping + rules rows →
write the two CSVs per record type → print a summary table.

Same control flow as the original ``generate_shaw_atoctran_csvs.py``;
the only difference is that the source-specific knobs (workbook path,
sheet list, output dir, filename prefix) are now arguments instead of
module-level constants.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Dict, List, Tuple

# Make ``scripts`` importable when invoked from repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.excel_to_spec_text import SpecReadError, read_workbook  # noqa: E402

from .config import SourceCsvConfig
from .csv_builders import (
    MAPPING_HEADERS,
    RULES_HEADERS,
    build_mapping_rows,
    build_rules_rows,
)


def _write_csv(path: Path, headers: List[str], rows: List[Dict[str, str]]) -> None:
    """Write *rows* to *path* with the given header order.

    Uses ``newline=""`` so the csv module controls line endings, matching
    the original generator's behavior (and keeping the regenerated CSVs
    byte-identical when run on the same workbook).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({h: row.get(h, "") for h in headers})


def generate_csvs_for_source(config: SourceCsvConfig) -> int:
    """Generate mapping+rules CSVs for one SHAW source.

    Args:
        config: Declarative configuration for this source. See
            :class:`SourceCsvConfig`.

    Returns:
        Exit code: ``0`` on success, ``1`` if the workbook is missing or
        a sheet read fails.
    """
    workbook = config.workbook_path
    if not workbook.is_file():
        print(f"workbook not found: {workbook}")
        return 1

    print(f"Reading {workbook.name}")
    print(f"Target sheets: {', '.join(config.target_sheets)}")
    try:
        output_rel = config.output_dir.relative_to(_REPO_ROOT)
    except ValueError:
        output_rel = config.output_dir
    print(f"Output dir: {output_rel}")
    print()

    try:
        readers = list(
            read_workbook(workbook, sheet_names=tuple(config.target_sheets))
        )
    except SpecReadError as exc:
        print(f"error: {exc}")
        return 1

    summary: List[Tuple[str, int, int]] = []
    for reader in readers:
        sheet_name = reader.sheet_name
        rectype = config.sheet_to_rectype(sheet_name)
        mapping_rows = build_mapping_rows(reader)
        rules_rows = build_rules_rows(mapping_rows)

        mapping_path = config.output_dir / f"{config.csv_stem(rectype, 'mapping')}.csv"
        rules_path = config.output_dir / f"{config.csv_stem(rectype, 'rules')}.csv"

        _write_csv(mapping_path, MAPPING_HEADERS, mapping_rows)
        _write_csv(rules_path, RULES_HEADERS, rules_rows)

        summary.append((rectype, len(mapping_rows), len(rules_rows)))
        print(
            f"  {rectype}: {len(mapping_rows):>3} fields, "
            f"{len(rules_rows):>3} rules  ->  "
            f"{mapping_path.name}, {rules_path.name}"
        )

    print()
    print("=== Summary ===")
    print(f"{'Rec Type':<15}{'Fields':>8}{'Rules':>8}")
    for rectype, n_fields, n_rules in summary:
        print(f"{rectype:<15}{n_fields:>8}{n_rules:>8}")
    print(f"\nTotal CSVs written: {2 * len(summary)}")
    return 0
