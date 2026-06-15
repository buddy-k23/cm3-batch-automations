"""Apply the SHAW manual-test seed to a SQLite or Oracle database.

This is the cross-backend runner referenced by ``tests/manual/TEST_PLAN.md``
Section 0 — Setup. It applies the SQL artefacts produced by
``scripts/build_shaw_test_files.py`` and prints a tidy row-count summary
so a BA / dev can confirm the seed landed.

SQLite (the default) needs zero external dependencies — it uses the
stdlib ``sqlite3`` module and writes ``tests/manual/valdo_test.db``
(overwriting the prior copy).

Oracle uses the ``oracledb`` package in thin mode (already a project
dependency). It expects three environment variables:

    ORACLE_DSN       e.g. localhost:1521/FREEPDB1
    ORACLE_USER      e.g. APP_INT
    ORACLE_PASSWORD  e.g. <secret>

Usage
-----
    # SQLite (default — Oracle-free local testing):
    python tests/manual/seed_db.py
    python tests/manual/seed_db.py --backend sqlite --drop-first

    # Oracle:
    ORACLE_DSN=localhost:1521/FREEPDB1 \\
    ORACLE_USER=app_int \\
    ORACLE_PASSWORD=<pwd> \\
        python tests/manual/seed_db.py --backend oracle --drop-first

Exit codes
----------
    0  success
    1  invocation error (bad flags, missing env vars)
    2  SQL execution error

The script is intentionally chatty: it lists which tables were created,
how many rows landed in each, and where the resulting artefact lives.
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SQL_DIR = REPO_ROOT / "tests" / "manual" / "sql"
DEFAULT_SQLITE_PATH = REPO_ROOT / "tests" / "manual" / "valdo_test.db"

# Tables we expect to exist after a successful seed. Used for the row-
# count summary and the --drop-first sweep.
EXPECTED_TABLES: List[str] = [
    "EXPECTED_BATCH_HEADER_TBL",
    "EXPECTED_NEW1_TBL",
    "EXPECTED_CUS_TBL",
    "EXPECTED_ORI_TBL",
    "EXPECTED_COD_TBL",
    "EXPECTED_CBRS_TBL",
    "EXPECTED_REC_TBL",
    "SHAW_COLLATERAL",
    "SHAW_FEE_MASTER",
    "SHAW_LOAN_MASTER",
    "SHAW_LOANS_NAME",
    "SHAW_TRANSACTIONS",
    "SHAW_TRANS_MASTER",
]

# Required row counts per table (matches the VALID fixture row plan).
EXPECTED_ROW_COUNTS = {
    "EXPECTED_BATCH_HEADER_TBL": 1,
    "EXPECTED_NEW1_TBL": 5,
    "EXPECTED_CUS_TBL": 4,
    "EXPECTED_ORI_TBL": 3,
    "EXPECTED_COD_TBL": 2,
    "EXPECTED_CBRS_TBL": 2,
    "EXPECTED_REC_TBL": 2,
    "SHAW_COLLATERAL": 5,
    "SHAW_FEE_MASTER": 5,
    "SHAW_LOAN_MASTER": 5,
    "SHAW_LOANS_NAME": 5,
    "SHAW_TRANSACTIONS": 5,
    "SHAW_TRANS_MASTER": 5,
}


# ---------------------------------------------------------------------------
# SQL parsing helpers
# ---------------------------------------------------------------------------


def split_sql_statements(sql: str, *, dialect: str) -> List[str]:
    """Split a SQL script into individual statements.

    For SQLite we use sqlite3's executescript, so callers won't usually
    invoke this. For Oracle we strip sqlplus directives (SET / WHENEVER /
    EXIT) and split on top-level semicolons -- DATE 'YYYY-MM-DD' literals
    contain no semicolons so naive splitting is safe.
    """
    statements: List[str] = []
    cleaned_lines: List[str] = []
    for raw_line in sql.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            cleaned_lines.append("")
            continue
        if stripped.startswith("--"):
            continue
        if dialect == "oracle":
            upper = stripped.upper()
            if (
                upper.startswith("SET ")
                or upper.startswith("WHENEVER ")
                or upper == "EXIT"
            ):
                continue
        cleaned_lines.append(raw_line)
    cleaned = "\n".join(cleaned_lines)

    buf: List[str] = []
    for ch in cleaned:
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


# ---------------------------------------------------------------------------
# SQLite backend
# ---------------------------------------------------------------------------


def seed_sqlite(*, drop_first: bool, db_path: Path) -> int:
    """Apply ``shaw_setup_sqlite.sql`` to ``db_path``.

    Returns 0 on success, 2 on SQL error.
    """
    sql_file = SQL_DIR / "shaw_setup_sqlite.sql"
    if not sql_file.exists():
        print(f"FATAL: SQL file not found: {sql_file}", file=sys.stderr)
        print(
            "Run `python scripts/build_shaw_test_files.py` to regenerate it.",
            file=sys.stderr,
        )
        return 2

    if drop_first and db_path.exists():
        print(f"--drop-first: removing existing {db_path}")
        db_path.unlink()

    db_path.parent.mkdir(parents=True, exist_ok=True)
    sql_text = sql_file.read_text(encoding="utf-8")

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        if drop_first:
            # File was deleted above so this is moot; keep a defensive
            # sweep for the no-file-but-stale-tables case (user passed
            # --drop-first but the file is a fresh empty DB).
            for tbl in EXPECTED_TABLES:
                cur.execute(f"DROP TABLE IF EXISTS {tbl};")
            conn.commit()
        cur.executescript(sql_text)
        conn.commit()
    except sqlite3.Error as exc:
        print(f"FATAL: SQLite error: {exc}", file=sys.stderr)
        conn.close()
        return 2

    rc = _print_summary_sqlite(conn, db_path)
    conn.close()
    return rc


def _print_summary_sqlite(conn: sqlite3.Connection, db_path: Path) -> int:
    """List tables + row counts. Return 0 if all expected rows present."""
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
    actual_tables = [row[0] for row in cur.fetchall()]

    print()
    print(f"Seeded SQLite database: {db_path}")
    print(f"Tables present ({len(actual_tables)}):")
    for tbl in actual_tables:
        print(f"  {tbl}")

    print()
    print("Row counts:")
    rc = 0
    for tbl in EXPECTED_TABLES:
        if tbl not in actual_tables:
            print(f"  {tbl:<30}  MISSING")
            rc = 2
            continue
        cur.execute(f"SELECT COUNT(*) FROM {tbl};")
        n = cur.fetchone()[0]
        expected = EXPECTED_ROW_COUNTS[tbl]
        flag = "OK" if n == expected else f"EXPECTED {expected}"
        print(f"  {tbl:<30}  {n:>3}  ({flag})")
        if n != expected:
            rc = 2
    return rc


# ---------------------------------------------------------------------------
# Oracle backend
# ---------------------------------------------------------------------------


def seed_oracle(*, drop_first: bool) -> int:
    """Apply ``shaw_setup.sql`` to the Oracle instance addressed by env vars.

    Requires ORACLE_DSN, ORACLE_USER, ORACLE_PASSWORD. Returns 0 on
    success, 1 on missing env vars, 2 on SQL error.
    """
    sql_file = SQL_DIR / "shaw_setup.sql"
    if not sql_file.exists():
        print(f"FATAL: SQL file not found: {sql_file}", file=sys.stderr)
        print(
            "Run `python scripts/build_shaw_test_files.py` to regenerate it.",
            file=sys.stderr,
        )
        return 2

    dsn = os.environ.get("ORACLE_DSN")
    user = os.environ.get("ORACLE_USER")
    pwd = os.environ.get("ORACLE_PASSWORD")
    missing = [n for n, v in (("ORACLE_DSN", dsn), ("ORACLE_USER", user),
                              ("ORACLE_PASSWORD", pwd)) if not v]
    if missing:
        print(f"FATAL: missing required env var(s): {', '.join(missing)}",
              file=sys.stderr)
        print(
            "Export ORACLE_DSN (e.g. localhost:1521/FREEPDB1), "
            "ORACLE_USER, ORACLE_PASSWORD and re-run.",
            file=sys.stderr,
        )
        return 1

    try:
        import oracledb  # type: ignore
    except ImportError:
        print("FATAL: oracledb not installed. Run `pip install oracledb`.",
              file=sys.stderr)
        return 1

    try:
        conn = oracledb.connect(user=user, password=pwd, dsn=dsn)
    except Exception as exc:  # pragma: no cover - depends on live DB
        print(f"FATAL: cannot connect to Oracle: {exc}", file=sys.stderr)
        return 2

    try:
        cur = conn.cursor()
        if drop_first:
            print("--drop-first: dropping existing SHAW_* and EXPECTED_*_TBL tables...")
            for tbl in EXPECTED_TABLES:
                try:
                    cur.execute(f"DROP TABLE {tbl} PURGE")
                    print(f"  dropped {tbl}")
                except Exception as exc:  # ORA-00942 = table does not exist
                    msg = str(exc).split("\n", 1)[0]
                    if "ORA-00942" in msg:
                        continue
                    print(f"  warning dropping {tbl}: {msg}", file=sys.stderr)
            conn.commit()

        sql_text = sql_file.read_text(encoding="utf-8")
        statements = split_sql_statements(sql_text, dialect="oracle")
        executed = 0
        for stmt in statements:
            upper = stmt.upper()
            if upper == "COMMIT":
                conn.commit()
                continue
            try:
                cur.execute(stmt)
                executed += 1
            except Exception as exc:  # pragma: no cover - depends on live DB
                print(f"FATAL: error executing statement #{executed + 1}:",
                      file=sys.stderr)
                print(stmt[:200], file=sys.stderr)
                print(f"  -> {exc}", file=sys.stderr)
                conn.rollback()
                conn.close()
                return 2
        conn.commit()
        print(f"Executed {executed} statements against {dsn}")
        rc = _print_summary_oracle(conn)
    finally:
        conn.close()
    return rc


def _print_summary_oracle(conn) -> int:  # pragma: no cover - depends on live DB
    cur = conn.cursor()
    print()
    print("Row counts:")
    rc = 0
    for tbl in EXPECTED_TABLES:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {tbl}")
            n = cur.fetchone()[0]
        except Exception as exc:
            msg = str(exc).split("\n", 1)[0]
            print(f"  {tbl:<30}  ERROR ({msg})")
            rc = 2
            continue
        expected = EXPECTED_ROW_COUNTS[tbl]
        flag = "OK" if n == expected else f"EXPECTED {expected}"
        print(f"  {tbl:<30}  {n:>3}  ({flag})")
        if n != expected:
            rc = 2
    return rc


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply the SHAW manual-test seed to SQLite or Oracle.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--backend",
        choices=("sqlite", "oracle"),
        default=os.environ.get("VALDO_TEST_DB", "sqlite"),
        help="Database backend (default: $VALDO_TEST_DB or 'sqlite').",
    )
    parser.add_argument(
        "--drop-first",
        action="store_true",
        help="Drop all SHAW_* + EXPECTED_*_TBL tables before re-creating.",
    )
    parser.add_argument(
        "--sqlite-path",
        type=Path,
        default=DEFAULT_SQLITE_PATH,
        help=f"Output path for SQLite DB (default: {DEFAULT_SQLITE_PATH}).",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    print(f"Backend: {args.backend}")
    if args.backend == "sqlite":
        return seed_sqlite(drop_first=args.drop_first, db_path=args.sqlite_path)
    return seed_oracle(drop_first=args.drop_first)


if __name__ == "__main__":
    sys.exit(main())
