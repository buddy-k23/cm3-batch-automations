"""Map rt_32010 group-relative row numbers to absolute file line numbers.

The error CSV reports row numbers relative to the per-record-type group
(i.e. row 15 = the 15th line dispatched to rt_32010), not absolute line
numbers in the source file. This script shows the mapping.
"""
from pathlib import Path

SAMPLE = Path("data/samples/tranert_shaw_20260521.txt")
TARGET_CODE = "32010"
DISC_POS = 169   # 0-indexed start of TRN-COD-ERT (position 170, length 5)
DISC_LEN = 5
REP_POS = 308    # 0-indexed start of REP-TYP-ORI (position 309, length 3)
REP_LEN = 3

lines = SAMPLE.read_text(encoding="utf-8", errors="replace").splitlines()
print(f"Total lines in file: {len(lines)}")

group_abs = []   # absolute 1-indexed line numbers for rt_32010 rows
for i, line in enumerate(lines):
    if len(line) >= DISC_POS + DISC_LEN:
        code = line[DISC_POS:DISC_POS + DISC_LEN]
        if code == TARGET_CODE:
            group_abs.append(i + 1)   # 1-indexed

print(f"rt_32010 group size: {len(group_abs)}")
print()
print(f"{'Group row':>10}  {'Abs line':>9}  {'REP-TYP-ORI':>12}  {'Line len':>9}")
print("-" * 50)
for group_row in range(1, len(group_abs) + 1):
    abs_line = group_abs[group_row - 1]
    line = lines[abs_line - 1]
    rep = line[REP_POS:REP_POS + REP_LEN] if len(line) >= REP_POS + REP_LEN else "<short>"
    # Only print rows with empty REP-TYP-ORI (show all of them)
    if not rep.strip():
        print(f"{group_row:>10}  {abs_line:>9}  {rep!r:>12}  {len(line):>9} <-- EMPTY")
