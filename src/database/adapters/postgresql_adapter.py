"""PostgreSQL database adapter using psycopg2.

``psycopg2`` is an optional dependency.  If it is not installed, the adapter
can still be instantiated, but :meth:`PostgreSQLAdapter.connect` will raise a
descriptive :class:`ImportError` with installation instructions rather than a
cryptic ``ModuleNotFoundError``.

Connection parameters are resolved from the central
:func:`~src.config.db_config.get_db_config` (single source of truth), which
reads these environment variables and resolves ``DB_USER`` / ``DB_PASSWORD``
through the active ``SECRETS_PROVIDER`` (env / Vault / Azure):

- ``DB_HOST`` — PostgreSQL server hostname (default ``localhost``)
- ``DB_PORT`` — PostgreSQL server port (default ``5432``)
- ``DB_NAME`` — Database / catalog name (default ``valdo`` — the Valdo
  application database; matches :mod:`src.database.db_url`)
- ``DB_USER`` — Database username (default ``postgres``)
- ``DB_PASSWORD`` — Database password (no default; required to connect)
"""

from __future__ import annotations

from typing import Optional

import sys
from typing import Any

import pandas as pd

from src.config.db_config import get_db_config
from src.database.adapters._delimited_writer import _write_delimited
from src.database.adapters.base import CanonicalType, ColumnMeta, DatabaseAdapter


