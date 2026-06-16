"""End-to-end portability proof: ``reconcile`` against a real SQLite database.

This is the headline acceptance criterion for ADR 0022 S12-3 (#404): the
**same mapping shape** that reconciles against Oracle now reconciles against a
live SQLite database via :func:`~src.database.adapters.factory.get_database_adapter`
— with no Oracle catalog SQL in the reconciliation path.

The fixture database lives under a tmp/gitignored path (pytest ``tmp_path``),
never a tracked file.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.config.mapping_parser import MappingParser
from src.database.adapters.sqlite_adapter import SQLiteAdapter
from src.database.reconciliation import SchemaReconciler


def _seed_target_db(db_path: str) -> None:
    """Create a target table with mixed types incl. boolean-as-INTEGER.

    Columns:
        CUSTOMER_ID  TEXT     NOT NULL  -> STRING   (maps clean to string)
        AGE          INTEGER  NULL      -> INTEGER  (maps clean to integer)
        BALANCE      NUMERIC  NULL      -> DECIMAL  (maps clean to decimal)
        IS_ACTIVE    BOOLEAN  NULL      -> INTEGER  (boolean advisory)
        CREATED_AT   TIMESTAMP NULL     -> TIMESTAMP (maps clean to date)
        NOTES                  NULL     -> UNKNOWN  (typeless -> advisory)
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE CUSTOMER (
                CUSTOMER_ID TEXT NOT NULL,
                AGE INTEGER,
                BALANCE NUMERIC,
                IS_ACTIVE BOOLEAN,
                CREATED_AT TIMESTAMP,
                NOTES
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _build_mapping(extra_mappings=None):
    """Build a database-target mapping the SAME shape used for Oracle."""
    mappings = [
        {
            "source_column": "customer_id",
            "target_column": "CUSTOMER_ID",
            "data_type": "string",
            "required": True,
            "transformations": [],
            "validation_rules": [],
        },
        {
            "source_column": "age",
            "target_column": "AGE",
            "data_type": "integer",
            "required": False,
            "transformations": [],
            "validation_rules": [],
        },
        {
            "source_column": "balance",
            "target_column": "BALANCE",
            "data_type": "decimal",
            "required": False,
            "transformations": [],
            "validation_rules": [],
        },
        {
            "source_column": "is_active",
            "target_column": "IS_ACTIVE",
            "data_type": "boolean",
            "required": False,
            "transformations": [],
            "validation_rules": [],
        },
        {
            "source_column": "notes",
            "target_column": "NOTES",
            "data_type": "string",
            "required": False,
            "transformations": [],
            "validation_rules": [],
        },
    ]
    if extra_mappings:
        mappings.extend(extra_mappings)

    mapping_dict = {
        "mapping_name": "sqlite_recon_test",
        "version": "1.0.0",
        "description": "Portability proof mapping",
        "source": {"type": "file", "format": "pipe_delimited"},
        "target": {"type": "database", "table_name": "CUSTOMER"},
        "mappings": mappings,
        "key_columns": ["customer_id"],
    }
    return MappingParser().parse(mapping_dict)


@pytest.fixture()
def sqlite_adapter(tmp_path: Path):
    """A connected SQLite adapter over a seeded tmp database file."""
    db_path = str(tmp_path / "recon_fixture.db")
    _seed_target_db(db_path)
    adapter = SQLiteAdapter(db_path=db_path)
    adapter.connect()
    try:
        yield adapter
    finally:
        adapter.disconnect()


def test_reconcile_runs_clean_on_sqlite(sqlite_adapter):
    """The whole-clean mapping reconciles VALID with zero type-mismatch errors."""
    reconciler = SchemaReconciler(sqlite_adapter)
    result = reconciler.reconcile_mapping(_build_mapping())

    assert result["valid"] is True
    assert result["error_count"] == 0
    # No spurious type-mismatch warnings for the cleanly-mapped columns.
    assert not any(
        "Type mismatch" in w for w in result["warnings"]
    ), f"Unexpected type mismatch warnings: {result['warnings']}"


def test_reconcile_flags_real_type_conflict_on_sqlite(sqlite_adapter):
    """A genuine conflict (mapping string vs INTEGER column) is flagged."""
    reconciler = SchemaReconciler(sqlite_adapter)
    # AGE is INTEGER in the DB; declare it as a string in the mapping.
    mapping = _build_mapping()
    for m in mapping.mappings:
        if m.target_column == "AGE":
            m.data_type = "string"

    result = reconciler.reconcile_mapping(mapping)

    assert any(
        "Type mismatch for AGE" in w for w in result["warnings"]
    ), f"Expected AGE type mismatch; got {result['warnings']}"


def test_reconcile_boolean_emits_advisory_not_error_on_sqlite(sqlite_adapter):
    """boolean mapping over an INTEGER-stored flag is an advisory, not an error."""
    reconciler = SchemaReconciler(sqlite_adapter)
    result = reconciler.reconcile_mapping(_build_mapping())

    assert result["valid"] is True
    assert any(
        "IS_ACTIVE" in w and "boolean" in w.lower()
        for w in result["warnings"]
    ), f"Expected boolean advisory for IS_ACTIVE; got {result['warnings']}"


def test_reconcile_unknown_type_emits_note_not_error_on_sqlite(sqlite_adapter):
    """A typeless SQLite column (NOTES) resolves UNKNOWN -> compatible + note."""
    reconciler = SchemaReconciler(sqlite_adapter)
    result = reconciler.reconcile_mapping(_build_mapping())

    assert result["valid"] is True
    assert any(
        "NOTES" in w and "could not" in w.lower()
        for w in result["warnings"]
    ), f"Expected UNKNOWN advisory note for NOTES; got {result['warnings']}"


def test_reconcile_missing_required_column_is_error_on_sqlite(sqlite_adapter):
    """A required mapping column absent from the DB table is a hard error."""
    reconciler = SchemaReconciler(sqlite_adapter)
    extra = [
        {
            "source_column": "ssn",
            "target_column": "SSN",
            "data_type": "string",
            "required": True,
            "transformations": [],
            "validation_rules": [],
        }
    ]
    result = reconciler.reconcile_mapping(_build_mapping(extra_mappings=extra))

    assert result["valid"] is False
    assert any(
        "Required target column not found: SSN" in e for e in result["errors"]
    )


def test_reconcile_no_oracle_catalog_sql_in_path():
    """Static guard: reconciliation's portable path issues no Oracle catalog SQL."""
    src = Path("src/database/reconciliation.py").read_text(encoding="utf-8")
    for token in ("all_tables", "user_tables", "all_tab_columns", "user_tab_columns"):
        assert token not in src.lower(), (
            f"Oracle catalog token {token!r} still present in reconciliation.py"
        )
