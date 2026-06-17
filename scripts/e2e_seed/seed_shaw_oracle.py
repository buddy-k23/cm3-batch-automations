#!/usr/bin/env python3
"""Re-runnable SHAW E2E Oracle seeding script (LOCAL DEV ONLY).

Seeds the local ``valdo-oracle`` container (gvenzl/oracle-free, service
``FREEPDB1``) with the minimal SHAW staging + audit schema and synthetic,
internally-consistent data needed to UNBLOCK the DB-dependent E2E SHAW test
cases (TC-INT-SHAW-001/002/010/011/012/020/021/022 and the BASE anchor used by
the count cases).

This is **test scaffolding**, not production code. It does NOT touch any file
under ``src/`` or ``config/``. The mapping configs for the SHAW staging inputs
(``config/mappings/SHAW_*.json``) are 1-field TODO stubs, so this script builds
a **minimal-but-sensible** schema: an account/key column plus a couple of
descriptive columns, enough to demonstrate row COUNT(*) reconciliation.

Idempotency / load semantics
-----------------------------
* Tables: DROP-IF-EXISTS then CREATE (clean re-create on every run).
* Data: the synthetic source files are regenerated, then each staging table is
  loaded by reading the pipe-delimited file and inserting ``file_rows - 1``
  rows (one header line skipped) — mirroring the real ``staging_count ==
  file_rows - 1`` truncate-load model (Phase 2 Section 6).
* ``SHAW_LOAN_MASTER`` is loaded from ``loans-master`` (post-consolidation
  state assumed identical for this local seed; OQ-A2 is not resolvable from
  config). Its row count is the anchor ``BASE``.
* ``AUDIT_SQL_LOADER`` gets one row: ``source_system='shaw'``,
  ``table_name='shaw_loan_master'``, ``LOADED_ROW_COUNT = BASE``. The
  ``LOADED_ROW_COUNT`` column name is this seed's documented resolution of
  OQ-A1 for the local environment.

Connection (LOCAL DEV credentials; the password is a local container value,
NOT a secret):
    ORACLE_USER=APP_INT  ORACLE_PASSWORD=apppass
    ORACLE_DSN=localhost:1521/FREEPDB1  ORACLE_SCHEMA=APP_INT  DB_ADAPTER=oracle

Run:
    ./.venv/bin/python scripts/e2e_seed/seed_shaw_oracle.py
Environment overrides (optional):
    ORACLE_USER / ORACLE_PASSWORD / ORACLE_DSN  (defaults shown above)
    SHAW_SEED_DIR  (default: <repo>/.e2e_seed/source/shaw)
"""

from __future__ import annotations

import os
from pathlib import Path

import oracledb

# --- Connection params (local container defaults) --------------------------
ORACLE_USER = os.environ.get("ORACLE_USER", "APP_INT")
ORACLE_PASSWORD = os.environ.get("ORACLE_PASSWORD", "apppass")
ORACLE_DSN = os.environ.get("ORACLE_DSN", "localhost:1521/FREEPDB1")

# --- Local seed dirs (/app/software is not writable on this host) ----------
REPO_ROOT = Path(__file__).resolve().parents[2]
SEED_DIR = Path(os.environ.get("SHAW_SEED_DIR", REPO_ROOT / ".e2e_seed" / "source" / "shaw"))

# Fixed batch_date used for the synthetic delivery.
BATCH_DATE = "20260617"

# --- Synthetic source-file definitions -------------------------------------
# Each entry: (file_type, target_staging_table, header, [data rows]).
# Rows are pipe-delimited. staging_count will equal len(data rows) == file_rows-1.
# Accounts are zero-padded 18-char strings to echo the ACCT-NUM 7/18 layout.
def _acct(n: int) -> str:
    return str(n).zfill(18)


LOAN_MASTER_ROWS = [
    # acct_num | borrower_name | loan_status | balance
    f"{_acct(1001)}|ALPHA HOLDINGS LLC|ACTIVE|125000.00",
    f"{_acct(1002)}|BRAVO ENTERPRISES|ACTIVE|87500.50",
    f"{_acct(1003)}|CHARLIE INDUSTRIES|ACTIVE|240000.00",
    f"{_acct(1004)}|DELTA PARTNERS|CHARGEOFF|0.00",
    f"{_acct(1005)}|ECHO CAPITAL|ACTIVE|56000.25",
    f"{_acct(1006)}|FOXTROT GROUP|NEW|310000.00",
    f"{_acct(1007)}|GOLF VENTURES|ACTIVE|44250.75",
    f"{_acct(1008)}|HOTEL FUNDING CO|NEW|199900.00",
]

