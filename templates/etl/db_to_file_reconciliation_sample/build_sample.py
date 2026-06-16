"""Seed a SQLite DB and exercise the DB-to-file reconciliation sample.

This is the worked-example driver for the BA-facing DB-to-file
reconciliation template (S7-1, #375). It is intentionally Oracle-free
so a BA on a laptop without database access can still see the contract
end-to-end.

What this script does:

1. Creates a temporary SQLite database under
   ``templates/etl/db_to_file_reconciliation_sample/sample.db`` and
   seeds it with five SAMPLE_CUSTOMER rows. The rows match the
   committed ``output.txt`` exactly so the reconciliation produces
   **zero violations** -- this is the contract pinned by
   ``tests/unit/test_etl_templates.py``.
2. Runs ``extract.sqlite.sql`` against that database to produce the
   expected-rows DataFrame.
3. Compares the DataFrame against ``output.txt`` using
   :class:`src.comparators.file_comparator.FileComparator` (the same
   engine ``valdo db-compare`` uses internally after the Oracle extract).
4. Writes the result to ``expected_report.json`` so the test can pin it.

Usage:
    python templates/etl/db_to_file_reconciliation_sample/build_sample.py

Exit codes:
    0 success (zero violations as designed)
    1 invocation or schema error
    2 SQL execution or comparison error
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
# Allow ``from src...`` imports when this script is run directly from the
# sample directory (BAs invoke it via ``python templates/etl/.../build_sample.py``).
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd

SAMPLE_DIR = Path(__file__).resolve().parent
SQLITE_PATH = SAMPLE_DIR / "sample.db"
EXTRACT_SQL = SAMPLE_DIR / "extract.sqlite.sql"
OUTPUT_FILE = SAMPLE_DIR / "output.txt"
EXPECTED_REPORT = SAMPLE_DIR / "expected_report.json"

# Five sample customer rows. These must match output.txt exactly --
# zero-violation is the documented contract.
SAMPLE_ROWS = [
    ("CUST000001", "Alice Anderson", "alice@example.com", "1250.00"),
    ("CUST000002", "Bob Brown", "bob@example.com", "3400.50"),
    ("CUST000003", "Carol Chen", "carol@example.com", "75.25"),
    ("CUST000004", "Dan Davis", "dan@example.com", "9800.00"),
    ("CUST000005", "Eve Edwards", "eve@example.com", "540.10"),
]


def seed_sqlite(db_path: Path) -> None:
    """Create SAMPLE_CUSTOMER and insert the five fixed sample rows."""
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE SAMPLE_CUSTOMER (
                CUSTOMER_ID TEXT NOT NULL PRIMARY KEY,
                NAME        TEXT NOT NULL,
                EMAIL       TEXT,
                BALANCE     TEXT NOT NULL
            )
            """
        )
        cur.executemany(
            "INSERT INTO SAMPLE_CUSTOMER (CUSTOMER_ID, NAME, EMAIL, BALANCE) "
            "VALUES (?, ?, ?, ?)",
            SAMPLE_ROWS,
        )
        conn.commit()
    finally:
        conn.close()


def run_extract(db_path: Path, sql_file: Path) -> pd.DataFrame:
    """Execute the extract SQL and return the result as a DataFrame."""
    sql = sql_file.read_text(encoding="utf-8")
    conn = sqlite3.connect(str(db_path))
    try:
        return pd.read_sql_query(sql, conn, dtype=str)
    finally:
        conn.close()


def load_output_file(path: Path) -> pd.DataFrame:
    """Read the pipe-delimited output file into a DataFrame of strings."""
    return pd.read_csv(path, sep="|", dtype=str, keep_default_na=False)


def reconcile(db_df: pd.DataFrame, file_df: pd.DataFrame) -> dict:
    """Run the generic file comparator over the DB extract vs the file.

    Returns a JSON-serialisable result dict mirroring the shape produced
    by ``compare_db_to_file`` (workflow + compare).
    """
    # Local import keeps this script lightweight when imported by tests.
    from src.comparators.file_comparator import FileComparator

    comparator = FileComparator(
        db_df, file_df, key_columns=["CUSTOMER_ID"]
    )
    compare_result = comparator.compare(detailed=True)

    # Normalise DataFrame fields for JSON serialisation.
    only_left = compare_result["only_in_file1"]
    only_right = compare_result["only_in_file2"]
    only_left_records = (
        only_left.to_dict(orient="records")
        if hasattr(only_left, "to_dict")
        else list(only_left)
    )
    only_right_records = (
        only_right.to_dict(orient="records")
        if hasattr(only_right, "to_dict")
        else list(only_right)
    )

    workflow_status = (
        "passed"
        if not only_left_records
        and not only_right_records
        and not compare_result["differences"]
        else "failed"
    )

    return {
        "workflow": {
            "status": workflow_status,
            "db_rows_extracted": len(db_df),
            "query_or_table": "extract.sqlite.sql",
        },
        "compare": {
            "only_in_file1": only_left_records,
            "only_in_file2": only_right_records,
            "differences": compare_result["differences"],
            "total_rows_file1": compare_result["total_rows_file1"],
            "total_rows_file2": compare_result["total_rows_file2"],
            "matching_rows": compare_result["matching_rows"],
            "rows_with_differences": compare_result["rows_with_differences"],
            "field_statistics": compare_result["field_statistics"],
        },
    }


def main() -> int:
    if not EXTRACT_SQL.exists():
        print(f"FATAL: extract SQL not found: {EXTRACT_SQL}", file=sys.stderr)
        return 1
    if not OUTPUT_FILE.exists():
        print(f"FATAL: output file not found: {OUTPUT_FILE}", file=sys.stderr)
        return 1

    print(f"Seeding SQLite at {SQLITE_PATH} ...")
    seed_sqlite(SQLITE_PATH)

    print(f"Running extract from {EXTRACT_SQL.name} ...")
    db_df = run_extract(SQLITE_PATH, EXTRACT_SQL)
    print(f"  -> {len(db_df)} rows extracted")

    print(f"Loading output file {OUTPUT_FILE.name} ...")
    file_df = load_output_file(OUTPUT_FILE)
    print(f"  -> {len(file_df)} rows read")

    print("Running reconciliation ...")
    result = reconcile(db_df, file_df)
    print(
        f"  -> status={result['workflow']['status']}, "
        f"diffs={result['compare']['rows_with_differences']}, "
        f"only_in_db={len(result['compare']['only_in_file1'])}, "
        f"only_in_file={len(result['compare']['only_in_file2'])}"
    )

    EXPECTED_REPORT.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"Expected report written to {EXPECTED_REPORT}")

    if result["workflow"]["status"] != "passed":
        print(
            "FATAL: sample produced non-zero violations -- the worked example is "
            "designed to be a clean reconciliation. Fix the seed rows or output.txt.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
