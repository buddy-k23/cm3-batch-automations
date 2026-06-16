"""Unit tests for ADR 0022 S12-1a: get_column_metadata + CanonicalType.

Tests cover:
- The CanonicalType enum and ColumnMeta dataclass shape (base.py).
- Per-dialect raw-type -> CanonicalType normalization matrices, tested
  against the pure helper functions each adapter exposes (so Oracle and
  PostgreSQL can be tested without a live server).
- nullable/length/precision/scale extraction from representative catalog rows.
- SQLite get_column_metadata end-to-end against a real in-memory database.

Per ADR 0022 (S12-2, #403).  Additive only — no consumer is exercised here.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# CanonicalType / ColumnMeta shape (base.py)
# ---------------------------------------------------------------------------


class TestCanonicalTypeModel:
    """The portable type model defined in base.py."""

    def test_canonical_type_members(self) -> None:
        """CanonicalType exposes exactly the ADR 0022 member set."""
        from src.database.adapters.base import CanonicalType

        names = {m.name for m in CanonicalType}
        assert names == {
            "STRING",
            "INTEGER",
            "DECIMAL",
            "FLOAT",
            "BOOLEAN",
            "DATE",
            "TIMESTAMP",
            "BINARY",
            "UNKNOWN",
        }

    def test_column_meta_fields_and_frozen(self) -> None:
        """ColumnMeta carries the ADR fields and is immutable (frozen)."""
        from src.database.adapters.base import CanonicalType, ColumnMeta

        meta = ColumnMeta(
            name="amount",
            canonical_type=CanonicalType.DECIMAL,
            raw_type="NUMBER",
            nullable=False,
            length=None,
            precision=10,
            scale=2,
        )
        assert meta.name == "amount"
        assert meta.canonical_type is CanonicalType.DECIMAL
        assert meta.raw_type == "NUMBER"
        assert meta.nullable is False
        assert meta.length is None
        assert meta.precision == 10
        assert meta.scale == 2

        with pytest.raises(Exception):
            meta.name = "other"  # type: ignore[misc]

    def test_get_column_metadata_is_abstract(self) -> None:
        """A subclass missing get_column_metadata cannot be instantiated."""
        from src.database.adapters.base import DatabaseAdapter

        class Incomplete(DatabaseAdapter):
            def connect(self) -> None:  # pragma: no cover - never called
                pass

            def disconnect(self) -> None:  # pragma: no cover
                pass

            def execute_query(self, sql: str, params: dict = None) -> pd.DataFrame:
                return pd.DataFrame()

            def get_table_columns(self, table: str, schema: str = None) -> list:
                return []

            def table_exists(self, table: str, schema: str = None) -> bool:
                return False

            def extract_to_file(
                self, query: str, output_path: str, delimiter: str = "|"
            ) -> int:
                return 0

            # NOTE: get_column_metadata intentionally NOT implemented.

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# Oracle normalization matrix (pure function — no live DB)
# ---------------------------------------------------------------------------


class TestOracleNormalization:
    """Oracle raw catalog type -> CanonicalType per ADR 0022 table."""

    @pytest.mark.parametrize(
        "data_type, precision, scale, expected",
        [
            ("VARCHAR2", None, None, "STRING"),
            ("NVARCHAR2", None, None, "STRING"),
            ("CHAR", None, None, "STRING"),
            ("NCHAR", None, None, "STRING"),
            ("CLOB", None, None, "STRING"),
            ("NCLOB", None, None, "STRING"),
            ("NUMBER", 10, 0, "INTEGER"),
            ("NUMBER", 5, 0, "INTEGER"),
            ("INTEGER", None, None, "INTEGER"),
            ("NUMBER", 10, 2, "DECIMAL"),
            ("NUMBER", None, None, "DECIMAL"),  # unspecified precision/scale
            ("FLOAT", None, None, "FLOAT"),
            ("BINARY_FLOAT", None, None, "FLOAT"),
            ("BINARY_DOUBLE", None, None, "FLOAT"),
            ("DATE", None, None, "DATE"),
            ("TIMESTAMP", None, None, "TIMESTAMP"),
            ("TIMESTAMP(6)", None, None, "TIMESTAMP"),
            ("TIMESTAMP WITH TIME ZONE", None, None, "TIMESTAMP"),
            ("TIMESTAMP WITH LOCAL TIME ZONE", None, None, "TIMESTAMP"),
            ("BLOB", None, None, "BINARY"),
            ("RAW", None, None, "BINARY"),
            ("LONG RAW", None, None, "BINARY"),
            ("SOMETHING_WEIRD", None, None, "UNKNOWN"),
        ],
    )
    def test_normalize(
        self, data_type: str, precision, scale, expected: str
    ) -> None:
        from src.database.adapters.base import CanonicalType
        from src.database.adapters.oracle_adapter import _normalize_oracle_type

        result = _normalize_oracle_type(data_type, precision, scale)
        assert result is getattr(CanonicalType, expected)

    def test_number_1_is_integer_boolean_compatible(self) -> None:
        """NUMBER(1) -> INTEGER (boolean-compatible per ADR advisory rule)."""
        from src.database.adapters.base import CanonicalType
        from src.database.adapters.oracle_adapter import _normalize_oracle_type

        assert _normalize_oracle_type("NUMBER", 1, 0) is CanonicalType.INTEGER

    def test_char_1_is_string_boolean_compatible(self) -> None:
        """CHAR(1) -> STRING (boolean-compatible per ADR advisory rule)."""
        from src.database.adapters.base import CanonicalType
        from src.database.adapters.oracle_adapter import _normalize_oracle_type

        assert _normalize_oracle_type("CHAR", None, None) is CanonicalType.STRING

    def test_get_column_metadata_from_catalog_rows(self) -> None:
        """get_column_metadata builds ColumnMeta from ALL_TAB_COLUMNS rows."""
        from src.database.adapters.base import CanonicalType
        from src.database.adapters.oracle_adapter import OracleAdapter

        catalog = pd.DataFrame(
            {
                "COLUMN_NAME": ["ID", "NAME", "AMOUNT"],
                "DATA_TYPE": ["NUMBER", "VARCHAR2", "NUMBER"],
                "DATA_LENGTH": [22, 50, 22],
                "DATA_PRECISION": [10, None, 12],
                "DATA_SCALE": [0, None, 2],
                "NULLABLE": ["N", "Y", "Y"],
            }
        )

        adapter = OracleAdapter(username="U", password="P", dsn="H:1521/DB")
        adapter._connection = object()  # placeholder, read_sql is patched

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "src.database.adapters.oracle_adapter.pd.read_sql",
                lambda *a, **k: catalog,
            )
            meta = adapter.get_column_metadata("MY_TABLE", schema="APP_INT")

        assert set(meta) == {"ID", "NAME", "AMOUNT"}
        assert meta["ID"].canonical_type is CanonicalType.INTEGER
        assert meta["ID"].nullable is False
        assert meta["ID"].precision == 10
        assert meta["ID"].scale == 0
        assert meta["NAME"].canonical_type is CanonicalType.STRING
        assert meta["NAME"].nullable is True
        assert meta["NAME"].length == 50
        assert meta["AMOUNT"].canonical_type is CanonicalType.DECIMAL
        assert meta["AMOUNT"].precision == 12
        assert meta["AMOUNT"].scale == 2


# ---------------------------------------------------------------------------
# PostgreSQL normalization matrix (pure function — no live DB)
# ---------------------------------------------------------------------------


class TestPostgresNormalization:
    """PostgreSQL raw catalog type -> CanonicalType per ADR 0022 table."""

    @pytest.mark.parametrize(
        "data_type, expected",
        [
            ("varchar", "STRING"),
            ("character varying", "STRING"),
            ("char", "STRING"),
            ("character", "STRING"),
            ("bpchar", "STRING"),
            ("text", "STRING"),
            ("integer", "INTEGER"),
            ("int", "INTEGER"),
            ("int2", "INTEGER"),
            ("int4", "INTEGER"),
            ("int8", "INTEGER"),
            ("bigint", "INTEGER"),
            ("smallint", "INTEGER"),
            ("numeric", "DECIMAL"),
            ("decimal", "DECIMAL"),
            ("real", "FLOAT"),
            ("double precision", "FLOAT"),
            ("float8", "FLOAT"),
            ("boolean", "BOOLEAN"),
            ("bool", "BOOLEAN"),
            ("date", "DATE"),
            ("timestamp", "TIMESTAMP"),
            ("timestamp without time zone", "TIMESTAMP"),
            ("timestamp with time zone", "TIMESTAMP"),
            ("timestamptz", "TIMESTAMP"),
            ("bytea", "BINARY"),
            ("json", "UNKNOWN"),
        ],
    )
    def test_normalize(self, data_type: str, expected: str) -> None:
        from src.database.adapters.base import CanonicalType
        from src.database.adapters.postgresql_adapter import _normalize_postgres_type

        assert _normalize_postgres_type(data_type) is getattr(
            CanonicalType, expected
        )

    def test_boolean_is_exact_boolean(self) -> None:
        """PG boolean normalizes to BOOLEAN exactly (the native-boolean case)."""
        from src.database.adapters.base import CanonicalType
        from src.database.adapters.postgresql_adapter import _normalize_postgres_type

        assert _normalize_postgres_type("boolean") is CanonicalType.BOOLEAN

    def test_get_column_metadata_from_catalog_rows(self) -> None:
        """get_column_metadata builds ColumnMeta from information_schema rows."""
        from src.database.adapters.base import CanonicalType
        from src.database.adapters.postgresql_adapter import PostgreSQLAdapter

        catalog = pd.DataFrame(
            {
                "column_name": ["id", "label", "active", "price"],
                "data_type": ["integer", "varchar", "boolean", "numeric"],
                "character_maximum_length": [None, 100, None, None],
                "numeric_precision": [32, None, None, 8],
                "numeric_scale": [0, None, None, 2],
                "is_nullable": ["NO", "YES", "YES", "YES"],
            }
        )

        adapter = PostgreSQLAdapter(
            host="h", port=5432, database="d", username="u", password="p"
        )
        adapter._connection = object()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "src.database.adapters.postgresql_adapter.pd.read_sql",
                lambda *a, **k: catalog,
            )
            meta = adapter.get_column_metadata("my_table", schema="public")

        assert set(meta) == {"id", "label", "active", "price"}
        assert meta["id"].canonical_type is CanonicalType.INTEGER
        assert meta["id"].nullable is False
        assert meta["label"].canonical_type is CanonicalType.STRING
        assert meta["label"].nullable is True
        assert meta["label"].length == 100
        assert meta["active"].canonical_type is CanonicalType.BOOLEAN
        assert meta["price"].canonical_type is CanonicalType.DECIMAL
        assert meta["price"].precision == 8
        assert meta["price"].scale == 2


# ---------------------------------------------------------------------------
# SQLite normalization (affinity) + end-to-end
# ---------------------------------------------------------------------------


class TestSQLiteNormalization:
    """SQLite declared-type affinity -> CanonicalType per ADR 0022."""

    @pytest.mark.parametrize(
        "declared, expected",
        [
            ("TEXT", "STRING"),
            ("VARCHAR(50)", "STRING"),
            ("CHARACTER(20)", "STRING"),
            ("CLOB", "STRING"),
            ("NCHAR", "STRING"),
            ("INTEGER", "INTEGER"),
            ("INT", "INTEGER"),
            ("BIGINT", "INTEGER"),
            ("REAL", "FLOAT"),
            ("FLOAT", "FLOAT"),
            ("DOUBLE", "FLOAT"),
            ("DOUBLE PRECISION", "FLOAT"),
            ("NUMERIC", "DECIMAL"),
            ("DECIMAL(10,2)", "DECIMAL"),
            ("BLOB", "BINARY"),
            ("", "UNKNOWN"),
            (None, "UNKNOWN"),
            ("DATE", "DATE"),
            ("DATETIME", "TIMESTAMP"),
            ("TIMESTAMP", "TIMESTAMP"),
            ("BOOLEAN", "INTEGER"),  # affinity: contains no special token -> NUMERIC/INT
        ],
    )
    def test_normalize(self, declared, expected: str) -> None:
        from src.database.adapters.base import CanonicalType
        from src.database.adapters.sqlite_adapter import _normalize_sqlite_type

        assert _normalize_sqlite_type(declared) is getattr(CanonicalType, expected)

    def _adapter_with_table(self) -> Any:
        from src.database.adapters.sqlite_adapter import SQLiteAdapter

        adapter = SQLiteAdapter(db_path=":memory:")
        adapter.connect()
        adapter._connection.execute(
            "CREATE TABLE accounts ("
            "  id INTEGER NOT NULL,"
            "  name TEXT NOT NULL,"
            "  balance REAL,"
            "  is_active INTEGER,"
            "  notes,"  # typeless column -> UNKNOWN
            "  opened DATE"
            ")"
        )
        return adapter

    def test_get_column_metadata_end_to_end(self) -> None:
        """get_column_metadata returns correct ColumnMeta from a live table."""
        from src.database.adapters.base import CanonicalType

        adapter = self._adapter_with_table()
        try:
            meta = adapter.get_column_metadata("accounts")
        finally:
            adapter.disconnect()

        assert set(meta) == {"id", "name", "balance", "is_active", "notes", "opened"}

        assert meta["id"].canonical_type is CanonicalType.INTEGER
        assert meta["id"].nullable is False
        assert meta["id"].raw_type.upper().startswith("INTEGER")

        assert meta["name"].canonical_type is CanonicalType.STRING
        assert meta["name"].nullable is False

        assert meta["balance"].canonical_type is CanonicalType.FLOAT
        assert meta["balance"].nullable is True

        assert meta["is_active"].canonical_type is CanonicalType.INTEGER

        # Typeless column -> UNKNOWN (compatible+note downstream)
        assert meta["notes"].canonical_type is CanonicalType.UNKNOWN

        assert meta["opened"].canonical_type is CanonicalType.DATE

        # PRAGMA exposes no length/precision/scale -> all None
        for m in meta.values():
            assert m.length is None
            assert m.precision is None
            assert m.scale is None

    def test_get_column_metadata_empty_for_missing_table(self) -> None:
        """get_column_metadata returns an empty dict for a missing table."""
        from src.database.adapters.sqlite_adapter import SQLiteAdapter

        adapter = SQLiteAdapter(db_path=":memory:")
        adapter.connect()
        try:
            meta = adapter.get_column_metadata("ghost")
        finally:
            adapter.disconnect()

        assert meta == {}
