"""Deterministic generator for the JSON (NDJSON) single-record worked example.

Emits a 10-record newline-delimited JSON file under
``templates/etl/json_single_record_sample/input.ndjson``. Each line is one
JSON object (a customer record with a nested ``customer`` object and a
``transactions`` array). The file is deliberately seeded with three defects
that the matching ``expected_report.json`` documents:

* **Line 8** -- ``customer.status`` is ``"FROZEN"``, which is not in the
  mapping's ``valid_values=['ACTIVE','CLOSED','SUSPENDED']`` allowlist.
* **Line 9** -- ``customer.id`` is absent (the key is missing), exercising the
  ``required`` check on the ``CUSTOMER_ID`` field (json_path ``$.customer.id``).
* **Line 10** -- ``customer.age`` is the string ``"NaN"`` rather than a number,
  exercising the ``decimal`` data-type check on the ``AGE`` field.

The ``TXN_COUNT`` field maps the array path ``$.transactions[*]`` -- the parser
collapses it to an integer count column (``TXN_COUNT_count``) per ADR 0018 §3,
the load-bearing input to ``validate_json_array_length``. Line 6 carries an
empty ``transactions`` array (count 0) to make that count observable.

Re-run this script (``python3 build_sample.py``) to regenerate ``input.ndjson``
deterministically. ``mapping.json`` is hand-curated and committed alongside;
``expected_report.json`` is generated from the real engine (see the module
docstring of the sample's test).
"""

from __future__ import annotations

import json
from pathlib import Path

# Each tuple: (id_or_None, name, age, status, n_transactions)
# Lines 1-5, 7 are clean; lines 6, 8, 9, 10 carry documented edge cases/defects.
RECORDS = [
    ("C0001", "Alice Anderson", 34, "ACTIVE", 3),
    ("C0002", "Bob Brown", 51, "ACTIVE", 1),
    ("C0003", "Carol Chen", 28, "CLOSED", 2),
    ("C0004", "David Davis", 63, "SUSPENDED", 5),
    ("C0005", "Eve Evans", 45, "ACTIVE", 2),
    # Line 6 -- empty transactions array (count 0; observable to json_array_length).
    ("C0006", "Frank Fisher", 39, "ACTIVE", 0),
    ("C0007", "Grace Gomez", 22, "CLOSED", 4),
    # Line 8 -- invalid status ('FROZEN' not in the valid_values allowlist).
    ("C0008", "Henry Hall", 57, "FROZEN", 1),
    # Line 9 -- missing customer.id (required field absent).
    (None, "Iris Iverson", 41, "ACTIVE", 2),
    # Line 10 -- non-numeric age ("NaN" string instead of a number).
    ("C0010", "Jack Jones", "NaN", "SUSPENDED", 1),
]


def _build_record(
    cust_id, name: str, age, status: str, n_txn: int
) -> dict:
    """Assemble one customer record dict.

    Args:
        cust_id: Customer id, or ``None`` to omit the ``id`` key entirely
            (the missing-required-field fixture).
        name: Customer display name.
        age: Customer age (int for clean rows; a string for the bad-type row).
        status: Account status code.
        n_txn: Number of transaction objects to emit in ``transactions``.

    Returns:
        A nested dict ready for ``json.dumps`` as one NDJSON line.
    """
    customer: dict = {"name": name, "age": age, "status": status}
    if cust_id is not None:
        # ``id`` first when present; omitted entirely for the line-9 fixture.
        customer = {"id": cust_id, **customer}
    transactions = [
        {"amount": round(100.0 + i * 10.5, 2), "currency": "USD"}
        for i in range(n_txn)
    ]
    return {"customer": customer, "transactions": transactions}


def main() -> None:
    """Write the deterministic 10-record sample to ``input.ndjson``."""
    out_path = Path(__file__).resolve().parent / "input.ndjson"
    lines = [
        json.dumps(_build_record(*rec), separators=(",", ":"), sort_keys=False)
        for rec in RECORDS
    ]
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(lines)} NDJSON records to {out_path}")


if __name__ == "__main__":
    main()
