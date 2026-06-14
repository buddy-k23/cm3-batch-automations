"""Inspect the TRANERT_SHAW_Mappings.xlsx workbook structure.

Diagnostic helper to determine whether the TRANERT workbook is the real
BA spec and what shape it has compared to ATOCTRAN_SHAW_Mappings.xlsx.
This is a developer aid; safe to delete after TRANERT generator work
is signed off.
"""

from __future__ import annotations

import sys
from pathlib import Path

import openpyxl


def _trunc(value, length: int = 30) -> str:
    """Truncate a cell value to *length* chars for tabular display."""
    if value is None:
        return ""
    text = str(value).strip()
    return text if len(text) <= length else text[: length - 1] + "…"


def _dump_sheet(ws, max_rows: int, max_cols: int) -> None:
    """Print the first *max_rows* x *max_cols* of *ws* in a fixed-width table."""
    rows = list(ws.iter_rows(min_row=1, max_row=max_rows, max_col=max_cols))
    if not rows:
        print("  (empty)")
        return
    # Compute per-column widths from the truncated values.
    widths = [0] * max_cols
    matrix = []
    for row in rows:
        line = []
        for i, cell in enumerate(row[:max_cols]):
            text = _trunc(cell.value)
            line.append(text)
            widths[i] = max(widths[i], len(text))
        matrix.append(line)
    for line in matrix:
        print("  " + " | ".join(text.ljust(widths[i]) for i, text in enumerate(line)))


def main() -> int:
    path = Path("mappings/excel/TRANERT_SHAW_Mappings.xlsx")
    if not path.exists():
        print(f"Not found: {path}")
        return 1

    wb = openpyxl.load_workbook(path, data_only=True)
    print(f"Workbook: {path}")
    print(f"Total sheets: {len(wb.sheetnames)}\n")

    # First: the summary sheets at the top (Batch Header + record-name sheets).
    summary_sheets = ["Batch Header", "NEW1", "CUS", "ORI", "COD", "VR", "CBRS", "REC", "CON"]
    layout_sheets = [n for n in wb.sheetnames if "-" in n and n[0].isalpha()]

    print(f"Summary sheets present: {[n for n in summary_sheets if n in wb.sheetnames]}")
    print(f"Per-record-type layout sheets: {layout_sheets}")
    print()

    # Dump the Batch Header (the candidate discriminator description).
    if "Batch Header" in wb.sheetnames:
        print("=== Batch Header (first 24 rows, cols A-I) ===")
        _dump_sheet(wb["Batch Header"], max_rows=24, max_cols=9)
        print()

    # Dump the first 6 rows of one summary sheet to see its layout.
    if "NEW1" in wb.sheetnames:
        print("=== NEW1 summary (first 8 rows, cols A-I) ===")
        _dump_sheet(wb["NEW1"], max_rows=8, max_cols=9)
        print()

    # Dump the first 10 rows of a per-record-type layout sheet to see its
    # header structure — this is what the CSV generator will need to read.
    candidate_layouts = [n for n in wb.sheetnames if " - 32" in n]
    if candidate_layouts:
        sample = candidate_layouts[0]
        print(f"=== {sample} (first 8 rows, cols A-N) ===")
        _dump_sheet(wb[sample], max_rows=8, max_cols=14)
        print()

    # Confirm the discriminator (TRN-COD-*RT) position and length across
    # all summary sheets.
    print("=== Discriminator (TRN-COD-*RT) per summary sheet ===")
    for name in ["NEW1", "CUS", "ORI", "COD", "VR", "CBRS", "REC", "CON",
                 "Batch Header"]:
        if name not in wb.sheetnames:
            continue
        ws = wb[name]
        for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=9):
            cell0 = row[0].value
            if cell0 and "TRN-COD" in str(cell0).upper():
                pos = row[2].value if len(row) > 2 else None
                length = row[3].value if len(row) > 3 else None
                dtype = row[4].value if len(row) > 4 else None
                vv = row[7].value if len(row) > 7 else None
                print(
                    f"  {name}: field={cell0} pos={pos} length={length} "
                    f"type={dtype} valid_values={_trunc(vv, 40)!r}"
                )
                break
    print()

    # Show the Header→Detail reconciliation candidate fields.
    print("=== Cross-type rule candidates (Batch Header ITM-CNT / DR-CR-AMT) ===")
    if "Batch Header" in wb.sheetnames:
        ws = wb["Batch Header"]
        for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=9):
            cell0 = row[0].value
            if cell0 and any(k in str(cell0).upper() for k in ("ITM-CNT", "DR-CR-AMT")):
                pos = row[2].value if len(row) > 2 else None
                length = row[3].value if len(row) > 3 else None
                dtype = row[4].value if len(row) > 4 else None
                fmt = row[5].value if len(row) > 5 else None
                print(
                    f"  {cell0}: pos={pos} length={length} "
                    f"type={dtype} fmt={fmt}"
                )
    print()

    # Show the amount field in each record-type summary.
    print("=== Per-record amount fields (*AMT* in summary sheets) ===")
    for name in ["NEW1", "CUS", "ORI", "COD", "VR", "CBRS", "REC", "CON"]:
        if name not in wb.sheetnames:
            continue
        ws = wb[name]
        for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=9):
            cell0 = row[0].value
            if cell0 and "AMT" in str(cell0).upper():
                pos = row[2].value if len(row) > 2 else None
                length = row[3].value if len(row) > 3 else None
                print(f"  {name}: field={cell0} pos={pos} length={length}")
                break
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
