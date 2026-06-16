"""Deterministic generator for the fixed-width single-record worked example.

Emits a 10-row, 80-column fixed-width file under
``templates/etl/fixed_width_single_record_sample/input.txt``.  The file is
deliberately seeded with three defects that the matching ``expected_report.json``
documents:

* **Row 8** -- the BALANCE field carries non-numeric characters ("ABCD123456")
  to exercise the ``valdo validate`` format check (``FW_FMT_001`` against the
  ``9(10)`` format).
* **Row 9** -- the ACCT_STATUS field is ``XX``, which is not in the
  ``valid_values=['AC','CL','SU']`` allowlist (``FW_VAL_001``).
* **Row 10** -- the line is intentionally truncated to 75 characters
  (FILLER short by 5 chars) to exercise the row-length check
  (``FW_LEN_001``).

Re-run this script (``python3 build_sample.py``) to regenerate ``input.txt``
deterministically.  The script writes no other files; ``mapping.json`` and
``expected_report.json`` are hand-curated and committed alongside.

Schema (1-indexed, total 80 chars):

| Field        | Positions | Length | Type   | Notes                          |
|--------------|-----------|--------|--------|--------------------------------|
| ACCT_NUM     | 1-5       | 5      | num    | zero-padded                    |
| ACCT_NAME    | 6-25      | 20     | str    | blank-padded                   |
| BRANCH_CODE  | 26-30     | 5      | num    | zero-padded                    |
| OPEN_DATE    | 31-40     | 10     | date   | MM/DD/CCYY                     |
| BALANCE      | 41-50     | 10     | num    | zero-padded                    |
| ACCT_STATUS  | 51-52     | 2      | str    | valid_values=['AC','CL','SU']  |
| FILLER       | 53-80     | 28     | str    | blanks                         |
"""

from __future__ import annotations

from pathlib import Path

# (acct_num, acct_name, branch_code, open_date, balance, acct_status)
# Rows 1-7 are clean; rows 8, 9, 10 carry the documented defects.
ROWS = [
    ("00001", "ALICE ANDERSON", "00101", "01/15/2024", "0000100000", "AC"),
    ("00002", "BOB BROWN", "00101", "02/20/2024", "0000250075", "AC"),
    ("00003", "CAROL CHEN", "00102", "03/10/2024", "0000050050", "CL"),
    ("00004", "DAVID DAVIS", "00102", "04/05/2024", "0000175025", "SU"),
    ("00005", "EVE EVANS", "00103", "05/12/2024", "0000999999", "AC"),
    ("00006", "FRANK FISHER", "00103", "06/18/2024", "0000425000", "AC"),
    ("00007", "GRACE GOMEZ", "00104", "07/22/2024", "0000087550", "CL"),
    # Row 8 -- non-numeric BALANCE (FW_FMT_001 against 9(10) format).
    ("00008", "HENRY HALL", "00104", "08/30/2024", "ABCD123456", "AC"),
    # Row 9 -- invalid ACCT_STATUS (FW_VAL_001 -- 'XX' not in ['AC','CL','SU']).
    ("00009", "IRIS IVERSON", "00105", "09/14/2024", "0000300000", "XX"),
    # Row 10 -- truncated line (FW_LEN_001 -- 75 chars instead of 80).
    ("00010", "JACK JONES", "00105", "10/03/2024", "0000150000", "SU"),
]


def _build_line(
    acct_num: str,
    acct_name: str,
    branch_code: str,
    open_date: str,
    balance: str,
    acct_status: str,
    truncate_filler: bool = False,
) -> str:
    """Assemble one 80-char fixed-width line from the field values.

    Args:
        acct_num: 5-char zero-padded account number.
        acct_name: account holder name; blank-padded right to 20 chars.
        branch_code: 5-char zero-padded branch code.
        open_date: 10-char MM/DD/CCYY date string.
        balance: 10-char zero-padded balance.
        acct_status: 2-char status code.
        truncate_filler: When True, the FILLER field is left blank (length 0)
            so the line is short by exactly 23 chars (final length 57).
            Used to construct the truncated row 10 fixture -- set the actual
            length explicitly via the ROWS table.

    Returns:
        A string sized to the configured width (default 80; 75 for the
        truncated row 10 case).
    """
    fields = (
        acct_num.ljust(5)[:5]
        + acct_name.ljust(20)[:20]
        + branch_code.ljust(5)[:5]
        + open_date.ljust(10)[:10]
        + balance.ljust(10)[:10]
        + acct_status.ljust(2)[:2]
    )
    # FILLER fills to 80 chars (28 blanks); for the truncated row, pad less.
    filler_width = 23 if truncate_filler else 28
    return fields + (" " * filler_width)


def main() -> None:
    """Write the deterministic 10-row sample to ``input.txt``."""
    out_path = Path(__file__).resolve().parent / "input.txt"
    lines: list[str] = []
    for idx, (acct, name, branch, dt, bal, status) in enumerate(ROWS, start=1):
        truncate = idx == 10  # Row 10 is the documented truncated line.
        line = _build_line(acct, name, branch, dt, bal, status, truncate_filler=truncate)
        # Sanity: row 10 must be 75 chars; all others 80.
        expected_len = 75 if truncate else 80
        assert len(line) == expected_len, (
            f"Row {idx} built at length {len(line)}, expected {expected_len}"
        )
        lines.append(line)

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(lines)} rows to {out_path}")


if __name__ == "__main__":
    main()
