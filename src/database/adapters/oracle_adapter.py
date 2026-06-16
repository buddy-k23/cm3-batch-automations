"""Oracle database adapter using oracledb thin mode.

Reads connection parameters from the same ``ORACLE_*`` environment variables
that the legacy :class:`~src.database.connection.OracleConnection` class uses,
ensuring full backward compatibility with existing configuration.
"""

from __future__ import annotations

import os
from typing import Optional

import oracledb
import pandas as pd

from src.database.adapters.base import CanonicalType, ColumnMeta, DatabaseAdapter

# Default values kept in sync with src.config.db_config
_DEFAULT_USER = "APP_INT"
_DEFAULT_DSN = "localhost:1521/FREEPDB1"


def _as_int(value) -> Optional[int]:
    """Coerce a catalog cell to ``int`` or ``None``.

    Catalog columns such as ``DATA_PRECISION`` arrive from pandas as ``None``,
    ``NaN``, ``float``, or ``int`` depending on the driver.  This collapses all
    "no value" variants to ``None`` and otherwise returns a plain ``int``.

    Args:
        value: A scalar catalog cell.

    Returns:
        The integer value, or ``None`` when the cell is null / NaN.
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return int(value)


def _normalize_oracle_type(
    data_type: str,
    precision: Optional[int] = None,
    scale: Optional[int] = None,
) -> CanonicalType:
    """Map an Oracle raw catalog type to a portable :class:`CanonicalType`.

    Pure function (no DB access) so the full normalization matrix can be
    unit-tested directly per ADR 0022 §3.  Follows the ADR's per-dialect
    table, including the "no native boolean" rule: Oracle ``NUMBER(1)`` maps
    to ``INTEGER`` and ``CHAR(1)`` maps to ``STRING`` — both boolean-compatible
    via the dialect-neutral advisory rule, not a native boolean.

    Args:
        data_type: The ``DATA_TYPE`` string from ``ALL_TAB_COLUMNS`` (e.g.
            ``"VARCHAR2"``, ``"NUMBER"``, ``"TIMESTAMP(6)"``).
        precision: ``DATA_PRECISION`` for numeric types, or ``None``.
        scale: ``DATA_SCALE`` for numeric types, or ``None``.

    Returns:
        The matching :class:`CanonicalType`; :attr:`CanonicalType.UNKNOWN`
        for an unrecognised type string.
    """
    t = (data_type or "").strip().upper()

    if t in {"VARCHAR2", "NVARCHAR2", "VARCHAR", "CHAR", "NCHAR", "CLOB", "NCLOB"}:
        return CanonicalType.STRING
    if t == "INTEGER":
        return CanonicalType.INTEGER
    if t == "NUMBER":
        # NUMBER(p,0) -> INTEGER; NUMBER(p,s>0) or unspecified -> DECIMAL.
        if scale is not None and int(scale) == 0:
            return CanonicalType.INTEGER
        return CanonicalType.DECIMAL
    if t in {"FLOAT", "BINARY_FLOAT", "BINARY_DOUBLE"}:
        return CanonicalType.FLOAT
    if t == "DATE":
        return CanonicalType.DATE
    if t.startswith("TIMESTAMP"):
        return CanonicalType.TIMESTAMP
    if t in {"BLOB", "RAW", "LONG RAW"}:
        return CanonicalType.BINARY
    return CanonicalType.UNKNOWN


class OracleAdapter(DatabaseAdapter):
    """Database adapter for Oracle using ``oracledb`` in thin mode.

    Connection parameters are read from environment variables on construction
    so that the adapter can be instantiated without external dependencies:

    - ``ORACLE_USER`` — database username (default ``APP_INT``)
    - ``ORACLE_PASSWORD`` — database password (no default; required to connect)
    - ``ORACLE_DSN`` — Easy Connect string (default ``localhost:1521/FREEPDB1``)

    Example::

        with OracleAdapter() as adapter:
            df = adapter.execute_query("SELECT * FROM APP_INT.MY_TABLE")
    """

    def __init__(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        dsn: Optional[str] = None,
    ) -> None:
        """Initialise Oracle adapter from explicit values or environment variables.

        When a parameter is *None* the corresponding ``ORACLE_*`` env var is
        read.  This allows callers that already hold resolved credentials to
        pass them directly while supporting the common env-var-driven path.

        Args:
            username: Oracle username.  Falls back to ``ORACLE_USER`` or
                ``APP_INT``.
            password: Oracle password.  Falls back to ``ORACLE_PASSWORD`` or
                ``""`` (empty string — connection will fail fast).
            dsn: Oracle Easy Connect string.  Falls back to ``ORACLE_DSN`` or
                ``localhost:1521/FREEPDB1``.
        """
        self.username: str = username or os.getenv("ORACLE_USER", _DEFAULT_USER)
        self.password: str = password if password is not None else os.getenv(
            "ORACLE_PASSWORD", ""
        )
        self.dsn: str = dsn or os.getenv("ORACLE_DSN", _DEFAULT_DSN)
        self._connection: Optional[oracledb.Connection] = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Open an oracledb thin-mode connection.

        Raises:
            RuntimeError: If ``ORACLE_PASSWORD`` is empty (fail-fast).
            ConnectionError: If oracledb cannot reach the server.
        """
        if not self.password:
            raise RuntimeError(
                "ORACLE_PASSWORD is not set.  "
                "Set it in your .env file or export it as an environment "
                "variable before attempting a database connection."
            )
        try:
            self._connection = oracledb.connect(
                user=self.username,
                password=self.password,
                dsn=self.dsn,
            )
        except oracledb.Error as exc:
            raise ConnectionError(
                f"Failed to connect to Oracle (user={self.username!r}, "
                f"dsn={self.dsn!r}): {exc}"
            ) from exc

    def disconnect(self) -> None:
        """Close the Oracle connection and set the internal reference to None.

        Raises:
            ConnectionError: If closing the connection raises an oracledb error.
        """
        if self._connection is None:
            return
        try:
            self._connection.close()
        except oracledb.Error as exc:
            raise ConnectionError(
                f"Failed to close Oracle connection: {exc}"
            ) from exc
        finally:
            self._connection = None

    # ------------------------------------------------------------------
    # Query execution
    # ------------------------------------------------------------------

    def execute_query(self, sql: str, params: Optional[dict] = None) -> pd.DataFrame:
        """Execute a SELECT and return results as a DataFrame.

        Uses :func:`pandas.read_sql` which handles column naming automatically.

        Args:
            sql: The SQL query string.
            params: Optional named bind parameters.

        Returns:
            DataFrame with query results.

        Raises:
            RuntimeError: If the query fails.
        """
        try:
            return pd.read_sql(sql, self._connection, params=params)
        except Exception as exc:
            raise RuntimeError(f"Oracle query execution failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Schema inspection
    # ------------------------------------------------------------------

    def get_table_columns(
        self, table: str, schema: Optional[str] = None
    ) -> list:
        """Return column names for *table* from ``ALL_TAB_COLUMNS``.

        Args:
            table: Table name (uppercased automatically).
            schema: Optional schema/owner name (uppercased).  When None the
                query omits the OWNER filter.

        Returns:
            List of column name strings ordered by COLUMN_ID.  Empty list if
            the table is not found.
        """
        owner_clause = ""
        params: dict = {"table_name": table.upper()}
        if schema:
            owner_clause = " AND OWNER = :owner"
            params["owner"] = schema.upper()

        sql = (
            "SELECT COLUMN_NAME FROM ALL_TAB_COLUMNS "
            "WHERE TABLE_NAME = :table_name"
            f"{owner_clause} "
            "ORDER BY COLUMN_ID"
        )
        try:
            df = pd.read_sql(sql, self._connection, params=params)
            return df["COLUMN_NAME"].tolist()
        except Exception:
            return []

    def table_exists(self, table: str, schema: Optional[str] = None) -> bool:
        """Check if *table* exists via ``ALL_TABLES``.

        Args:
            table: Table name (case-insensitive).
            schema: Optional schema/owner qualifier.

        Returns:
            True when the table exists.
        """
        owner_clause = ""
        params: dict = {"table_name": table.upper()}
        if schema:
            owner_clause = " AND OWNER = :owner"
            params["owner"] = schema.upper()

        sql = (
            "SELECT COUNT(*) AS COUNT_ FROM ALL_TABLES "
            "WHERE TABLE_NAME = :table_name"
            f"{owner_clause}"
        )
        df = pd.read_sql(sql, self._connection, params=params)
        return int(df["COUNT_"].iloc[0]) > 0

    def get_column_metadata(
        self, table: str, schema: Optional[str] = None
    ) -> dict[str, ColumnMeta]:
        """Return rich column metadata from ``ALL_TAB_COLUMNS``.

        Reads the same catalog columns the legacy reconciliation engine read
        (``DATA_TYPE``/``DATA_LENGTH``/``DATA_PRECISION``/``DATA_SCALE``/
        ``NULLABLE``) and normalises each raw type to a portable
        :class:`CanonicalType` via :func:`_normalize_oracle_type`.  Oracle's
        ``'Y'``/``'N'`` nullability is normalised to a real bool.

        Args:
            table: Table name (uppercased automatically).
            schema: Optional schema/owner name (uppercased).

        Returns:
            Mapping of column name to :class:`ColumnMeta` ordered by
            ``COLUMN_ID``.  Empty dict if the table is not found.
        """
        owner_clause = ""
        params: dict = {"table_name": table.upper()}
        if schema:
            owner_clause = " AND OWNER = :owner"
            params["owner"] = schema.upper()

        sql = (
            "SELECT COLUMN_NAME, DATA_TYPE, DATA_LENGTH, DATA_PRECISION, "
            "DATA_SCALE, NULLABLE FROM ALL_TAB_COLUMNS "
            "WHERE TABLE_NAME = :table_name"
            f"{owner_clause} "
            "ORDER BY COLUMN_ID"
        )
        try:
            df = pd.read_sql(sql, self._connection, params=params)
        except Exception:
            return {}

        result: dict[str, ColumnMeta] = {}
        for _, row in df.iterrows():
            name = row["COLUMN_NAME"]
            raw_type = str(row["DATA_TYPE"])
            precision = _as_int(row["DATA_PRECISION"])
            scale = _as_int(row["DATA_SCALE"])
            length = _as_int(row["DATA_LENGTH"])
            nullable = str(row["NULLABLE"]).strip().upper() != "N"
            result[name] = ColumnMeta(
                name=name,
                canonical_type=_normalize_oracle_type(raw_type, precision, scale),
                raw_type=raw_type,
                nullable=nullable,
                length=length,
                precision=precision,
                scale=scale,
            )
        return result

    # ------------------------------------------------------------------
    # Data export
    # ------------------------------------------------------------------

    def extract_to_file(
        self, query: str, output_path: str, delimiter: str = "|"
    ) -> int:
        """Execute *query* and write results to a pipe-delimited text file.

        Fetches data in chunks of 10 000 rows to keep memory usage bounded.

        Args:
            query: SELECT statement to execute.
            output_path: Path of the output file (created or overwritten).
            delimiter: Column separator string.  Defaults to ``"|"``.

        Returns:
            Total number of data rows written (not counting the header).

        Raises:
            RuntimeError: If the query or file write fails.
        """
        _CHUNK = 10_000
        total_rows = 0

        try:
            cursor = self._connection.cursor()
            cursor.execute(query)
            col_names = [desc[0] for desc in cursor.description]

            with open(output_path, "w", encoding="utf-8") as fh:
                fh.write(delimiter.join(col_names) + "\n")
                while True:
                    rows = cursor.fetchmany(_CHUNK)
                    if not rows:
                        break
                    for row in rows:
                        fh.write(
                            delimiter.join(
                                "" if val is None else str(val) for val in row
                            )
                            + "\n"
                        )
                        total_rows += 1
            cursor.close()
        except oracledb.Error as exc:
            raise RuntimeError(f"Oracle extraction failed: {exc}") from exc

        return total_rows
