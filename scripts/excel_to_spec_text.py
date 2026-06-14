"""Read Valdo source-spec Excel workbooks and emit pipe-delimited text or
structured row dicts that downstream CSV generators can consume.

This helper is the *reading* half of the Excel-to-CSV pipeline used by the
``prompts/generate-mapping-csv.md``, ``prompts/generate-rules-csv.md`` and
``prompts/generate-both.md`` workflows. It deliberately does **not** generate
CSVs — see ``scripts/generate_shaw_atoctran_csvs.py`` (and future per-source
generators) for that step.

Design notes
------------
- Source-spec workbooks share a recurring shape: a metadata row at top, a
  header row at row 2, then one data row per field. Many sheets carry a
  SOURCE-side block (often blank for a given source) and a TARGET-side block
  separated by a ``Transformation Logic`` column.
- We detect the header row by hint-matching, not by hard-coding row index,
  so future workbooks with slightly different prelude rows still work.
- Strikethrough rows are dropped per the prompt contract
  (``strike`` attribute on the cell font).
- Designed to be reusable for ESA_AFS, P327_SHAW and other Excel specs.

Public API
----------
- :class:`SheetReader` — reads one sheet, exposes its rows as dicts keyed by
  the detected header labels.
- :func:`read_workbook` — convenience for iterating multiple sheets.
- :func:`format_as_pipe_text` — renders a list of row dicts as the
  pipe-delimited block the prompts expect to be pasted in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence

import openpyxl
from openpyxl.cell.cell import Cell
from openpyxl.worksheet.worksheet import Worksheet


# Hints used to find the header row. Match is case-insensitive substring
# against any cell value. Header is the first row hitting >= ``_HEADER_HITS``.
_HEADER_HINTS: tuple[str, ...] = (
    "field", "column", "position", "datatype", "data type",
    "format", "length", "required", "transformation", "valid values",
)
_HEADER_HITS = 3


class SpecReadError(ValueError):
    """Raised when a workbook or sheet cannot be parsed as a Valdo spec."""


@dataclass
class SheetReader:
    """Reads one worksheet and exposes its data rows as label-keyed dicts.

    Attributes:
        sheet_name: The sheet's display name.
        header_row: 1-indexed row number where the header was found.
        headers: Ordered list of column header labels (empties dropped).
        column_indices: Mapping from header label to 0-indexed column number.
        data_rows: List of dicts; each dict maps header label to cell value.
    """

    sheet_name: str
    header_row: int
    headers: List[str]
    column_indices: Dict[str, int]
    data_rows: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_worksheet(
        cls,
        ws: Worksheet,
        *,
        drop_strikethrough: bool = True,
    ) -> "SheetReader":
        """Build a :class:`SheetReader` from an open openpyxl worksheet.

        Args:
            ws: The worksheet to read.
            drop_strikethrough: When True, rows whose field-name cell uses
                strikethrough formatting are dropped (deprecated fields per
                the prompt contract).

        Returns:
            A populated :class:`SheetReader`.

        Raises:
            SpecReadError: If no header row could be detected.
        """
        header_row, headers, col_idx = _detect_header(ws)
        if header_row is None:
            raise SpecReadError(
                f"sheet {ws.title!r}: could not detect a header row "
                f"(looked for hints: {sorted(_HEADER_HINTS)})"
            )

        # Identify the field-name column for strikethrough screening.
        field_name_col = _find_field_name_column(col_idx)

        data_rows: List[Dict[str, Any]] = []
        for r_idx in range(header_row + 1, ws.max_row + 1):
            row_cells: List[Cell] = list(
                ws.iter_rows(
                    min_row=r_idx, max_row=r_idx, values_only=False
                )
            )[0]

            # Skip blank rows.
            if not any(
                c.value is not None and str(c.value).strip()
                for c in row_cells
            ):
                continue

            # Skip rows whose field name is struck through.
            if (
                drop_strikethrough
                and field_name_col is not None
                and field_name_col < len(row_cells)
                and _is_struck(row_cells[field_name_col])
            ):
                continue

            row: Dict[str, Any] = {}
            for label, idx in col_idx.items():
                if idx >= len(row_cells):
                    row[label] = None
                    continue
                cell = row_cells[idx]
                if (
                    drop_strikethrough
                    and _is_struck(cell)
                ):
                    # Individual struck cell: treat as missing.
                    row[label] = None
                else:
                    row[label] = cell.value
            data_rows.append(row)

        return cls(
            sheet_name=ws.title,
            header_row=header_row,
            headers=headers,
            column_indices=col_idx,
            data_rows=data_rows,
        )


def read_workbook(
    workbook_path: Path,
    *,
    sheet_names: Optional[Sequence[str]] = None,
    skip_sheets: Optional[Sequence[str]] = None,
    drop_strikethrough: bool = True,
) -> Iterator[SheetReader]:
    """Yield :class:`SheetReader` for each requested sheet of a workbook.

    Args:
        workbook_path: Path to the ``.xlsx`` file.
        sheet_names: If given, only these sheets are read (in this order).
        skip_sheets: If given, these sheets are skipped. Ignored when
            ``sheet_names`` is provided.
        drop_strikethrough: Forwarded to :meth:`SheetReader.from_worksheet`.

    Yields:
        One :class:`SheetReader` per matching sheet.

    Raises:
        SpecReadError: If a requested sheet is missing or a sheet has no
            detectable header row.
    """
    workbook_path = Path(workbook_path)
    if not workbook_path.is_file():
        raise SpecReadError(f"workbook not found: {workbook_path}")

    wb = openpyxl.load_workbook(workbook_path, data_only=True)
    try:
        if sheet_names is not None:
            requested = list(sheet_names)
            missing = [s for s in requested if s not in wb.sheetnames]
            if missing:
                raise SpecReadError(
                    f"sheets not in workbook {workbook_path.name}: "
                    f"{missing}. Available: {wb.sheetnames}"
                )
            iter_names = requested
        else:
            skip = set(skip_sheets or ())
            iter_names = [s for s in wb.sheetnames if s not in skip]

        for name in iter_names:
            yield SheetReader.from_worksheet(
                wb[name], drop_strikethrough=drop_strikethrough
            )
    finally:
        wb.close()


def format_as_pipe_text(
    rows: Iterable[Dict[str, Any]],
    *,
    columns: Sequence[str],
) -> str:
    """Render a sequence of row dicts as a pipe-delimited text block.

    The prompts in ``prompts/`` expect specifications pasted in this shape.
    Each value is trimmed and any embedded pipes are replaced with a slash
    so the output remains parseable.

    Args:
        rows: Iterable of header-keyed dicts (e.g. from
            :attr:`SheetReader.data_rows`).
        columns: Header labels to emit, in order. Missing keys become empty.

    Returns:
        A multi-line string with a header line and one row per spec field.
    """
    out: List[str] = ["|".join(columns)]
    for row in rows:
        cells: List[str] = []
        for col in columns:
            raw = row.get(col)
            if raw is None:
                cells.append("")
                continue
            text = str(raw).strip().replace("|", "/").replace("\n", " ")
            cells.append(text)
        out.append("|".join(cells))
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #


def _detect_header(
    ws: Worksheet,
) -> tuple[Optional[int], List[str], Dict[str, int]]:
    """Find the header row in ``ws`` by counting hint hits per row.

    Many Valdo source-spec workbooks use a *two-row header*: row 1 carries
    section banners like ``SOURCE`` / ``Transformation Logic`` / ``TARGET``
    over the cells they describe, and row 2 carries the bulk of the column
    labels. We pick row 2 as the canonical header row and merge in any
    row-1 labels for columns left blank in row 2.

    Returns ``(header_row, headers, col_idx)``. When no row matches,
    ``(None, [], {})`` is returned. Duplicate header labels get a numeric
    suffix so ``column_indices`` stays a clean 1:1 mapping.
    """
    rows_seen: List[List[str]] = []
    for r_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
        vals = [(str(c).strip() if c is not None else "") for c in row]
        rows_seen.append(vals)
        lowered = [v.lower() for v in vals]
        hits = sum(
            1 for v in lowered if any(h in v for h in _HEADER_HINTS)
        )
        if hits >= _HEADER_HITS:
            # Merge labels from the row above for any column blank in the
            # detected header row (covers the SOURCE/TARGET banner layout).
            merged = list(vals)
            if r_idx >= 2:
                prev = rows_seen[r_idx - 2]
                for c_idx in range(max(len(merged), len(prev))):
                    here = merged[c_idx] if c_idx < len(merged) else ""
                    above = prev[c_idx] if c_idx < len(prev) else ""
                    if not here and above:
                        if c_idx < len(merged):
                            merged[c_idx] = above
                        else:
                            merged.append(above)

            seen: Dict[str, int] = {}
            headers: List[str] = []
            col_idx: Dict[str, int] = {}
            for c_idx, label in enumerate(merged):
                if not label:
                    continue
                if label in seen:
                    seen[label] += 1
                    label = f"{label}_{seen[label]}"
                else:
                    seen[label] = 1
                headers.append(label)
                col_idx[label] = c_idx
            return r_idx, headers, col_idx
        if r_idx > 20:
            break  # Header should be near the top.
    return None, [], {}


def _find_field_name_column(col_idx: Dict[str, int]) -> Optional[int]:
    """Find the column index that names the field, for strikethrough scans.

    Workbooks with SOURCE+TARGET layouts duplicate this label. We prefer the
    *target* side: pick the column whose label contains 'column' or 'field'
    and has the **highest** index, on the assumption that target columns
    come after source columns.
    """
    candidates = [
        idx
        for label, idx in col_idx.items()
        if "column" in label.lower() or "field name" in label.lower()
    ]
    if not candidates:
        return None
    return max(candidates)


def _is_struck(cell: Cell) -> bool:
    """Return True when ``cell`` has strikethrough font formatting."""
    font = getattr(cell, "font", None)
    if font is None:
        return False
    return bool(getattr(font, "strike", False))
