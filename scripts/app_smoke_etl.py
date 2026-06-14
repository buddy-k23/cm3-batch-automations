#!/usr/bin/env python3
"""Valdo smoke ETL: parse -> validate -> insert into APP_INT."""

import json
import os
from pathlib import Path

import oracledb
import pandas as pd
from dotenv import load_dotenv

from src.parsers.pipe_delimited_parser import PipeDelimitedParser
from src.parsers.fixed_width_parser import FixedWidthParser
from src.parsers.validator import SchemaValidator

ROOT = Path(__file__).resolve().parents[1]


def _load_env():
    load_dotenv(ROOT / ".env")
    user = os.getenv("ORACLE_USER")
    pw = os.getenv("ORACLE_PASSWORD")
    dsn = os.getenv("ORACLE_DSN")
    if not all([user, pw, dsn]):
        raise RuntimeError("Missing ORACLE_USER/ORACLE_PASSWORD/ORACLE_DSN in environment")
    return user, pw, dsn


def _mapping_columns(mapping_path: Path):
    mapping = json.loads(mapping_path.read_text())
    mappings = mapping.get("mappings", [])
    expected = [m["source_column"] for m in mappings]
    required = [m["source_column"] for m in mappings if m.get("required", False)]
    return expected, required


def _validate(df: pd.DataFrame, mapping_path: Path, name: str):
    expected, required = _mapping_columns(mapping_path)
    result = SchemaValidator(expected_columns=expected, required_columns=required).validate(df)
    if not result["valid"]:
        raise RuntimeError(f"{name} schema validation failed: {result['errors']}")
    if result["warnings"]:
        print(f"[{name}] warnings: {result['warnings']}")


def _parse_customer() -> pd.DataFrame:
    parser = PipeDelimitedParser(str(ROOT / "data/samples/customers.txt"))
    df = parser.parse()
    _validate(df, ROOT / "config/mappings/customer_mapping.json", "customer")

    # Normalize for DB insert
    df = df.rename(columns={
        "customer_id": "CUSTOMER_ID",
        "first_name": "FIRST_NAME",
        "last_name": "LAST_NAME",
        "email": "EMAIL",
        "phone": "PHONE_NUMBER",
        "account_balance": "ACCOUNT_BALANCE",
        "status": "STATUS",
    })
    df["ACCOUNT_BALANCE"] = pd.to_numeric(df["ACCOUNT_BALANCE"], errors="coerce").fillna(0)
    return df


def _parse_transaction() -> pd.DataFrame:
    # matches docs/data/samples/README.md fixed-width spec
    specs = [
        ("transaction_id", 0, 13),
        ("customer_id", 13, 23),
        ("transaction_date", 23, 31),
        ("amount", 33, 42),
        ("transaction_type", 42, 50),
        ("description", 50, 70),
    ]
    parser = FixedWidthParser(str(ROOT / "data/samples/transactions.txt"), specs)
    df = parser.parse()

    _validate(df, ROOT / "config/mappings/transaction_mapping.json", "transaction")

    df = df.rename(columns={
        "transaction_id": "TRANSACTION_ID",
        "customer_id": "CUSTOMER_ID",
        "transaction_date": "TRANSACTION_DATE",
        "amount": "AMOUNT",
        "transaction_type": "TRANSACTION_TYPE",
        "description": "DESCRIPTION",
    })

    df["CUSTOMER_ID"] = df["CUSTOMER_ID"].str.strip()
    df["TRANSACTION_TYPE"] = df["TRANSACTION_TYPE"].str.strip().str.upper()
    df["DESCRIPTION"] = df["DESCRIPTION"].str.strip()

    # Accept YYYYMMDD or YYYY-MM-DD
    dt = pd.to_datetime(df["TRANSACTION_DATE"].str.strip(), format="%Y%m%d", errors="coerce")
    fallback = pd.to_datetime(df["TRANSACTION_DATE"].str.strip(), format="%Y-%m-%d", errors="coerce")
    df["TRANSACTION_DATE"] = dt.fillna(fallback)

    df["AMOUNT"] = pd.to_numeric(df["AMOUNT"].str.strip(), errors="coerce")
    return df


