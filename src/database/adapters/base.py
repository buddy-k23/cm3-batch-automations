"""Abstract base class for pluggable database adapters.

All concrete adapters (Oracle, PostgreSQL, SQLite, …) must implement every
abstract method defined here so that the rest of the application can swap
backends via the :func:`~src.database.adapters.factory.get_database_adapter`
factory without changing call sites.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import pandas as pd


class CanonicalType(Enum):
    """Backend-neutral column type model (ADR 0022 §3).

    Each concrete adapter normalises its dialect-native catalog type string
    into one of these members so that reconciliation can compare a mapping's
    declared type against a *portable* canonical type rather than against an
    Oracle/PostgreSQL/SQLite-specific raw type string.

    Members:
        STRING: Character / text types (VARCHAR2, text, TEXT, …).
        INTEGER: Whole-number types (NUMBER(p,0), integer, INTEGER, …).
        DECIMAL: Fixed-point numeric types (NUMBER(p,s>0), numeric, …).
        FLOAT: Approximate numeric types (FLOAT, double precision, REAL, …).
        BOOLEAN: Native boolean (PostgreSQL only; advisory elsewhere).
        DATE: Date-only types.
        TIMESTAMP: Date+time types.
        BINARY: Binary / large-object byte types (BLOB, bytea, RAW, …).
        UNKNOWN: Type could not be determined (e.g. a typeless SQLite column).
            Treated as compatible-with-everything plus an informational note
            downstream — never a silent failure.
    """

    STRING = "STRING"
    INTEGER = "INTEGER"
    DECIMAL = "DECIMAL"
    FLOAT = "FLOAT"
    BOOLEAN = "BOOLEAN"
    DATE = "DATE"
    TIMESTAMP = "TIMESTAMP"
    BINARY = "BINARY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ColumnMeta:
    """Portable per-column metadata returned by :meth:`get_column_metadata`.

    A frozen (immutable) dataclass carrying the dialect-neutral facts the
    reconciliation engine needs, with the backend-native ``raw_type`` retained
    for human-readable messages.

    Attributes:
        name: Column name as reported by the backend catalog.
        canonical_type: The portable :class:`CanonicalType` the adapter mapped
            this column's raw type to.
        raw_type: Backend-native type string (e.g. ``"VARCHAR2"``,
            ``"numeric"``, ``"INTEGER"``), kept verbatim for messages.
        nullable: ``True`` if the column accepts NULL, ``False`` otherwise.
            Normalised to a real bool (no Oracle ``'Y'``/``'N'`` leak).
        length: Declared character length, or ``None`` when not applicable /
            not reported by the backend.
        precision: Declared numeric precision, or ``None``.
        scale: Declared numeric scale, or ``None``.
    """

    name: str
    canonical_type: CanonicalType
    raw_type: str
    nullable: bool
    length: Optional[int]
    precision: Optional[int]
    scale: Optional[int]


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

    @abstractmethod
    def get_column_metadata(
        self, table: str, schema: Optional[str] = None
    ) -> dict[str, ColumnMeta]:
        """Return rich per-column metadata for *table* (ADR 0022 §1).

        Unlike :meth:`get_table_columns` (names only), this returns the type,
        nullability, and length/precision/scale needed by the reconciliation
        engine — with each column's backend-native type normalised to a
        portable :class:`CanonicalType` by the adapter itself, so that dialect
        knowledge stays inside the adapter rather than leaking into consumers.

        Args:
            table: Table name (case handling is driver-dependent).
            schema: Optional schema/owner qualifier.  When *None* the adapter
                uses the connection's default schema.

        Returns:
            A mapping of column name to :class:`ColumnMeta`, in the catalog's
            natural order where the backend provides one.  Returns an empty
            dict if the table does not exist.
        """

    # ------------------------------------------------------------------
    # Data export
    # ------------------------------------------------------------------

    @abstractmethod
    def extract_to_file(
        self,
        query: str,
        output_path: str,
        delimiter: str = "|",
        params: Optional[dict] = None,
    ) -> int:
        """Execute *query* and write results to a delimited text file.

        The first line of the output file is a header row containing the
        column names joined by *delimiter*.  Each subsequent line is one data
        row.  ``None`` values are written as empty strings.  Writing goes
        through the stdlib :mod:`csv` module (``QUOTE_MINIMAL``) so values
        containing the delimiter, the quote character, or a newline are quoted
        and round-trip through the comparator's :func:`pandas.read_csv` reader
        rather than corrupting the file (S16-2, #426); simple values are written
        unquoted.

        Args:
            query: The SELECT statement to execute.
            output_path: Absolute path of the output file to create (or
                overwrite).
            delimiter: Column separator.  Defaults to ``"|"``.
            params: Optional named bind parameters for *query* (driver-specific
                placeholder format).  Bound, never concatenated — added in
                S15-1 (#405) so parameterised query extraction is supported on
                every backend.

        Returns:
            The total number of data rows written (excluding the header).

        Raises:
            RuntimeError: If the query or file write fails.
        """

    # ------------------------------------------------------------------
    # Dialect helpers
    # ------------------------------------------------------------------

    def limit_clause(self, param_name: str = "row_limit") -> str:
        """Return a dialect-appropriate, *bound* row-limit clause (ADR 0022 §4).

        The returned clause references a bind placeholder named *param_name*
        rather than interpolating the limit value, preserving the S13.5-4
        hardening (the row limit is always bound, never concatenated).  The
        default implementation emits the ANSI/SQLite/PostgreSQL ``LIMIT``
        form with a named (``:name``) placeholder; adapters whose driver uses
        a different placeholder style or limit syntax (e.g. Oracle's
        ``FETCH FIRST … ROWS ONLY``, PostgreSQL's ``%(name)s`` pyformat)
        override this.

        Args:
            param_name: The bind-parameter name the caller will populate with
                the (validated, positive-integer) row limit.

        Returns:
            A SQL fragment beginning with a leading space, suitable for
            appending to a ``SELECT … FROM table`` statement.
        """
        return f" LIMIT :{param_name}"

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
