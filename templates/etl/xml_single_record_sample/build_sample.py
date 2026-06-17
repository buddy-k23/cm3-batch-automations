"""Deterministic generator for the XML single-record worked example (ADR 0019).

Emits a repeated-``<record>`` XML document under
``templates/etl/xml_single_record_sample/input.xml`` — one ``<record>`` per
line (so ``lxml``'s ``sourceline`` maps record *i* to file line *i+1*, the
``__source_row__`` the report shows). Each record carries a nested
``<customer>`` element with an ``id`` **attribute** (read via the XPath
``customer/@id``), child elements (``name``/``age``/``status``), and a
``<transactions>`` block whose repeated ``<transaction>`` children are
collapsed by the parser to an integer ``TXN_COUNT_count`` column.

The file is deliberately seeded with four defects the matching
``expected_report.json`` documents:

* **Record 6** -- empty ``<transactions/>`` (count 0), failing the
  ``json``-style ``xml_array_length`` minimum of 1.
* **Record 8** -- ``<status>FROZEN</status>`` (not in the valid_values allowlist).
* **Record 9** -- the ``customer`` element has **no ``id`` attribute**
  (absent path -> ``nested_required`` violation).
* **Record 10** -- ``<age>NaN</age>`` (non-numeric).

No DOCTYPE / no external entities -- the hardened parser rejects those by
design (see ``tests/unit/test_xml_parser.py``). Re-run with
``python3 build_sample.py``.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

# (id_or_None, name, age, status, n_transactions)
RECORDS = [
    ("C0001", "Alice Anderson", "34", "ACTIVE", 3),
    ("C0002", "Bob Brown", "51", "ACTIVE", 1),
    ("C0003", "Carol Chen", "28", "CLOSED", 2),
    ("C0004", "David Davis", "63", "SUSPENDED", 5),
    ("C0005", "Eve Evans", "45", "ACTIVE", 2),
    ("C0006", "Frank Fisher", "39", "ACTIVE", 0),       # empty transactions
    ("C0007", "Grace Gomez", "22", "CLOSED", 4),
    ("C0008", "Henry Hall", "57", "FROZEN", 1),          # invalid status
    (None, "Iris Iverson", "41", "ACTIVE", 2),           # missing customer/@id
    ("C0010", "Jack Jones", "NaN", "SUSPENDED", 1),      # non-numeric age
]


def _record_line(cust_id, name: str, age: str, status: str, n_txn: int) -> str:
    """Build one single-line ``<record>...</record>`` string.

    Args:
        cust_id: Customer id for the ``customer/@id`` attribute, or ``None`` to
            omit the attribute entirely (the missing-required fixture).
        name: Customer name (element text).
        age: Age element text (a non-numeric string for the bad-type row).
        status: Status element text.
        n_txn: Number of ``<transaction>`` children to emit.

    Returns:
        One line of XML (no surrounding whitespace) ending the record element.
    """
    id_attr = f' id="{escape(cust_id)}"' if cust_id is not None else ""
    txns = "".join(
        f'<transaction><amount>{round(100.0 + i * 10.5, 2)}</amount>'
        f"<currency>USD</currency></transaction>"
        for i in range(n_txn)
    )
    return (
        f'<record><customer{id_attr}>'
        f"<name>{escape(name)}</name>"
        f"<age>{escape(age)}</age>"
        f"<status>{escape(status)}</status>"
        f"</customer><transactions>{txns}</transactions></record>"
    )


def main() -> None:
    """Write the deterministic 10-record XML sample to ``input.xml``."""
    out_path = Path(__file__).resolve().parent / "input.xml"
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<records>"]
    lines += [_record_line(*rec) for rec in RECORDS]
    lines.append("</records>")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(RECORDS)} <record> elements to {out_path}")


if __name__ == "__main__":
    main()
