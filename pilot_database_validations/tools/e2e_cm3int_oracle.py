#!/usr/bin/env python3
from __future__ import annotations

import csv
import os
from pathlib import Path

import oracledb

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "generated" / "e2e_cm3int"
SUMMARY = OUT / "summary.txt"
SOURCE_CSV = OUT / "source_query_output.csv"
FINAL_FILE = OUT / "final_output.txt"
VALIDATION_CSV = OUT / "validation_results.csv"

TABLE_ACCOUNTS = "CM3_DEMO_ACCOUNTS"
TABLE_CONTACTS = "CM3_DEMO_CONTACTS"
TABLE_ACCT_CONTACTS = "CM3_DEMO_ACCOUNT_CONTACTS"


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def pad(val: str, n: int) -> str:
    return (val or "")[:n].ljust(n)


def zpad(val: str, n: int) -> str:
    return (val or "")[:n].rjust(n, "0")


def make_line(row: dict) -> str:
    txn = row["TRANSACTION_CODE"]
    rel_seq = str(row.get("REL_SEQ") or "") if txn == "32005" else ""
    amt = ""
    if txn == "32010" and row.get("CHARGE_OFF_AMT") is not None:
        amt = str(int(round(float(row["CHARGE_OFF_AMT"]) * 100)))
    delq = str(row.get("FIRST_DELQ_DATE") or "") if txn == "32040" else ""

    return "".join(
        [
            pad("DTL", 3),
            pad("10003", 5),
            pad(txn, 5),
            pad(str(row["ACCOUNT_ID"]), 10),
            zpad(rel_seq, 3) if rel_seq else pad("", 3),
            zpad(amt, 10) if amt else pad("", 10),
            pad(delq, 8),
            pad(str(row.get("CONTACT_NAME") or ""), 20),
        ]
    )


def setup_demo_data(cur) -> None:
    for t in [TABLE_ACCT_CONTACTS, TABLE_CONTACTS, TABLE_ACCOUNTS]:
        try:
            cur.execute(f"DROP TABLE {t} PURGE")
        except Exception:
            pass

    cur.execute(
        f"""
        CREATE TABLE {TABLE_ACCOUNTS} (
          ACCOUNT_ID VARCHAR2(10) PRIMARY KEY,
          CHARGE_OFF_AMT NUMBER(12,2),
          FIRST_DELQ_DATE DATE,
          OPENED_DATE DATE
        )
        """
    )
    cur.execute(
        f"""
        CREATE TABLE {TABLE_CONTACTS} (
          CONTACT_ID VARCHAR2(12) PRIMARY KEY,
          FULL_NAME VARCHAR2(100)
        )
        """
    )
    cur.execute(
        f"""
        CREATE TABLE {TABLE_ACCT_CONTACTS} (
          ACCOUNT_ID VARCHAR2(10) NOT NULL,
          CONTACT_ID VARCHAR2(12) NOT NULL,
          RELATIONSHIP_CODE VARCHAR2(20),
          REL_SEQ NUMBER(3),
          CONSTRAINT PK_CM3_DEMO_AC PRIMARY KEY (ACCOUNT_ID, CONTACT_ID),
          CONSTRAINT FK_CM3_DEMO_AC1 FOREIGN KEY (ACCOUNT_ID) REFERENCES {TABLE_ACCOUNTS}(ACCOUNT_ID),
          CONSTRAINT FK_CM3_DEMO_AC2 FOREIGN KEY (CONTACT_ID) REFERENCES {TABLE_CONTACTS}(CONTACT_ID)
        )
        """
    )

    cur.execute(
        f"""
        INSERT INTO {TABLE_ACCOUNTS} (ACCOUNT_ID, CHARGE_OFF_AMT, FIRST_DELQ_DATE, OPENED_DATE)
        SELECT 'ACCT' || LPAD(LEVEL, 6, '0'),
               ROUND(DBMS_RANDOM.VALUE(500, 20000), 2),
               DATE '2025-01-01' + LEVEL,
               DATE '2023-12-01' - LEVEL
        FROM dual
        CONNECT BY LEVEL <= 100
        """
    )

    cur.execute(
        f"""
        INSERT INTO {TABLE_CONTACTS} (CONTACT_ID, FULL_NAME)
        SELECT 'C' || SUBSTR(a.ACCOUNT_ID, 5) || LPAD(c.seq, 2, '0'),
               'Customer_' || SUBSTR(a.ACCOUNT_ID, 8) || '_' || c.seq
        FROM {TABLE_ACCOUNTS} a
        JOIN (
          SELECT 1 AS seq FROM dual
          UNION ALL SELECT 2 FROM dual
          UNION ALL SELECT 3 FROM dual
        ) c ON c.seq <= (MOD(TO_NUMBER(SUBSTR(a.ACCOUNT_ID,5)),3) + 1)
        """
    )

    cur.execute(
        f"""
        INSERT INTO {TABLE_ACCT_CONTACTS} (ACCOUNT_ID, CONTACT_ID, RELATIONSHIP_CODE, REL_SEQ)
        SELECT a.ACCOUNT_ID,
               'C' || SUBSTR(a.ACCOUNT_ID, 5) || LPAD(c.seq, 2, '0'),
               CASE WHEN c.seq = 1 THEN 'PRIMARY' ELSE 'SECONDARY' END,
               999 - c.seq
        FROM {TABLE_ACCOUNTS} a
        JOIN (
          SELECT 1 AS seq FROM dual
          UNION ALL SELECT 2 FROM dual
          UNION ALL SELECT 3 FROM dual
        ) c ON c.seq <= (MOD(TO_NUMBER(SUBSTR(a.ACCOUNT_ID,5)),3) + 1)
        """
    )


