"""Generate mapping + rules CSVs for SHAW ATOCTRAN.

Walks the 7 record-type sheets in
``mappings/excel/ATOCTRAN_SHAW_Mappings_only.xlsx`` and emits two CSVs
per sheet under ``mappings/csv/shaw_atoctran/``:

  - ``SHAW_ATOCTRAN_<rectype>_mapping.csv`` — fed to
    :class:`src.config.template_converter.TemplateConverter`
  - ``SHAW_ATOCTRAN_<rectype>_rules.csv`` — fed to
    :class:`src.config.ba_rules_template_converter.BARulesTemplateConverter`

The CSV column layouts match the contracts in
``prompts/generate-mapping-csv.md`` and ``prompts/generate-rules-csv.md``.

This wrapper is now a thin driver — the actual workbook → CSV pipeline
lives in :mod:`scripts.shaw_csv_gen`. Sibling wrappers exist for other
SHAW outputs (TRANERT, CONTACT, …) and share the same package.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make ``scripts`` importable when invoked from repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.shaw_csv_gen import (  # noqa: E402
    SourceCsvConfig,
    generate_csvs_for_source,
)


ATOCTRAN_CONFIG = SourceCsvConfig(
    source_name="ATOCTRAN",
    workbook_path=_REPO_ROOT / "mappings" / "excel" / "ATOCTRAN_SHAW_Mappings_only.xlsx",
    target_sheets=("100", "200", "300", "605", "700", "900", "060"),
    output_dir=_REPO_ROOT / "mappings" / "csv" / "shaw_atoctran",
    # Sheet names == record-type tokens for ATOCTRAN (identity).
)


def main() -> int:
    """Run ATOCTRAN CSV generation."""
    return generate_csvs_for_source(ATOCTRAN_CONFIG)


if __name__ == "__main__":
    sys.exit(main())
