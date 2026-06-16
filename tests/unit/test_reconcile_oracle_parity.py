"""Oracle verdict-parity tests for the adapter-routed reconciler.

ADR 0022 S12-3 risk mitigation: rewriting ``reconciliation.py`` must NOT
regress Oracle reconcile behaviour.  These tests drive the real
:class:`~src.database.adapters.oracle_adapter.OracleAdapter` through a
monkeypatched ``pd.read_sql`` returning canned ``ALL_TAB_COLUMNS`` catalog
rows, then assert the field-level verdict (which fields flagged, the overall
``valid`` verdict) matches the pre-migration Oracle semantics.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.config.mapping_parser import MappingParser
from src.database.adapters import oracle_adapter as oa
from src.database.adapters.oracle_adapter import OracleAdapter
from src.database.reconciliation import SchemaReconciler


# Canned ALL_TAB_COLUMNS rows for a CUSTOMER table.
_CATALOG = pd.DataFrame(
    [
        {
            "COLUMN_NAME": "NAME",
            "DATA_TYPE": "VARCHAR2",
            "DATA_LENGTH": 20,
            "DATA_PRECISION": None,
            "DATA_SCALE": None,
            "NULLABLE": "N",
        },
        {
            "COLUMN_NAME": "AGE",
            "DATA_TYPE": "NUMBER",
            "DATA_LENGTH": 22,
            "DATA_PRECISION": 5,
            "DATA_SCALE": 0,
            "NULLABLE": "Y",
        },
        {
            "COLUMN_NAME": "ACTIVE_FLAG",
            "DATA_TYPE": "NUMBER",
            "DATA_LENGTH": 22,
            "DATA_PRECISION": 1,
            "DATA_SCALE": 0,
            "NULLABLE": "Y",
        },
    ]
)


@pytest.fixture()
def oracle_reconciler(monkeypatch):
    """An OracleAdapter whose catalog reads return canned rows (no DB)."""

    def _fake_read_sql(sql, _conn, params=None):
        s = sql.upper()
        if "ALL_TABLES" in s:
            return pd.DataFrame([{"COUNT_": 1}])
        if "ALL_TAB_COLUMNS" in s:
            cols = ["COLUMN_NAME", "DATA_TYPE", "DATA_LENGTH", "DATA_PRECISION",
                    "DATA_SCALE", "NULLABLE"]
            if "ORDER BY COLUMN_ID" in s and "COUNT" not in s:
                # get_table_columns selects only COLUMN_NAME; metadata selects all.
                if "DATA_TYPE" in s:
                    return _CATALOG.copy()
                return _CATALOG[["COLUMN_NAME"]].copy()
            return _CATALOG.copy()
        return pd.DataFrame()

    monkeypatch.setattr(oa.pd, "read_sql", _fake_read_sql)
    adapter = OracleAdapter(username="u", password="p", dsn="d")
    adapter._connection = object()  # sentinel; read_sql is patched
    return SchemaReconciler(adapter)


def _mapping(target_col, data_type, required=True):
    return MappingParser().parse(
        {
            "mapping_name": "oracle_parity",
            "version": "1.0.0",
            "description": "parity",
            "source": {"type": "file", "format": "pipe_delimited"},
            "target": {"type": "database", "table_name": "CUSTOMER"},
            "mappings": [
                {
                    "source_column": target_col.lower(),
                    "target_column": target_col,
                    "data_type": data_type,
                    "required": required,
                    "transformations": [],
                    "validation_rules": [],
                }
            ],
            "key_columns": [target_col.lower()],
        }
    )


def test_oracle_string_to_varchar2_is_clean(oracle_reconciler):
    """string -> VARCHAR2 reconciles clean (same as legacy Oracle matrix)."""
    result = oracle_reconciler.reconcile_mapping(_mapping("NAME", "string"))
    assert result["valid"] is True
    assert not any("Type mismatch" in w for w in result["warnings"])


def test_oracle_integer_to_number_is_clean(oracle_reconciler):
    """integer -> NUMBER(5,0) reconciles clean (NUMBER scale 0 -> INTEGER)."""
    result = oracle_reconciler.reconcile_mapping(
        _mapping("AGE", "integer", required=False)
    )
    assert result["valid"] is True
    assert not any("Type mismatch" in w for w in result["warnings"])


def test_oracle_string_to_number_flags_mismatch(oracle_reconciler):
    """string -> NUMBER is a real conflict, flagged exactly as before."""
    result = oracle_reconciler.reconcile_mapping(
        _mapping("AGE", "string", required=False)
    )
    assert any("Type mismatch for AGE" in w for w in result["warnings"])


def test_oracle_boolean_to_number1_is_advisory_not_error(oracle_reconciler):
    """boolean -> NUMBER(1) is the dialect-neutral advisory (was Oracle-only)."""
    result = oracle_reconciler.reconcile_mapping(
        _mapping("ACTIVE_FLAG", "boolean", required=False)
    )
    assert result["valid"] is True
    assert any(
        "ACTIVE_FLAG" in w and "boolean" in w.lower()
        for w in result["warnings"]
    )


def test_oracle_required_but_nullable_warns(oracle_reconciler):
    """A required mapping column that is nullable in Oracle still warns."""
    result = oracle_reconciler.reconcile_mapping(
        _mapping("AGE", "integer", required=True)
    )
    assert any(
        "required in mapping but nullable in database" in w
        for w in result["warnings"]
    )
