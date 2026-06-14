"""Abstract base class for pluggable database adapters.

All concrete adapters (Oracle, PostgreSQL, SQLite, …) must implement every
abstract method defined here so that the rest of the application can swap
backends via the :func:`~src.database.adapters.factory.get_database_adapter`
factory without changing call sites.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

import pandas as pd


class DatabaseAdapter(ABC):
    """Abstract interface for a database connection adapter.

    Provides a uniform API for connecting, querying, inspecting schema, and
    extracting data to a delimited file regardless of the underlying database
    engine.

    Concrete subclasses must implement all six abstract methods.  The
    context-manager protocol (``with adapter:`` …) is provided by this base
    class and delegates to :meth:`connect` / :meth:`disconnect`.

    Example::

        with get_database_adapter("sqlite") as adapter:
            df = adapter.execute_query("SELECT 1 AS n")
            print(df)
    """

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    def connect(self) -> None:
        """Open the database connection.

        The connection object is stored on the instance so that subsequent
        method calls can reuse it.  Calling :meth:`connect` more than once
        without an intervening :meth:`disconnect` is implementation-defined.

        Raises:
            ConnectionError: If the underlying driver cannot reach the server.
            ImportError: If a required third-party driver is not installed.
            RuntimeError: If required credentials are missing.
        """

    @abstractmethod
    def disconnect(self) -> None:
        """Close the database connection and release resources.

        Safe to call even when no connection is open — implementations must
        silently ignore a no-op close.
        """

    # ------------------------------------------------------------------
    # Query execution
    # ------------------------------------------------------------------

    @abstractmethod
    def execute_query(self, sql: str, params: Optional[dict] = None) -> pd.DataFrame:
        """Execute a SELECT statement and return results as a DataFrame.

        Args:
            sql: The SQL query to execute.
            params: Optional named bind parameters (driver-specific format).

        Returns:
            A :class:`pandas.DataFrame` with one column per result column and
            one row per result row.  An empty query returns an empty DataFrame.

        Raises:
            RuntimeError: If the query fails at the driver level.
        """

    # ------------------------------------------------------------------
    # Schema inspection
    # ------------------------------------------------------------------

    @abstractmethod
    def get_table_columns(
        self, table: str, schema: Optional[str] = None
    ) -> list:
        """Return the ordered list of column names for *table*.

        Args:
            table: Table name (case handling is driver-dependent).
            schema: Optional schema/owner qualifier.  When *None* the adapter
                uses the connection's default schema.

        Returns:
            List of column name strings in definition order.  Returns an empty
            list if the table does not exist.
        """

    @abstractmethod
    def table_exists(self, table: str, schema: Optional[str] = None) -> bool:
        """Check whether *table* exists in the database.

        Args:
            table: Table name to look up.
            schema: Optional schema/owner qualifier.

        Returns:
            ``True`` if the table exists, ``False`` otherwise.
        """

    # ------------------------------------------------------------------
    # Data export
    # ------------------------------------------------------------------

    @abstractmethod
    def extract_to_file(
        self, query: str, output_path: str, delimiter: str = "|"
    ) -> int:
        """Execute *query* and write results to a delimited text file.

        The first line of the output file is a header row containing the
        column names joined by *delimiter*.  Each subsequent line is one data
        row.  ``None`` values are written as empty strings.

        Args:
            query: The SELECT statement to execute.
            output_path: Absolute path of the output file to create (or
                overwrite).
            delimiter: Column separator.  Defaults to ``"|"``.

        Returns:
            The total number of data rows written (excluding the header).

        Raises:
            RuntimeError: If the query or file write fails.
        """

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "DatabaseAdapter":
        """Connect and return *self* for use as a context manager.

        Returns:
            This adapter instance after connecting.
        """
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Disconnect when leaving the ``with`` block.

        Args:
            exc_type: Exception type, if any.
            exc_val: Exception value, if any.
            exc_tb: Traceback, if any.
        """
        self.disconnect()
