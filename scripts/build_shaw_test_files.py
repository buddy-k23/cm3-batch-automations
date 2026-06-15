"""Generate the manual SHAW TRANERT test fixtures.

This script materializes the three fixed-width test files referenced by
``tests/manual/TEST_PLAN.md``:

    tests/manual/fixtures/tranert_shaw_test_valid.txt
    tests/manual/fixtures/tranert_shaw_test_structural_failures.txt
    tests/manual/fixtures/tranert_shaw_test_clean_no_violations.txt

All three files share the TRANERT umbrella's GLOBAL record width — the
maximum ``position + length - 1`` across every record-type mapping
referenced by ``config/mappings/SHAW_TRANERT.yaml``. ORI is the widest
(636 chars), so every line in every fixture is padded right to 636.

The generator reads the real mapping JSONs at runtime; it is not pinned
to a snapshot of field lists. Re-run it whenever the mappings change and
the fixtures will track.

Usage
-----
    .venv311/bin/python scripts/build_shaw_test_files.py

The script is idempotent (overwrites the three files) and prints a short
summary on stdout. Exits non-zero if any line ends up the wrong width.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
MAPPINGS_DIR = REPO_ROOT / "config" / "mappings"
FIXTURE_DIR = REPO_ROOT / "tests" / "manual" / "fixtures"

# Record-type → mapping JSON file (mirrors SHAW_TRANERT.yaml).
RECORD_TYPE_MAPPINGS: Dict[str, str] = {
    "batch_header": "SHAW_TRANERT_BATCH_HEADER_mapping.json",
    "new1": "SHAW_TRANERT_NEW1_mapping.json",
    "cus": "SHAW_TRANERT_CUS_mapping.json",
    "ori": "SHAW_TRANERT_ORI_mapping.json",
    "cod": "SHAW_TRANERT_COD_mapping.json",
    "cbrs": "SHAW_TRANERT_CBRS_mapping.json",
    "rec": "SHAW_TRANERT_REC_mapping.json",
}

# Discriminator codes the umbrella expects.
TRN_COD_BY_TYPE: Dict[str, str] = {
    "new1": "32000",  # Could also be 32001; we use 32000 (NAS) for valid rows.
    "cus": "32005",
    "ori": "32010",
    "cod": "32025",
    "cbrs": "32040",
    "rec": "32075",
}

# Field name → static default (string, blank-padded).
# Numerics get zero-padded; dates get a fixed MMDDYYYY value; everything
# else falls back to the generic helpers below.
DATE_DEFAULT = "06012026"  # MMDDYYYY for 2026-06-01 — placeholder for date fields.

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_mapping(record_type: str) -> dict:
    """Load the JSON mapping for ``record_type``."""
    fn = MAPPINGS_DIR / RECORD_TYPE_MAPPINGS[record_type]
    if not fn.exists():
        raise FileNotFoundError(f"Missing mapping JSON: {fn}")
    with fn.open() as fh:
        return json.load(fh)


def expected_length(mapping: dict) -> int:
    """Return the right-most byte position used by the mapping (1-indexed)."""
    return max(f["position"] + f["length"] - 1 for f in mapping["fields"])


def global_record_width() -> int:
    """Compute the umbrella's global record width across all 7 record types."""
    return max(expected_length(load_mapping(rt)) for rt in RECORD_TYPE_MAPPINGS)


