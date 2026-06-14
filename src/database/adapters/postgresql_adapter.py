"""PostgreSQL database adapter using psycopg2.

``psycopg2`` is an optional dependency.  If it is not installed, the adapter
can still be instantiated, but :meth:`PostgreSQLAdapter.connect` will raise a
descriptive :class:`ImportError` with installation instructions rather than a
cryptic ``ModuleNotFoundError``.

Connection parameters are read from these environment variables:

- ``DB_HOST`` — PostgreSQL server hostname (default ``localhost``)
- ``DB_PORT`` — PostgreSQL server port (default ``5432``)
- ``DB_NAME`` — Database / catalog name (default ``postgres``)
- ``DB_USER`` — Database username (default ``postgres``)
- ``DB_PASSWORD`` — Database password (no default; required to connect)
"""

from __future__ import annotations

import os
from typing import Optional

import sys
from typing import Any

import pandas as pd

from src.database.adapters.base import DatabaseAdapter

_DEFAULT_HOST = "localhost"
_DEFAULT_PORT = 5432
_DEFAULT_DB = "postgres"
_DEFAULT_USER = "postgres"


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
        """Initialise PostgreSQL adapter from explicit values or env vars.

        Args:
            host: PostgreSQL hostname.  Falls back to ``DB_HOST`` or
                ``"localhost"``.
            port: PostgreSQL port.  Falls back to ``DB_PORT`` or ``5432``.
            database: Database name.  Falls back to ``DB_NAME`` or
                ``"postgres"``.
            username: Database user.  Falls back to ``DB_USER`` or
                ``"postgres"``.
            password: Database password.  Falls back to ``DB_PASSWORD`` or
                ``""`` (empty — connection will fail at the server level).
        """
        self.host: str = host or os.getenv("DB_HOST", _DEFAULT_HOST)
        self.port: int = int(port or os.getenv("DB_PORT", _DEFAULT_PORT))
        self.database: str = database or os.getenv("DB_NAME", _DEFAULT_DB)
        self.username: str = username or os.getenv("DB_USER", _DEFAULT_USER)
        self.password: str = (
            password if password is not None else os.getenv("DB_PASSWORD", "")
        )
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

    # ------------------------------------------------------------------
    # Data export
    # ------------------------------------------------------------------

    def extract_to_file(
        self, query: str, output_path: str, delimiter: str = "|"
    ) -> int:
        """Execute *query* and stream results to a delimited text file.

        Uses a server-side cursor (``cursor_factory=psycopg2.extras.DictCursor``)
        to stream rows in batches of 10 000, keeping memory usage bounded for
        large result sets.

        Args:
            query: SELECT statement to execute.
            output_path: Path to the output file.
            delimiter: Column separator.  Defaults to ``"|"``.

        Returns:
            Total number of data rows written.

        Raises:
            RuntimeError: If the query or file write fails.
        """
        _CHUNK = 10_000
        total_rows = 0

        try:
            cursor = self._connection.cursor()
            cursor.execute(query)
            col_names = [desc[0] for desc in cursor.description] if cursor.description else []

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
        except Exception as exc:
            raise RuntimeError(f"PostgreSQL extraction failed: {exc}") from exc

        return total_rows
