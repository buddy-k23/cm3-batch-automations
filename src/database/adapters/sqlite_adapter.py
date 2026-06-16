"""SQLite database adapter using the built-in :mod:`sqlite3` module.

SQLite requires no external packages and is therefore the ideal adapter for
local development, unit testing, and lightweight CI pipelines that do not have
access to an Oracle or PostgreSQL server.

Connection parameters come from the constructor argument ``db_path``, which
defaults to the ``DB_PATH`` environment variable or ``:memory:`` (in-memory
database) when neither is provided.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Optional

import pandas as pd

from src.database.adapters.base import CanonicalType, ColumnMeta, DatabaseAdapter

# Default path: in-memory database — ephemeral, no filesystem required.
_DEFAULT_DB_PATH = ":memory:"


def _normalize_sqlite_type(declared_type: Optional[str]) -> CanonicalType:
    """Map a SQLite declared type to a portable :class:`CanonicalType`.

    Pure function (no DB access) per ADR 0022 §3.  SQLite is dynamically
    typed, so this follows SQLite's own *type-affinity* rules over the
    column's declared type, with two ADR overlays applied first:

    - Date/time hints (``DATE`` -> :attr:`CanonicalType.DATE`;
      ``DATETIME``/``TIMESTAMP`` -> :attr:`CanonicalType.TIMESTAMP`).
    - ``BOOL`` declared types -> :attr:`CanonicalType.INTEGER` (SQLite stores
      booleans as ``0``/``1`` integers — boolean-compatible via the advisory
      rule, not a native boolean).

    Affinity (SQLite's order): a declared type containing ``INT`` -> INTEGER;
    containing ``CHAR``/``CLOB``/``TEXT`` -> STRING; containing ``BLOB`` ->
    BINARY; an empty/absent declared type -> :attr:`CanonicalType.UNKNOWN`
    (compatible-with-everything plus an informational note downstream);
    containing ``REAL``/``FLOA``/``DOUB`` -> FLOAT; anything else (NUMERIC
    affinity) -> DECIMAL.

    Args:
        declared_type: The ``type`` column from ``PRAGMA table_info`` (may be
            an empty string or ``None`` for a typeless column).

    Returns:
        The matching :class:`CanonicalType`.
    """
    t = (declared_type or "").strip().upper()

    if t == "":
        return CanonicalType.UNKNOWN

    # ADR overlays first (these would otherwise fall through to affinity).
    if "BOOL" in t:
        return CanonicalType.INTEGER
    if "DATETIME" in t or "TIMESTAMP" in t:
        return CanonicalType.TIMESTAMP
    if "DATE" in t:
        return CanonicalType.DATE

    # SQLite affinity rules, in SQLite's own precedence order.
    if "INT" in t:
        return CanonicalType.INTEGER
    if "CHAR" in t or "CLOB" in t or "TEXT" in t:
        return CanonicalType.STRING
    if "BLOB" in t:
        return CanonicalType.BINARY
    if "REAL" in t or "FLOA" in t or "DOUB" in t:
        return CanonicalType.FLOAT
    return CanonicalType.DECIMAL


class SQLiteAdapter(DatabaseAdapter):
    """Database adapter for SQLite using the built-in ``sqlite3`` module.

    The in-memory default (``":memory:"``) makes this adapter safe for use in
    unit tests without any setup or teardown of external databases.

    Example::

        with SQLiteAdapter() as adapter:
            adapter._connection.execute("CREATE TABLE t (x INTEGER)")
            adapter._connection.execute("INSERT INTO t VALUES (1)")
            df = adapter.execute_query("SELECT x FROM t")
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        """Initialise the SQLite adapter.

        Args:
            db_path: Path to the SQLite database file or ``":memory:"`` for
                an in-memory database.  Falls back to the ``DB_PATH`` env var,
                then to ``":memory:"``.
        """
        self.db_path: str = (
            db_path
            or os.getenv("DB_PATH", _DEFAULT_DB_PATH)
        )
        self._connection: Optional[sqlite3.Connection] = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Open the SQLite connection.

        Uses ``check_same_thread=False`` so the connection can be shared
        across helper threads (useful inside FastAPI background tasks).

        Raises:
            ConnectionError: If :func:`sqlite3.connect` raises.
        """
        try:
            self._connection = sqlite3.connect(
                self.db_path, check_same_thread=False
            )
            # Return column names as strings (not bytes) when accessing rows.
            self._connection.row_factory = sqlite3.Row
        except sqlite3.Error as exc:
            raise ConnectionError(
                f"Failed to open SQLite database at {self.db_path!r}: {exc}"
            ) from exc

    def disconnect(self) -> None:
        """Close the SQLite connection.

        No-op when no connection is open.
        """
        if self._connection is None:
            return
        try:
            self._connection.close()
        finally:
            self._connection = None

    # ------------------------------------------------------------------
    # Query execution
    # ------------------------------------------------------------------

    def execute_query(self, sql: str, params: Optional[dict] = None) -> pd.DataFrame:
        """Execute a SELECT statement and return results as a DataFrame.

        Bind parameters follow the ``sqlite3`` named placeholder syntax
        (e.g. ``":name"`` in the SQL string with ``{"name": value}`` as
        *params*).

        Args:
            sql: SQL query string.
            params: Optional named bind parameters dict.

        Returns:
            DataFrame with query results.  Column names are taken from the
            cursor description.

        Raises:
            RuntimeError: If the query fails.
        """
        try:
            cursor = self._connection.execute(
                sql, params or {}
            )
            rows = cursor.fetchall()
            col_names = [desc[0] for desc in cursor.description] if cursor.description else []
            return pd.DataFrame([dict(zip(col_names, row)) for row in rows], columns=col_names)
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite query failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Schema inspection
    # ------------------------------------------------------------------

    def get_table_columns(
        self, table: str, schema: Optional[str] = None
    ) -> list:
        """Return column names for *table* via ``PRAGMA table_info()``.

        The ``schema`` argument is accepted for API compatibility but is
        ignored — SQLite connections are single-database.

        Args:
            table: Name of the table to inspect.
            schema: Ignored for SQLite.

        Returns:
            List of column name strings in definition order.  Empty list if
            the table does not exist.
        """
        try:
            cursor = self._connection.execute(f"PRAGMA table_info({table})")
            rows = cursor.fetchall()
            # PRAGMA table_info returns: cid, name, type, notnull, dflt_value, pk
            return [row[1] for row in rows]
        except sqlite3.Error:
            return []

    def table_exists(self, table: str, schema: Optional[str] = None) -> bool:
        """Check if *table* exists in ``sqlite_master``.

        Args:
            table: Table name to check (case-insensitive).
            schema: Ignored for SQLite.

        Returns:
            True if the table exists.
        """
        try:
            cursor = self._connection.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='table' AND LOWER(name)=LOWER(?)",
                (table,),
            )
            count = cursor.fetchone()[0]
            return count > 0
        except sqlite3.Error:
            return False

    def get_column_metadata(
        self, table: str, schema: Optional[str] = None
    ) -> dict[str, ColumnMeta]:
        """Return rich column metadata via ``PRAGMA table_info()``.

        ``PRAGMA table_info`` yields ``(cid, name, type, notnull, dflt_value,
        pk)``.  The declared ``type`` is normalised to a portable
        :class:`CanonicalType` by :func:`_normalize_sqlite_type`, and
        ``notnull`` (``1``/``0``) is normalised to a real nullable bool.
        SQLite reports no character length or numeric precision/scale, so those
        fields are always ``None`` (and the corresponding reconciliation
        sub-checks are skipped, per ADR 0022 §3).

        The ``schema`` argument is accepted for API compatibility but ignored —
        SQLite connections are single-database.

        Args:
            table: Name of the table to inspect.
            schema: Ignored for SQLite.

        Returns:
            Mapping of column name to :class:`ColumnMeta` in definition order.
            Empty dict if the table does not exist.
        """
        try:
            cursor = self._connection.execute(f"PRAGMA table_info({table})")
            rows = cursor.fetchall()
        except sqlite3.Error:
            return {}

        result: dict[str, ColumnMeta] = {}
        for row in rows:
            # row: cid, name, type, notnull, dflt_value, pk
            name = row[1]
            raw_type = row[2] if row[2] is not None else ""
            notnull = int(row[3])
            result[name] = ColumnMeta(
                name=name,
                canonical_type=_normalize_sqlite_type(raw_type),
                raw_type=raw_type,
                nullable=notnull == 0,
                length=None,
                precision=None,
                scale=None,
            )
        return result

    # ------------------------------------------------------------------
    # Data export
    # ------------------------------------------------------------------

    def extract_to_file(
        self, query: str, output_path: str, delimiter: str = "|"
    ) -> int:
        """Execute *query* and write results to a delimited text file.

        Args:
            query: SELECT statement to execute.
            output_path: Path of the output file to write (created or
                overwritten).
            delimiter: Column separator.  Defaults to ``"|"``.

        Returns:
            Total number of data rows written (excluding the header line).

        Raises:
            RuntimeError: If the query fails.
        """
        try:
            cursor = self._connection.execute(query)
            col_names = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite extraction failed: {exc}") from exc

        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write(delimiter.join(col_names) + "\n")
            for row in rows:
                fh.write(
                    delimiter.join(
                        "" if val is None else str(val) for val in row
                    )
                    + "\n"
                )

        return len(rows)
