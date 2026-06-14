"""Reusable CSV-generation library for SHAW source spec workbooks.

Extracted from ``scripts/generate_shaw_atoctran_csvs.py`` so the same
mapping/rules CSV generation pipeline can be reused for every SHAW
output file (ATOCTRAN, TRANERT, CONTACT, …). The original ATOCTRAN
generator is now a thin wrapper that imports from this package; new
generators (e.g. ``generate_shaw_tranert_csvs.py``) follow the same
pattern.

Public exports
--------------

- :class:`SourceCsvConfig` — declarative configuration for one source
  file (workbook path, target sheets, sheet→rectype mapping, file
  naming).
- :func:`generate_csvs_for_source` — runs the full pipeline for one
  source and prints a summary table.
- :data:`STRING_FIELD_MIN_LENGTH_OVERRIDES` — shared override table
  for operator-confirmed minimum trimmed lengths for string fields.
- :data:`MIN_LENGTH_DEFAULT` — default minimum trimmed length for
  string fields not listed in the override table.

The split keeps three concerns separate:

- ``csv_builders`` — row construction (mapping + rules) from a
  :class:`SheetReader`.
- ``valid_values`` — text-mining the Transformation column for
  enumerated valid-value lists.
- ``helpers`` — small utilities (string normalization, length
  derivation, datatype normalization).

Naming note: this package is named ``shaw_csv_gen`` rather than
``shaw_csv_lib`` because the repo ``.gitignore`` excludes any ``lib/``
directory (see AGENTS.md).
"""

from __future__ import annotations

from .config import SourceCsvConfig
from .csv_builders import (
    MAPPING_HEADERS,
    MIN_LENGTH_DEFAULT,
    RULES_HEADERS,
    STRING_FIELD_MIN_LENGTH_OVERRIDES,
    build_mapping_rows,
    build_rules_rows,
)
from .runner import generate_csvs_for_source

__all__ = [
    "MAPPING_HEADERS",
    "MIN_LENGTH_DEFAULT",
    "RULES_HEADERS",
    "STRING_FIELD_MIN_LENGTH_OVERRIDES",
    "SourceCsvConfig",
    "build_mapping_rows",
    "build_rules_rows",
    "generate_csvs_for_source",
]