LOANS_NAME_ROWS = [
    # acct_num | name_type | full_name
    f"{_acct(1001)}|PRIMARY|ALPHA HOLDINGS LLC",
    f"{_acct(1002)}|PRIMARY|BRAVO ENTERPRISES",
    f"{_acct(1003)}|PRIMARY|CHARLIE INDUSTRIES",
    f"{_acct(1003)}|SECONDARY|CHARLIE SUBSIDIARY INC",
    f"{_acct(1005)}|PRIMARY|ECHO CAPITAL",
    f"{_acct(1006)}|PRIMARY|FOXTROT GROUP",
]

POSTED_TRANS_ROWS = [
    # acct_num | trans_code | trans_amount | trans_date
    f"{_acct(1001)}|300|1500.00|{BATCH_DATE}",
    f"{_acct(1001)}|300|-250.00|{BATCH_DATE}",
    f"{_acct(1002)}|300|980.10|{BATCH_DATE}",
    f"{_acct(1003)}|300|12000.00|{BATCH_DATE}",
    f"{_acct(1004)}|900|0.00|{BATCH_DATE}",
    f"{_acct(1005)}|300|320.45|{BATCH_DATE}",
    f"{_acct(1007)}|300|75.00|{BATCH_DATE}",
]

# table_name -> (header line, ddl columns, data rows, source file glob stem)
TABLES = {
    "SHAW_LOAN_MASTER": {
        "file_stem": "loans-master",
        "header": "ACCT_NUM|BORROWER_NAME|LOAN_STATUS|BALANCE",
        "ddl": """(
            ACCT_NUM      VARCHAR2(18) NOT NULL,
            BORROWER_NAME VARCHAR2(120),
            LOAN_STATUS   VARCHAR2(20),
            BALANCE       NUMBER(18,2),
            CONSTRAINT PK_SHAW_LOAN_MASTER PRIMARY KEY (ACCT_NUM)
        )""",
        "insert": "INSERT INTO SHAW_LOAN_MASTER (ACCT_NUM, BORROWER_NAME, LOAN_STATUS, BALANCE) VALUES (:1, :2, :3, :4)",
        "rows": LOAN_MASTER_ROWS,
    },
    "SHAW_LOANS_NAME": {
        "file_stem": "loans-name",
        "header": "ACCT_NUM|NAME_TYPE|FULL_NAME",
        "ddl": """(
            ACCT_NUM  VARCHAR2(18) NOT NULL,
            NAME_TYPE VARCHAR2(20),
            FULL_NAME VARCHAR2(120)
        )""",
        "insert": "INSERT INTO SHAW_LOANS_NAME (ACCT_NUM, NAME_TYPE, FULL_NAME) VALUES (:1, :2, :3)",
        "rows": LOANS_NAME_ROWS,
    },
    "SHAW_TRANSACTIONS": {
        "file_stem": "posted-trans",
        "header": "ACCT_NUM|TRANS_CODE|TRANS_AMOUNT|TRANS_DATE",
        "ddl": """(
            ACCT_NUM     VARCHAR2(18) NOT NULL,
            TRANS_CODE   VARCHAR2(5),
            TRANS_AMOUNT NUMBER(18,2),
            TRANS_DATE   VARCHAR2(8)
        )""",
        "insert": "INSERT INTO SHAW_TRANSACTIONS (ACCT_NUM, TRANS_CODE, TRANS_AMOUNT, TRANS_DATE) VALUES (:1, :2, :3, :4)",
        "rows": POSTED_TRANS_ROWS,
    },
}

AUDIT_TABLE = "AUDIT_SQL_LOADER"
AUDIT_DDL = """(
    AUDIT_ID        NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    SOURCE_SYSTEM   VARCHAR2(40)  NOT NULL,
    TABLE_NAME      VARCHAR2(60)  NOT NULL,
    LOADED_ROW_COUNT NUMBER       NOT NULL,
    LOAD_TIMESTAMP  TIMESTAMP DEFAULT SYSTIMESTAMP
)"""