def run():
    user, pw, dsn = _load_env()
    customer_df = _parse_customer()
    trx_df = _parse_transaction()

    print(f"Parsed customers: {len(customer_df)} rows")
    print(f"Parsed transactions: {len(trx_df)} rows")

    conn = oracledb.connect(user=user, password=pw, dsn=dsn)
    cur = conn.cursor()

    # Upsert customers
    customer_rows = [tuple(x) for x in customer_df[[
        "CUSTOMER_ID", "FIRST_NAME", "LAST_NAME", "EMAIL", "PHONE_NUMBER", "ACCOUNT_BALANCE", "STATUS"
    ]].itertuples(index=False, name=None)]

    cur.executemany(
        """
        MERGE INTO CUSTOMER c
        USING (SELECT :1 CUSTOMER_ID, :2 FIRST_NAME, :3 LAST_NAME, :4 EMAIL, :5 PHONE_NUMBER, :6 ACCOUNT_BALANCE, :7 STATUS FROM dual) s
        ON (c.CUSTOMER_ID = s.CUSTOMER_ID)
        WHEN MATCHED THEN UPDATE SET
          c.FIRST_NAME = s.FIRST_NAME,
          c.LAST_NAME = s.LAST_NAME,
          c.EMAIL = s.EMAIL,
          c.PHONE_NUMBER = s.PHONE_NUMBER,
          c.ACCOUNT_BALANCE = s.ACCOUNT_BALANCE,
          c.STATUS = s.STATUS
        WHEN NOT MATCHED THEN INSERT (CUSTOMER_ID, FIRST_NAME, LAST_NAME, EMAIL, PHONE_NUMBER, ACCOUNT_BALANCE, STATUS)
          VALUES (s.CUSTOMER_ID, s.FIRST_NAME, s.LAST_NAME, s.EMAIL, s.PHONE_NUMBER, s.ACCOUNT_BALANCE, s.STATUS)
        """,
        customer_rows,
    )

    trx_clean = trx_df.dropna(subset=["TRANSACTION_ID", "CUSTOMER_ID", "TRANSACTION_DATE", "AMOUNT"])
    trx_rows = [tuple(x) for x in trx_clean[[
        "TRANSACTION_ID", "CUSTOMER_ID", "TRANSACTION_DATE", "AMOUNT", "TRANSACTION_TYPE", "DESCRIPTION"
    ]].itertuples(index=False, name=None)]

    cur.executemany(
        """
        MERGE INTO "TRANSACTION" t
        USING (SELECT :1 TRANSACTION_ID, :2 CUSTOMER_ID, :3 TRANSACTION_DATE, :4 AMOUNT, :5 TRANSACTION_TYPE, :6 DESCRIPTION FROM dual) s
        ON (t.TRANSACTION_ID = s.TRANSACTION_ID)
        WHEN MATCHED THEN UPDATE SET
          t.CUSTOMER_ID = s.CUSTOMER_ID,
          t.TRANSACTION_DATE = s.TRANSACTION_DATE,
          t.AMOUNT = s.AMOUNT,
          t.TRANSACTION_TYPE = s.TRANSACTION_TYPE,
          t.DESCRIPTION = s.DESCRIPTION
        WHEN NOT MATCHED THEN INSERT (TRANSACTION_ID, CUSTOMER_ID, TRANSACTION_DATE, AMOUNT, TRANSACTION_TYPE, DESCRIPTION)
          VALUES (s.TRANSACTION_ID, s.CUSTOMER_ID, s.TRANSACTION_DATE, s.AMOUNT, s.TRANSACTION_TYPE, s.DESCRIPTION)
        """,
        trx_rows,
    )

    conn.commit()

    cur.execute("select count(*) from CUSTOMER")
    customer_count = cur.fetchone()[0]
    cur.execute('select count(*) from "TRANSACTION"')
    trx_count = cur.fetchone()[0]

    cur.close()
    conn.close()

    print(f"DB CUSTOMER rows: {customer_count}")
    print(f"DB TRANSACTION rows: {trx_count}")
    print("Smoke ETL complete ✅")


if __name__ == "__main__":
    run()
