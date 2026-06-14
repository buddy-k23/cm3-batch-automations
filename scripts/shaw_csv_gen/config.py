"""Declarative configuration for a single SHAW source CSV-generation run.

Each SHAW output file (ATOCTRAN, TRANERT, CONTACT, …) has its own spec
workbook with a slightly different sheet layout: ATOCTRAN's record-type
sheets are named ``"100"``, ``"200"``, …; TRANERT's are
``"NEW1 - 32000"``, ``"CUS - 32005"``, …; CONTACT's will be different
again. This module captures everything that varies per source so the
runner can be reused unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence


def _identity_rectype(sheet_name: str) -> str:
    """Default sheet-name → record-type mapping (identity)."""
    return sheet_name


@dataclass(frozen=True)
class SourceCsvConfig:
    """Configuration for one source's mapping/rules CSV generation.

    Attributes:
        source_name: Short uppercase token used as the second component
            of the generated CSV filenames (``SHAW_<source_name>_<rectype>_*.csv``).
            Examples: ``"ATOCTRAN"``, ``"TRANERT"``.
        workbook_path: Path to the spec ``.xlsx`` for this source.
        target_sheets: Ordered sequence of sheet names to read. Sheets
            not in this list (summary tabs, BA worksheets, etc.) are
            ignored.
        output_dir: Directory to write the per-record-type CSVs into.
            Typically ``mappings/csv/shaw_<source>/``.
        sheet_to_rectype: Optional callable mapping a sheet name to the
            record-type token used in CSV filenames. For ATOCTRAN this
            is the identity function (sheet ``"100"`` → rectype
            ``"100"``). For TRANERT it would strip the ``" - 32000"``
            suffix and return the alphabetic prefix
            (``"NEW1 - 32000"`` → ``"NEW1"``).
        filename_prefix: Optional override for the full filename prefix
            in case ``"SHAW_<source_name>"`` is not the desired stem.
            Leave ``None`` to derive from ``source_name``.
    """

    source_name: str
    workbook_path: Path
    target_sheets: Sequence[str]
    output_dir: Path
    sheet_to_rectype: Callable[[str], str] = field(default=_identity_rectype)
    filename_prefix: Optional[str] = None

    def csv_stem(self, rectype: str, kind: str) -> str:
        """Return the filename stem for a record-type CSV.

        Args:
            rectype: The record-type token (e.g. ``"100"``, ``"NEW1"``).
            kind: Either ``"mapping"`` or ``"rules"``.

        Returns:
            The full file stem, e.g. ``"SHAW_ATOCTRAN_100_mapping"``.
        """
        prefix = self.filename_prefix or f"SHAW_{self.source_name}"
        return f"{prefix}_{rectype}_{kind}"