def _pg_as_int(value) -> Optional[int]:
    """Coerce an information_schema cell to ``int`` or ``None``.

    Args:
        value: A scalar catalog cell (may be ``None``/``NaN``/``float``/``int``).

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


def _normalize_postgres_type(data_type: str) -> CanonicalType:
    """Map a PostgreSQL raw catalog type to a portable :class:`CanonicalType`.

    Pure function (no DB access) per ADR 0022 §3.  PostgreSQL has a native
    boolean, so ``boolean``/``bool`` map to :attr:`CanonicalType.BOOLEAN`
    exactly — the one backend where the mapping's ``boolean`` field reconciles
    without an advisory note.

    Args:
        data_type: The ``data_type`` string from ``information_schema.columns``
            (e.g. ``"character varying"``, ``"integer"``, ``"timestamp with
            time zone"``).

    Returns:
        The matching :class:`CanonicalType`; :attr:`CanonicalType.UNKNOWN`
        for an unrecognised type string.
    """
    t = (data_type or "").strip().lower()

    if t in {
        "varchar",
        "character varying",
        "char",
        "character",
        "bpchar",
        "text",
    }:
        return CanonicalType.STRING
    if t in {"integer", "int", "int2", "int4", "int8", "bigint", "smallint"}:
        return CanonicalType.INTEGER
    if t in {"numeric", "decimal"}:
        return CanonicalType.DECIMAL
    if t in {"real", "double precision", "float8", "float4"}:
        return CanonicalType.FLOAT
    if t in {"boolean", "bool"}:
        return CanonicalType.BOOLEAN
    if t == "date":
        return CanonicalType.DATE
    if t.startswith("timestamp"):
        return CanonicalType.TIMESTAMP
    if t == "bytea":
        return CanonicalType.BINARY
    return CanonicalType.UNKNOWN


class PostgreSQLAdapter(DatabaseAdapter):
    """Database adapter for PostgreSQL using ``psycopg2``.

    Reads connection parameters from ``DB_*`` environment variables so it
    fits naturally alongside the Oracle adapter (which uses ``ORACLE_*``
    vars) without conflict.

    Example::

        # .env
        # DB_ADAPTER=postgresql
        # DB_HOST=myserver
        # DB_PORT=5432
        # DB_NAME=mydb
        # DB_USER=myuser
        # DB_PASSWORD=secret

        with PostgreSQLAdapter() as adapter:
            df = adapter.execute_query("SELECT version()")
    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        database: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> None:
        """Initialise PostgreSQL adapter from explicit values or central config.

        When a parameter is *None* it is resolved from the single source of
        truth :func:`~src.config.db_config.get_db_config`, so the PostgreSQL
        defaults (``DB_NAME=valdo``) and the credential resolution path
        (``DB_USER`` / ``DB_PASSWORD`` via ``SECRETS_PROVIDER``) match the rest
        of the application instead of being re-derived here with divergent
        defaults (S16-4, #424).

        Args:
            host: PostgreSQL hostname.  Falls back to ``DB_HOST`` or
                ``"localhost"``.
            port: PostgreSQL port.  Falls back to ``DB_PORT`` or ``5432``.
            database: Database name.  Falls back to ``DB_NAME`` or ``"valdo"``.
            username: Database user.  Falls back to ``DB_USER`` or
                ``"postgres"``.
            password: Database password.  Falls back to ``DB_PASSWORD`` (via
                the secrets provider) or ``""`` (empty — connection will fail
                at the server level).
        """
        cfg = get_db_config()
        self.host: str = host or cfg.db_host
        self.port: int = int(port or cfg.db_port)
        self.database: str = database or cfg.db_name
        self.username: str = username or cfg.db_user
        self.password: str = password if password is not None else cfg.db_password
        self._connection: Optional[object] = None  # psycopg2.connection at runtime

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    @staticmethod
    def _get_psycopg2():
        """Return the psycopg2 module, raising ImportError if not available.

        Uses a dynamic lookup from ``sys.modules`` so that unit tests can
        inject a mock via :func:`unittest.mock.patch.dict` on ``sys.modules``.

        Returns:
            The ``psycopg2`` module object.

        Raises:
            ImportError: If ``psycopg2`` is not found in ``sys.modules`` and
                cannot be imported.
        """
        if "psycopg2" in sys.modules and sys.modules["psycopg2"] is None:
            # Explicitly set to None by test/caller — treat as absent.
            raise ImportError(
                "psycopg2 is required for the PostgreSQL adapter but is not "
                "installed.  Install it with: pip install psycopg2-binary"
            )
        try:
            import psycopg2  # type: ignore[import]
            return psycopg2
        except ImportError:
            raise ImportError(
                "psycopg2 is required for the PostgreSQL adapter but is not "
                "installed.  Install it with: pip install psycopg2-binary"
            )

    def connect(self) -> None:
        """Open a psycopg2 connection.

        Raises:
            ImportError: If ``psycopg2`` is not installed (includes pip hint).
            ConnectionError: If psycopg2 cannot reach the server.
        """
        psycopg2 = self._get_psycopg2()
        try:
            self._connection = psycopg2.connect(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.username,
                password=self.password,
            )
        except psycopg2.Error as exc:
            raise ConnectionError(
                f"Failed to connect to PostgreSQL "
                f"(host={self.host!r}, port={self.port}, db={self.database!r}): {exc}"
            ) from exc

    def disconnect(self) -> None:
        """Close the PostgreSQL connection.

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

        Uses :func:`pandas.read_sql` with the psycopg2 connection.

        Args:
            sql: SQL query string.  Use ``%(name)s`` for named placeholders
                (psycopg2 style).
            params: Optional named bind parameters.

        Returns:
            DataFrame with query results.

        Raises:
            RuntimeError: If the query fails.
        """
        try:
            return pd.read_sql(sql, self._connection, params=params)
        except Exception as exc:
            raise RuntimeError(f"PostgreSQL query execution failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Schema inspection
    # ------------------------------------------------------------------

    def get_table_columns(
        self, table: str, schema: Optional[str] = None
    ) -> list:
        """Return column names for *table* from ``information_schema.columns``.

        Args:
            table: Table name (lowercased automatically for PostgreSQL).
            schema: Optional schema name.  When None defaults to ``public``.

        Returns:
            List of column name strings in ordinal position order.  Empty list
            if the table does not exist.
        """
        effective_schema = schema or "public"
        sql = (
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = %(table)s AND table_schema = %(schema)s "
            "ORDER BY ordinal_position"
        )
        try:
            df = pd.read_sql(
                sql,
                self._connection,
                params={"table": table.lower(), "schema": effective_schema},
            )
            return df["column_name"].tolist()
        except Exception:
            return []

    def table_exists(self, table: str, schema: Optional[str] = None) -> bool:
        """Check if *table* exists in ``information_schema.tables``.

        Args:
            table: Table name (case-insensitive).
            schema: Optional schema name.  Defaults to ``public``.

        Returns:
            True if the table exists.
        """
        effective_schema = schema or "public"
        sql = (
            "SELECT COUNT(*) AS count_ FROM information_schema.tables "
            "WHERE table_name = %(table)s AND table_schema = %(schema)s"
        )
        df = pd.read_sql(
            sql,
            self._connection,
            params={"table": table.lower(), "schema": effective_schema},
        )
        return int(df["count_"].iloc[0]) > 0

    def get_column_metadata(
        self, table: str, schema: Optional[str] = None
    ) -> dict[str, ColumnMeta]:
        """Return rich column metadata from ``information_schema.columns``.

        Reads ``data_type``/``character_maximum_length``/``numeric_precision``/
        ``numeric_scale``/``is_nullable`` and normalises each raw type to a
        portable :class:`CanonicalType` via :func:`_normalize_postgres_type`.
        ``is_nullable`` (``"YES"``/``"NO"``) is normalised to a real bool.

        Args:
            table: Table name (lowercased automatically for PostgreSQL).
            schema: Optional schema name.  Defaults to ``public``.

        Returns:
            Mapping of column name to :class:`ColumnMeta` ordered by
            ``ordinal_position``.  Empty dict if the table is not found.
        """
        effective_schema = schema or "public"
        sql = (
            "SELECT column_name, data_type, character_maximum_length, "
            "numeric_precision, numeric_scale, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name = %(table)s AND table_schema = %(schema)s "
            "ORDER BY ordinal_position"
        )
        try:
            df = pd.read_sql(
                sql,
                self._connection,
                params={"table": table.lower(), "schema": effective_schema},
            )
        except Exception:
            return {}

        result: dict[str, ColumnMeta] = {}
        for _, row in df.iterrows():
            name = row["column_name"]
            raw_type = str(row["data_type"])
            nullable = str(row["is_nullable"]).strip().upper() != "NO"
            result[name] = ColumnMeta(
                name=name,
                canonical_type=_normalize_postgres_type(raw_type),
                raw_type=raw_type,
                nullable=nullable,
                length=_pg_as_int(row["character_maximum_length"]),
                precision=_pg_as_int(row["numeric_precision"]),
                scale=_pg_as_int(row["numeric_scale"]),
            )
        return result

    # ------------------------------------------------------------------
    # Data export
    # ------------------------------------------------------------------

    def limit_clause(self, param_name: str = "row_limit") -> str:
        """Return PostgreSQL's bound ``LIMIT`` clause in psycopg2 pyformat.

        Uses the ``%(name)s`` pyformat placeholder psycopg2 expects (ADR 0022
        §4) so the row limit is bound rather than concatenated, preserving the
        S13.5-4 hardening.

        Args:
            param_name: The bind-parameter name for the row limit.

        Returns:
            A leading-space SQL fragment, e.g. ``" LIMIT %(row_limit)s"``.
        """
        return f" LIMIT %({param_name})s"

    def extract_to_file(
        self,
        query: str,
        output_path: str,
        delimiter: str = "|",
        params: Optional[dict] = None,
    ) -> int:
        """Execute *query* and stream results to a delimited text file.

        Uses a server-side cursor (``cursor_factory=psycopg2.extras.DictCursor``)
        to stream rows in batches of 10 000, keeping memory usage bounded for
        large result sets.

        Args:
            query: SELECT statement to execute.
            output_path: Path to the output file.
            delimiter: Column separator.  Defaults to ``"|"``.
            params: Optional named bind parameters (psycopg2 ``%(name)s``
                pyformat).  Bound, never concatenated.

        Returns:
            Total number of data rows written.

        Raises:
            RuntimeError: If the query or file write fails.
        """
        _CHUNK = 10_000

        def _batches(cursor):
            """Yield successive ``fetchmany`` chunks until the cursor drains."""
            while True:
                rows = cursor.fetchmany(_CHUNK)
                if not rows:
                    break
                yield rows

        try:
            cursor = self._connection.cursor()
            cursor.execute(query, params or None)
            col_names = [desc[0] for desc in cursor.description] if cursor.description else []
            # Delegate the write to the shared csv-based helper (S16-2, #426):
            # values containing the delimiter, quotes, or newlines are quoted
            # via QUOTE_MINIMAL and round-trip with the comparator's reader.
            # The fetchmany batches stream through so memory stays bounded.
            total_rows = _write_delimited(
                output_path, delimiter, col_names, _batches(cursor)
            )
            cursor.close()
        except Exception as exc:
            raise RuntimeError(f"PostgreSQL extraction failed: {exc}") from exc

        return total_rows
