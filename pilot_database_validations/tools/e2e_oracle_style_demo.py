#!/usr/bin/env python3
from __future__ import annotations

import csv
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "generated" / "e2e_demo"
DB = OUT / "demo.db"
SOURCE_CSV = OUT / "source_query_output.csv"
FINAL_FILE = OUT / "final_output.txt"
VALIDATION_CSV = OUT / "validation_results.csv"
SUMMARY = OUT / "summary.txt"


def pad(val: str, n: int) -> str:
    s = (val or "")[:n]
    return s.ljust(n)


def zpad(val: str, n: int) -> str:
    s = (val or "")[:n]
    return s.rjust(n, "0")


def make_line(row: dict) -> str:
    txn = row["transaction_code"]
    rel_seq = str(row.get("rel_seq") or "") if txn == "32005" else ""
    amt = str(int(round(float(row.get("charge_off_amt") or 0) * 100))) if txn == "32010" else ""
    delq = str(row.get("first_delq_date") or "") if txn == "32040" else ""
    parts = [
        pad("DTL", 3),
        pad("10003", 5),
        pad(txn, 5),
        pad(str(row["account_id"]), 10),
        zpad(rel_seq, 3) if rel_seq else pad("", 3),
        zpad(amt, 10) if amt else pad("", 10),
        pad(delq, 8),
        pad(str(row.get("contact_name") or ""), 20),
    ]
    return "".join(parts)


def setup_db(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.executescript(
        """
        DROP TABLE IF EXISTS accounts;
        DROP TABLE IF EXISTS contacts;
        DROP TABLE IF EXISTS account_contacts;

        CREATE TABLE accounts (
          account_id TEXT PRIMARY KEY,
          charge_off_amt REAL,
          first_delq_date TEXT,
          opened_date TEXT
        );

        CREATE TABLE contacts (
          contact_id TEXT PRIMARY KEY,
          full_name TEXT
        );

        CREATE TABLE account_contacts (
          account_id TEXT,
          contact_id TEXT,
          relationship_code TEXT,
          rel_seq INTEGER,
          PRIMARY KEY (account_id, contact_id)
        );
        """
    )

    rng = random.Random(42)
    base = date(2025, 1, 1)

    for i in range(1, 101):
      acct = f"ACCT{i:06d}"
      amt = round(rng.uniform(500, 20000), 2)
      delq = (base + timedelta(days=i)).strftime("%Y%m%d")
      opened = (base - timedelta(days=400 + i)).isoformat()
      cur.execute("INSERT INTO accounts VALUES (?,?,?,?)", (acct, amt, delq, opened))

      # primary + up to 2 additional contacts
      contact_count = 1 + (i % 3)
      seq_start = 998
      for c in range(contact_count):
          cid = f"C{i:06d}{c+1:02d}"
          name = f"Customer_{i:03d}_{c+1}"
          rel = "PRIMARY" if c == 0 else "SECONDARY"
          rel_seq = seq_start - c
          cur.execute("INSERT INTO contacts VALUES (?,?)", (cid, name))
          cur.execute(
              "INSERT INTO account_contacts VALUES (?,?,?,?)",
              (acct, cid, rel, rel_seq),
          )

    conn.commit()


def run_query(conn: sqlite3.Connection) -> list[dict]:
    sql = """
    WITH account_txn AS (
      SELECT '32010' AS transaction_code,
             a.account_id,
             NULL AS rel_seq,
             a.charge_off_amt,
             NULL AS first_delq_date,
             'ACCOUNT' AS contact_name,
             1 AS order_key
      FROM accounts a
    ),
    contact_txn AS (
      SELECT '32005' AS transaction_code,
             ac.account_id,
             ac.rel_seq,
             NULL AS charge_off_amt,
             NULL AS first_delq_date,
             c.full_name AS contact_name,
             2 AS order_key
      FROM account_contacts ac
      JOIN contacts c ON c.contact_id = ac.contact_id
    ),
    delq_txn AS (
      SELECT '32040' AS transaction_code,
             a.account_id,
             NULL AS rel_seq,
             NULL AS charge_off_amt,
             a.first_delq_date,
             'DELINQUENCY' AS contact_name,
             3 AS order_key
      FROM accounts a
    )
    SELECT transaction_code, account_id, rel_seq, charge_off_amt, first_delq_date, contact_name
    FROM (
      SELECT * FROM account_txn
      UNION ALL
      SELECT * FROM contact_txn
      UNION ALL
      SELECT * FROM delq_txn
    )
    ORDER BY account_id, order_key, rel_seq DESC
    """

    cur = conn.cursor()
    rows = []
    cols = [d[0] for d in cur.execute(sql).description]
    for r in cur.execute(sql).fetchall():
        rows.append({cols[i]: r[i] for i in range(len(cols))})
    return rows


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def write_final_file(rows: list[dict], path: Path) -> None:
    lines = [make_line(r) for r in rows]
    with path.open("w", encoding="utf-8") as f:
        f.write(f"HDR{len(lines):07d}\n")
        for ln in lines:
            f.write(ln + "\n")


def parse_line(line: str) -> dict:
    return {
        "record_type": line[0:3].strip(),
        "bank_code": line[3:8].strip(),
        "transaction_code": line[8:13].strip(),
        "account_id": line[13:23].strip(),
        "rel_seq": line[23:26].strip(),
        "charge_off_amt_cents": line[26:36].strip(),
        "first_delq_date": line[36:44].strip(),
        "contact_name": line[44:64].strip(),
    }


def validate(rows: list[dict], final_file: Path) -> tuple[int, int]:
    expected = [make_line(r) for r in rows]
    lines = final_file.read_text(encoding="utf-8").splitlines()
    header = lines[0]
    actual = lines[1:]

    fails = 0
    validations = []

    declared = int(header[3:]) if header.startswith("HDR") else -1
    if declared != len(actual):
        fails += 1

    for i, exp in enumerate(expected):
        ok = i < len(actual) and actual[i] == exp
        if not ok:
            fails += 1
        validations.append({"row_number": i + 1, "status": "PASS" if ok else "FAIL"})

    with VALIDATION_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["row_number", "status"])
        w.writeheader()
        w.writerows(validations)

    return len(expected), fails


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB)
    try:
        setup_db(conn)
        rows = run_query(conn)
        write_csv(rows, SOURCE_CSV)
        write_final_file(rows, FINAL_FILE)
        total, fails = validate(rows, FINAL_FILE)

        with SUMMARY.open("w", encoding="utf-8") as f:
            f.write("E2E Oracle-style Demo Summary\n")
            f.write(f"Accounts: 100\n")
            f.write(f"Source rows from query: {total}\n")
            f.write(f"Validation failures: {fails}\n")
            f.write(f"Result: {'PASS' if fails == 0 else 'FAIL'}\n")
            f.write(f"DB: {DB}\n")
            f.write(f"Source CSV: {SOURCE_CSV}\n")
            f.write(f"Final file: {FINAL_FILE}\n")
            f.write(f"Validation CSV: {VALIDATION_CSV}\n")

        print(SUMMARY.read_text(encoding="utf-8"))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