def write_source_file(seed_dir: Path, file_stem: str, header: str, rows: list[str]) -> Path:
    """Write one pipe-delimited synthetic SHAW source file with a header row."""
    seed_dir.mkdir(parents=True, exist_ok=True)
    path = seed_dir / f"{file_stem}_{BATCH_DATE}.txt"
    with path.open("w", encoding="utf-8") as fh:
        fh.write(header + "\n")
        for r in rows:
            fh.write(r + "\n")
    return path


def drop_table(cur, name: str) -> None:
    try:
        cur.execute(f"DROP TABLE {name} CASCADE CONSTRAINTS PURGE")
    except oracledb.DatabaseError as exc:
        (err,) = exc.args
        if err.code != 942:  # ORA-00942: table or view does not exist
            raise


def load_table_from_file(cur, table: str, insert_sql: str, path: Path) -> int:
    """Read the file (skip header), split on '|', bulk insert. Returns row count."""
    lines = path.read_text(encoding="utf-8").splitlines()
    data = [ln for ln in lines if ln.strip()][1:]  # drop header
    parsed = [tuple(field.strip() for field in ln.split("|")) for ln in data]
    if parsed:
        cur.executemany(insert_sql, parsed)
    return len(parsed)


def main() -> None:
    print(f"Connecting as {ORACLE_USER}@{ORACLE_DSN} ...")
    conn = oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN)
    cur = conn.cursor()

    # 1. Write synthetic source files
    print(f"Writing synthetic source files under {SEED_DIR}")
    file_rows = {}
    for table, spec in TABLES.items():
        p = write_source_file(SEED_DIR, spec["file_stem"], spec["header"], spec["rows"])
        # file_rows includes the header line
        file_rows[table] = len(p.read_text(encoding="utf-8").splitlines())
        print(f"  {p.name}: {file_rows[table]} lines (1 header + {file_rows[table]-1} data)")

    # 2. (Re)create + load staging tables (truncate-load semantics via drop/create)
    base = None
    for table, spec in TABLES.items():
        drop_table(cur, table)
        cur.execute(f"CREATE TABLE {table} {spec['ddl']}")
        path = SEED_DIR / f"{spec['file_stem']}_{BATCH_DATE}.txt"
        n = load_table_from_file(cur, table, spec["insert"], path)
        conn.commit()
        print(f"  {table}: loaded {n} rows (expected file_rows-1 = {file_rows[table]-1})")
        assert n == file_rows[table] - 1, f"staging_count mismatch for {table}"
        if table == "SHAW_LOAN_MASTER":
            base = n

    # 3. (Re)create + seed AUDIT_SQL_LOADER with the anchor BASE
    drop_table(cur, AUDIT_TABLE)
    cur.execute(f"CREATE TABLE {AUDIT_TABLE} {AUDIT_DDL}")
    cur.execute(
        f"INSERT INTO {AUDIT_TABLE} (SOURCE_SYSTEM, TABLE_NAME, LOADED_ROW_COUNT) "
        f"VALUES ('shaw', 'shaw_loan_master', :1)",
        [base],
    )
    conn.commit()
    print(f"  {AUDIT_TABLE}: source_system='shaw' table_name='shaw_loan_master' LOADED_ROW_COUNT={base}")

    # 4. Verify anchor agreement
    cur.execute("SELECT COUNT(*) FROM SHAW_LOAN_MASTER")
    master_n = cur.fetchone()[0]
    cur.execute(
        f"SELECT LOADED_ROW_COUNT FROM {AUDIT_TABLE} "
        f"WHERE source_system='shaw' AND table_name='shaw_loan_master'"
    )
    audit_n = cur.fetchone()[0]
    print("\n=== ANCHOR VERIFICATION ===")
    print(f"  Source A  COUNT(*) SHAW_LOAN_MASTER      = {master_n}")
    print(f"  Source B  AUDIT_SQL_LOADER.LOADED_ROW_COUNT = {audit_n}")
    print(f"  AGREE: {master_n == audit_n}  (BASE = {master_n})")

    cur.close()
    conn.close()
    print("\nSeed complete.")


if __name__ == "__main__":
    main()