def fetch_source_rows(cur) -> list[dict]:
    sql = f"""
    WITH account_txn AS (
      SELECT '32010' AS TRANSACTION_CODE,
             a.ACCOUNT_ID,
             CAST(NULL AS NUMBER(3)) AS REL_SEQ,
             a.CHARGE_OFF_AMT,
             CAST(NULL AS DATE) AS FIRST_DELQ_DATE,
             'ACCOUNT' AS CONTACT_NAME,
             1 AS ORDER_KEY
      FROM {TABLE_ACCOUNTS} a
    ),
    contact_txn AS (
      SELECT '32005' AS TRANSACTION_CODE,
             ac.ACCOUNT_ID,
             ac.REL_SEQ,
             CAST(NULL AS NUMBER(12,2)) AS CHARGE_OFF_AMT,
             CAST(NULL AS DATE) AS FIRST_DELQ_DATE,
             c.FULL_NAME AS CONTACT_NAME,
             2 AS ORDER_KEY
      FROM {TABLE_ACCT_CONTACTS} ac
      JOIN {TABLE_CONTACTS} c ON c.CONTACT_ID = ac.CONTACT_ID
    ),
    delq_txn AS (
      SELECT '32040' AS TRANSACTION_CODE,
             a.ACCOUNT_ID,
             CAST(NULL AS NUMBER(3)) AS REL_SEQ,
             CAST(NULL AS NUMBER(12,2)) AS CHARGE_OFF_AMT,
             a.FIRST_DELQ_DATE,
             'DELINQUENCY' AS CONTACT_NAME,
             3 AS ORDER_KEY
      FROM {TABLE_ACCOUNTS} a
    )
    SELECT TRANSACTION_CODE,
           ACCOUNT_ID,
           REL_SEQ,
           CHARGE_OFF_AMT,
           TO_CHAR(FIRST_DELQ_DATE,'YYYYMMDD') AS FIRST_DELQ_DATE,
           CONTACT_NAME
    FROM (
      SELECT * FROM account_txn
      UNION ALL
      SELECT * FROM contact_txn
      UNION ALL
      SELECT * FROM delq_txn
    )
    ORDER BY ACCOUNT_ID, ORDER_KEY, REL_SEQ DESC
    """
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    rows = []
    for r in cur.fetchall():
        rows.append({cols[i]: r[i] for i in range(len(cols))})
    return rows


def write_csv(rows: list[dict], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def write_final(rows: list[dict], path: Path) -> None:
    lines = [make_line(r) for r in rows]
    with path.open("w", encoding="utf-8") as f:
        f.write(f"HDR{len(lines):07d}\n")
        for ln in lines:
            f.write(ln + "\n")


def validate(rows: list[dict], final_path: Path) -> tuple[int, int]:
    expected = [make_line(r) for r in rows]
    lines = final_path.read_text(encoding="utf-8").splitlines()
    hdr = lines[0]
    actual = lines[1:]

    failures = 0
    out_rows = []

    declared = int(hdr[3:]) if hdr.startswith("HDR") else -1
    if declared != len(actual):
        failures += 1

    for i, exp in enumerate(expected):
        ok = i < len(actual) and exp == actual[i]
        if not ok:
            failures += 1
        out_rows.append({"row_number": i + 1, "status": "PASS" if ok else "FAIL"})

    with VALIDATION_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["row_number", "status"])
        w.writeheader()
        w.writerows(out_rows)

    return len(expected), failures


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    env_path = Path("/Users/buddy/.openclaw/workspace/cm3-batch-automations/.env")
    env = load_env(env_path)

    user = env.get("ORACLE_USER") or os.getenv("ORACLE_USER")
    password = env.get("ORACLE_PASSWORD") or os.getenv("ORACLE_PASSWORD")
    dsn = env.get("ORACLE_DSN") or os.getenv("ORACLE_DSN")

    conn = oracledb.connect(user=user, password=password, dsn=dsn)
    cur = conn.cursor()
    try:
        setup_demo_data(cur)
        conn.commit()

        rows = fetch_source_rows(cur)
        write_csv(rows, SOURCE_CSV)
        write_final(rows, FINAL_FILE)
        total, failures = validate(rows, FINAL_FILE)

        with SUMMARY.open("w", encoding="utf-8") as f:
            f.write("CM3INT Oracle E2E Summary\n")
            f.write("Accounts: 100\n")
            f.write(f"Source rows: {total}\n")
            f.write(f"Validation failures: {failures}\n")
            f.write(f"Result: {'PASS' if failures == 0 else 'FAIL'}\n")
            f.write(f"Source CSV: {SOURCE_CSV}\n")
            f.write(f"Final file: {FINAL_FILE}\n")
            f.write(f"Validation CSV: {VALIDATION_CSV}\n")

        print(SUMMARY.read_text(encoding="utf-8"))
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