def field_value(
    field: dict,
    overrides: Dict[str, str],
    *,
    row_kind: str,
    seq: int,
) -> str:
    """Return a deterministic, type-correct value for ``field``.

    Args:
        field: A single mapping ``fields`` entry.
        overrides: Caller-supplied {field_name: literal_value}. Always wins.
        row_kind: One of the keys of ``RECORD_TYPE_MAPPINGS``.
        seq: 1-indexed row-of-its-kind counter, used to vary key columns.

    Returns:
        A string of exactly ``field['length']`` characters.
    """
    length = field["length"]
    name = field["name"]
    dtype = field["data_type"]

    if name in overrides:
        raw = overrides[name]
    elif dtype == "date":
        raw = DATE_DEFAULT  # 8 chars; date-field lengths are typically 8 or 10.
    elif dtype in ("decimal", "int", "num", "numeric"):
        raw = str(seq)
    elif dtype == "boolean":
        raw = "0"
    else:
        # String fallback — pad with blanks, no FILLER weirdness.
        raw = ""

    # Width fitting. Numerics zero-pad left; everything else blank-pads right.
    if dtype in ("decimal", "int", "num", "numeric", "boolean"):
        return raw[-length:].rjust(length, "0") if raw else "0" * length
    # Strings (and dates rendered as strings) are blank-padded right.
    return raw.ljust(length)[:length]


def build_record(
    record_type: str,
    width: int,
    *,
    overrides: Dict[str, str] | None = None,
    seq: int = 1,
) -> str:
    """Build a single fixed-width line for ``record_type``, padded to ``width``."""
    mapping = load_mapping(record_type)
    overrides = overrides or {}

    # Assemble byte-by-byte using the mapping spec, so gaps (FILLERs the
    # workbook didn't carve out) come through as blanks.
    line_chars: List[str] = [" "] * width
    for field in mapping["fields"]:
        start = field["position"] - 1
        end = start + field["length"]
        value = field_value(field, overrides, row_kind=record_type, seq=seq)
        line_chars[start:end] = list(value)
    return "".join(line_chars)


def header_overrides(item_count: int) -> Dict[str, str]:
    """BATCH_HEADER overrides anchoring the ITM-CNT-BRT assertion."""
    return {
        "BK-NUM-BRT": "00001",
        "APP-BRT": "200",
        "EFF-DAT-BRT": "06/01/2026",        # MM/DD/CCYY (length 10)
        "TRN-COD-BRT": "BATCH",
        "BAT-NUM-BRT": "0000001",
        "INP-SRC-COD-BRT": "001",
        "BAT-TYP-BRT": "32",                # NAS/EAS batches per spec
        "OPR-ID-BRT": "VALDOTST",
        "ITM-CNT-BRT": str(item_count).zfill(9),
        "DR-CR-AMT-BRT": "0.00".rjust(22),  # NAS/EAS batches use 0.00 per mapping
    }


def detail_overrides(record_type: str, seq: int) -> Dict[str, str]:
    """Shared key columns for detail records so reconciliation has stable keys."""
    return {
        "BK-NUM-ERT": "00001",
        "APP-ERT": "200",
        "LN-NUM-ERT": f"LN{seq:016d}",
        "EFF-DAT-ERT": "06012026",                # MMDDYYYY (length 8/10)
        "TRN-COD-ERT": TRN_COD_BY_TYPE[record_type],
    }


# ---------------------------------------------------------------------------
# File builders
# ---------------------------------------------------------------------------


def build_valid_file(width: int) -> List[str]:
    """Build the ~30-line VALID file: 1 BATCH_HEADER + 18 detail rows."""
    rows: List[str] = []
    rows.append(build_record("batch_header", width, overrides=header_overrides(18)))

    counts = [("new1", 5), ("cus", 4), ("ori", 3), ("cod", 2), ("cbrs", 2), ("rec", 2)]
    for rtype, n in counts:
        for i in range(1, n + 1):
            rows.append(build_record(rtype, width, overrides=detail_overrides(rtype, i), seq=i))
    return rows


def build_clean_file(width: int) -> List[str]:
    """Minimal clean baseline — 1 BATCH_HEADER + 1 NEW1 detail row."""
    rows: List[str] = []
    rows.append(build_record("batch_header", width, overrides=header_overrides(1)))
    rows.append(build_record("new1", width, overrides=detail_overrides("new1", 1), seq=1))
    return rows


