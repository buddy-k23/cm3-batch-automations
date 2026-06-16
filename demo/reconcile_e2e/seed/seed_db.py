"""Seed a SQLite DB + mapping for the #407 cross-dialect reconcile demo.

Creates a SQLite database whose ``CUSTOMER`` table is deliberately shaped so a
single reconcile produces an *interesting* verdict:

* ``CUSTOMER_ID`` TEXT     — mapping declares ``string``  -> clean match
* ``FIRST_NAME``  TEXT     — mapping declares ``string``  -> clean match
* ``EMAIL``       TEXT     — mapping declares ``string``  -> clean match
* ``AGE``         VARCHAR  — mapping declares ``integer`` -> TYPE MISMATCH
* ``IS_ACTIVE``   INTEGER  — mapping declares ``boolean`` -> ADVISORY
  (SQLite has no native boolean; the integer carrier is compatible-but-noted)
* ``NOTES``       (typeless / UNKNOWN affinity) — mapping declares ``string``
  -> dialect-neutral advisory (typeless SQLite column)

The matching mapping JSON is written into ``config/mappings/`` so both the
REST/UI mapping dropdown (which lists ``config/mappings/*``) and the MCP /
service bare-id resolution find it by its stem ``reconcile_demo``.

Run directly:

    python demo/reconcile_e2e/seed/seed_db.py

Honours env overrides ``DEMO_DB_PATH`` and ``DEMO_MAPPING_PATH``.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

# Repo root = .../demo/reconcile_e2e/seed/seed_db.py -> parents[3]
REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_DIR = REPO_ROOT / "demo" / "reconcile_e2e"

# Runtime DB lives under the demo dir and is gitignored.
DEFAULT_DB_PATH = DEMO_DIR / "seed" / "reconcile_demo.db"
# Mapping must live under config/mappings/ so the UI dropdown + bare-id resolve.
DEFAULT_MAPPING_PATH = REPO_ROOT / "config" / "mappings" / "reconcile_demo.json"

TABLE_NAME = "CUSTOMER"


def db_path() -> Path:
    return Path(os.getenv("DEMO_DB_PATH", str(DEFAULT_DB_PATH)))


def mapping_path() -> Path:
    return Path(os.getenv("DEMO_MAPPING_PATH", str(DEFAULT_MAPPING_PATH)))


def create_fixture_db(path: Path) -> None:
    """(Re)create the demo SQLite DB with the deliberately-mismatched table."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE CUSTOMER ("
            "CUSTOMER_ID TEXT NOT NULL, "      # string  -> clean
            "FIRST_NAME  TEXT, "               # string  -> clean
            "EMAIL       TEXT, "               # string  -> clean
            "AGE         VARCHAR(10), "        # integer -> MISMATCH
            "IS_ACTIVE   INTEGER, "            # boolean -> ADVISORY (no native bool)
            "NOTES "                           # typeless/UNKNOWN affinity -> ADVISORY
            ")"
        )
        conn.executemany(
            "INSERT INTO CUSTOMER "
            "(CUSTOMER_ID, FIRST_NAME, EMAIL, AGE, IS_ACTIVE, NOTES) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("C001", "Alice", "alice@example.com", "34", 1, "vip"),
                ("C002", "Bob", "bob@example.com", "29", 0, None),
                ("C003", "Carol", "carol@example.com", "41", 1, "watch"),
            ],
        )
        conn.commit()
    finally:
        conn.close()


def write_mapping(path: Path) -> None:
    """Write the mapping JSON whose target columns match CUSTOMER's columns."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # The mappings list (used by both the reconcile service and the
    # /api/v1/mappings/ list endpoint that populates the UI dropdown). The
    # reconcile parser reads ``mappings``; the list endpoint reads ``fields``
    # + ``source.format`` for display, so we provide BOTH (a ``fields`` mirror
    # keeps the demo mapping visible in the dropdown without affecting
    # reconciliation, which only consumes ``mappings``).
    field_specs = [
        ("customer_id", "CUSTOMER_ID", "string", True),
        ("first_name", "FIRST_NAME", "string", False),
        ("email", "EMAIL", "string", False),
        ("age", "AGE", "integer", False),          # VARCHAR -> MISMATCH
        ("is_active", "IS_ACTIVE", "boolean", False),  # INTEGER -> ADVISORY
        ("notes", "NOTES", "string", False),       # typeless -> ADVISORY
    ]
    doc = {
        "mapping_name": "reconcile_demo",
        "version": "1.0.0",
        "description": (
            "#407 cross-dialect reconcile demo mapping. Most fields match the "
            "SQLite CUSTOMER table cleanly; AGE forces a type mismatch and "
            "IS_ACTIVE / NOTES exercise dialect-neutral advisories."
        ),
        "source": {"type": "file", "format": "pipe_delimited"},
        "target": {"type": "database", "table_name": "CUSTOMER"},
        # ``fields`` mirror so /api/v1/mappings/ lists this mapping in the UI.
        "fields": [
            {"field_name": src, "data_type": dt, "required": req}
            for (src, _tgt, dt, req) in field_specs
        ],
        "mappings": [
            {
                "source_column": "customer_id",
                "target_column": "CUSTOMER_ID",
                "data_type": "string",
                "required": True,
                "transformations": [],
                "validation_rules": [],
            },
            {
                "source_column": "first_name",
                "target_column": "FIRST_NAME",
                "data_type": "string",
                "required": False,
                "transformations": [],
                "validation_rules": [],
            },
            {
                "source_column": "email",
                "target_column": "EMAIL",
                "data_type": "string",
                "required": False,
                "transformations": [],
                "validation_rules": [],
            },
            {
                # VARCHAR column declared integer -> genuine type mismatch.
                "source_column": "age",
                "target_column": "AGE",
                "data_type": "integer",
                "required": False,
                "transformations": [],
                "validation_rules": [],
            },
            {
                # INTEGER carrier declared boolean -> dialect-neutral advisory.
                "source_column": "is_active",
                "target_column": "IS_ACTIVE",
                "data_type": "boolean",
                "required": False,
                "transformations": [],
                "validation_rules": [],
            },
            {
                # Typeless/UNKNOWN SQLite column declared string -> advisory.
                "source_column": "notes",
                "target_column": "NOTES",
                "data_type": "string",
                "required": False,
                "transformations": [],
                "validation_rules": [],
            },
        ],
        "key_columns": ["customer_id"],
        "metadata": {"demo": "reconcile_e2e", "issue": "407"},
    }
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def main() -> None:
    dbp = db_path()
    mp = mapping_path()
    create_fixture_db(dbp)
    write_mapping(mp)
    print(f"[seed] SQLite DB written : {dbp}")
    print(f"[seed] mapping JSON      : {mp}")
    print(f"[seed] mapping id        : {mp.stem}")
    print(f"[seed] table             : {TABLE_NAME}")


if __name__ == "__main__":
    main()
