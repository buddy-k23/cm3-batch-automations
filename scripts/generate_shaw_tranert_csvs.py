"""Generate mapping + rules CSVs for SHAW TRANERT.

Walks the 6 record-type layout sheets in
``mappings/excel/TRANERT_SHAW_Mappings.xlsx`` and emits two CSVs per
sheet under ``mappings/csv/shaw_tranert/``:

  - ``SHAW_TRANERT_<rectype>_mapping.csv`` — fed to
    :class:`src.config.template_converter.TemplateConverter`
  - ``SHAW_TRANERT_<rectype>_rules.csv`` — fed to
    :class:`src.config.ba_rules_template_converter.BARulesTemplateConverter`

This wrapper is a thin driver — the actual workbook → CSV pipeline
lives in :mod:`scripts.shaw_csv_gen`, shared with the SHAW ATOCTRAN
generator.

Layout-sheet naming
-------------------

The TRANERT workbook names its per-record-type *mapping layout* sheets
with the pattern ``"<rectype> - <txn_code>"`` (e.g. ``"NEW1 - 32000"``).
The CSV generator uses the alphabetic prefix as the rectype token so
the resulting filenames are ``SHAW_TRANERT_NEW1_mapping.csv`` etc.

Transaction-code → record-type mapping
--------------------------------------

The TRANERT discriminator is ``TRN-COD-ERT`` at position 170, length 5.
Most record types map 1:1 to a code, but ``NEW1`` covers both ``32000``
(New Account Setup) and ``32001`` (Existing/Conversion Account Setup)
per the workbook spec. The umbrella YAML at
``config/mappings/SHAW_TRANERT.yaml`` therefore wires both codes to the
same NEW1 mapping/rules JSONs.

The ``Batch Header`` sheet is **not** processed by this generator. Per
operator direction (2026-05-21), TRANERT can carry multiple Batch
Header records per file, so it is treated as a separate record-type
entry in the umbrella with its own mapping pipeline rather than being
folded into the discriminator dispatch.
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


def _tranert_sheet_to_rectype(sheet_name: str) -> str:
    """Return the alphabetic prefix of a TRANERT layout sheet name.

    The TRANERT workbook names its per-record-type mapping sheets
    ``"<rectype> - <txn_code>"`` (e.g. ``"NEW1 - 32000"``). The CSV
    filenames use only the alphabetic prefix so they remain readable
    and so the umbrella can map multiple transaction codes to the
    same record-type JSON (NEW1 covers 32000 and 32001).
    """
    # Strip everything from the first " - " onwards.
    if " - " in sheet_name:
        return sheet_name.split(" - ", 1)[0].strip()
    return sheet_name.strip()


TRANERT_CONFIG = SourceCsvConfig(
    source_name="TRANERT",
    workbook_path=_REPO_ROOT / "mappings" / "excel" / "TRANERT_SHAW_Mappings.xlsx",
    target_sheets=(
        "NEW1 - 32000",
        "CUS - 32005",
        "ORI - 32010",
        "COD - 32025",
        "CBRS - 32040",
        "REC - 32075",
    ),
    output_dir=_REPO_ROOT / "mappings" / "csv" / "shaw_tranert",
    sheet_to_rectype=_tranert_sheet_to_rectype,
)


def main() -> int:
    """Run TRANERT CSV generation."""
    return generate_csvs_for_source(TRANERT_CONFIG)


if __name__ == "__main__":
    sys.exit(main())