def build_failure_file(width: int) -> List[str]:
    """Build the structural-failure fixture.

    Defects (deliberate):
      Line 1 — BATCH_HEADER with ITM-CNT-BRT=0099 but only 4 detail rows
               (header_trailer_count assertion fires).
      Line 2 — NEW1 line truncated to width-50 chars (length mismatch).
      Line 3 — NEW1 with non-numeric in BK-NUM-ERT (numeric-type violation).
      Line 4 — Detail line with unknown TRN-COD-ERT=99999 (unknown record type;
               default_action: error fires).
      Line 5 — Clean NEW1 row so the file isn't trivially malformed.
    """
    rows: List[str] = []
    rows.append(build_record("batch_header", width, overrides={**header_overrides(0), "ITM-CNT-BRT": "000000099"}))

    # Truncated NEW1 — chop off the last 50 chars after building it correctly.
    truncated = build_record("new1", width, overrides=detail_overrides("new1", 1), seq=1)
    rows.append(truncated[: width - 50])

    # Numeric type violation: BK-NUM-ERT (decimal, len 5) → "ABCDE".
    bad_numeric = build_record(
        "new1",
        width,
        overrides={**detail_overrides("new1", 2), "BK-NUM-ERT": "ABCDE"},
        seq=2,
    )
    rows.append(bad_numeric)

    # Unknown discriminator: build a NEW1 shape (so widths line up) but force
    # TRN-COD-ERT to 99999. The multi-record dispatcher should reject this row.
    unknown_disc = build_record(
        "new1",
        width,
        overrides={**detail_overrides("new1", 3), "TRN-COD-ERT": "99999"},
        seq=3,
    )
    rows.append(unknown_disc)

    # One clean NEW1 row so the file has at least one valid detail row.
    rows.append(build_record("new1", width, overrides=detail_overrides("new1", 4), seq=4))
    return rows


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def write_file(path: Path, rows: List[str], *, allow_short_lines: bool = False) -> None:
    """Write ``rows`` to ``path`` as a fixed-width file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(row + "\n")


def verify_widths(label: str, rows: List[str], width: int, *, allow_short_lines: bool = False) -> List[Tuple[int, int]]:
    """Return (line_no, actual_length) pairs that don't match ``width``."""
    bad: List[Tuple[int, int]] = []
    for i, row in enumerate(rows, start=1):
        if len(row) != width:
            bad.append((i, len(row)))
    if bad and not allow_short_lines:
        print(f"  WIDTH ERROR in {label}: {bad}", file=sys.stderr)
    return bad


def main() -> int:
    width = global_record_width()
    print(f"TRANERT global record width: {width}")

    # Build all three.
    clean = build_clean_file(width)
    valid = build_valid_file(width)
    fails = build_failure_file(width)

    # Write.
    write_file(FIXTURE_DIR / "tranert_shaw_test_clean_no_violations.txt", clean)
    write_file(FIXTURE_DIR / "tranert_shaw_test_valid.txt", valid)
    write_file(FIXTURE_DIR / "tranert_shaw_test_structural_failures.txt", fails)

    # Verify widths.
    rc = 0
    if verify_widths("clean", clean, width):
        rc = 1
    if verify_widths("valid", valid, width):
        rc = 1
    # The failure file deliberately contains one short line.
    fail_bad = verify_widths("failures", fails, width, allow_short_lines=True)
    if len(fail_bad) != 1:
        print(
            f"  EXPECTED exactly one short line in failures file, got {len(fail_bad)}: {fail_bad}",
            file=sys.stderr,
        )
        rc = 1

    print(f"  clean    -> {len(clean)} lines @ {width} chars each")
    print(f"  valid    -> {len(valid)} lines @ {width} chars each")
    print(f"  failures -> {len(fails)} lines (1 deliberately truncated)")
    if rc == 0:
        print("OK")
    return rc


if __name__ == "__main__":
    sys.exit(main())
