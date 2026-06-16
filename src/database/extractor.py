"""Database data extraction utilities.

Backend-agnostic (S15-1, #405, ADR 0022 §4)
-------------------------------------------
:class:`DataExtractor` is constructed with a
:class:`~src.database.adapters.base.DatabaseAdapter` (obtained from
:func:`~src.database.adapters.factory.get_database_adapter`) rather than the
Oracle-only :class:`~src.database.connection.OracleConnection`.  All query
execution and file extraction delegate to the adapter, so ``valdo extract``
runs on Oracle, PostgreSQL, or SQLite, with each backend supplying its own
dialect (notably the row-limit clause — Oracle ``FETCH FIRST``, PostgreSQL /
SQLite ``LIMIT`` — via :meth:`DatabaseAdapter.limit_clause`, replacing the old
hard-wired Oracle ``ROWNUM`` paging).

Security note (S13.5-4, #410 — preserved on the adapter path)
-------------------------------------------------------------
SQL identifiers (table and column names) cannot be supplied as driver bind
parameters, so they are *allow-listed* with :func:`_validate_identifier`
before being placed into the SQL text.  Every interpolated **value** (e.g. a
row limit) is bound as a parameter rather than concatenated.  Raw, free-form
``WHERE`` clauses are no longer accepted on the table-based public path —
arbitrary SQL must go through :meth:`DataExtractor.extract_by_query` (or the
``--query``/``--sql-file`` CLI modes), which are explicit "operator supplies
the whole statement" entry points rather than a string-concatenation site.
"""

import re
import pandas as pd
from typing import Optional, List, Dict, Any

from .adapters.base import DatabaseAdapter


class IdentifierValidationError(ValueError):
    """Raised when a SQL identifier fails allow-list validation.

    A subclass of :class:`ValueError` so existing ``except ValueError``
    handlers keep working while callers that care can catch the precise type.
    """


# A SQL identifier is one or more dot-separated parts (``SCHEMA.TABLE``), each
# starting with a letter or underscore and containing only letters, digits,
# underscore, or ``$`` (Oracle/PostgreSQL legal identifier chars).  This
# rejects quotes, semicolons, whitespace, parentheses, and comment markers
# (``--`` / ``/*``) by construction.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$]*)*$")


def _validate_identifier(identifier: str) -> str:
    """Validate a SQL identifier against a strict allow-list.

    Identifiers (table / column names) cannot be passed as driver bind
    parameters, so any operator-supplied identifier that is interpolated into
    SQL text must be validated.  Accepts plain (``CUSTOMERS``) and
    schema-qualified (``APP_INT.CUSTOMERS``) names composed only of letters,
    digits, underscores, and ``$``.  Rejects anything containing quotes,
    semicolons, whitespace, parentheses, or comment markers.

    Args:
        identifier: The candidate table or column name.

    Returns:
        The identifier unchanged when valid (so callers can inline the result).

    Raises:
        IdentifierValidationError: If *identifier* is not a string or does not
            match the strict identifier pattern.
    """
    if not isinstance(identifier, str) or not _IDENTIFIER_RE.match(identifier):
        raise IdentifierValidationError(
            f"Invalid SQL identifier: {identifier!r}. "
            "Identifiers must match [A-Za-z_][A-Za-z0-9_$]* (optionally "
            "schema-qualified with a dot) and may not contain quotes, "
            "semicolons, whitespace, parentheses, or comment markers."
        )
    return identifier


def _validate_limit(limit: Any) -> int:
    """Validate that *limit* is a positive integer suitable for binding.

    Args:
        limit: The candidate row limit (expected to be a positive ``int``;
            booleans are rejected to avoid ``True``/``False`` surprises).

    Returns:
        The validated integer limit.

    Raises:
        ValueError: If *limit* is not a positive integer.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError(
            f"limit must be a positive integer, got {limit!r}"
        )
    return limit


def _reject_raw_where(where_clause: Optional[str]) -> None:
    """Reject any raw WHERE clause on the table-based extract path.

    Free-form WHERE text cannot be safely parameterised or allow-listed
    without a structured-filter model, and no current consumer passes one
    (the CLI ``extract`` command exposes no ``--where`` flag and
    ``db_file_compare_service`` calls :meth:`extract_table` with the table name
    only).  Rather than carry an injection surface for an unused feature, the
    table-based path refuses a raw WHERE and directs callers to the explicit
    full-statement entry points.

    Residual-risk note: callers needing a filter must use
    :meth:`extract_by_query` / the ``--query``/``--sql-file`` CLI modes, which
    are documented "operator supplies the whole SQL statement" surfaces and
    are not reachable from untrusted end-user input in the current call graph.

    Args:
        where_clause: The candidate WHERE clause (must be ``None``).

    Raises:
        ValueError: If a non-empty *where_clause* is supplied.
    """
    if where_clause:
        raise ValueError(
            "Raw 'where_clause' is not supported on the table-based extract "
            "path for SQL-injection safety (S13.5-4, #410). Use "
            "extract_by_query() / the --query or --sql-file CLI modes to run a "
            "full SQL statement, or pass a column/limit filter instead."
        )


class DataExtractor:
    """Extract data from a database via a pluggable :class:`DatabaseAdapter`.

    Backend-agnostic since S15-1 (#405): the extractor builds portable SQL
    (with the S13.5-4 hardening) and delegates execution to the adapter, so
    the same extractor works against Oracle, PostgreSQL, and SQLite.
    """

    def __init__(self, adapter: DatabaseAdapter):
        """Initialize data extractor.

        Args:
            adapter: A connected (or context-managed) :class:`DatabaseAdapter`
                instance, typically from
                :func:`~src.database.adapters.factory.get_database_adapter`.
                The extractor delegates all execution to this adapter.
        """
        self.adapter = adapter

    def extract_table(self, table_name: str, columns: Optional[List[str]] = None,
                     where_clause: Optional[str] = None, limit: Optional[int] = None) -> pd.DataFrame:
        """Extract data from a table.

        Args:
            table_name: Name of the table (allow-list validated identifier).
            columns: List of columns to extract (None = all). Each name is
                allow-list validated.
            where_clause: Not supported on this path — must be ``None``. Use
                :meth:`extract_by_query` for arbitrary SQL.
            limit: Optional positive-integer row limit (bound as a parameter).

        Returns:
            DataFrame with extracted data

        Raises:
            IdentifierValidationError: If the table name or any column name
                fails identifier validation.
            ValueError: If ``where_clause`` is supplied, or ``limit`` is not a
                positive integer.
        """
        # Validate identifiers (cannot be bind params) and reject raw WHERE.
        safe_table = _validate_identifier(table_name)
        _reject_raw_where(where_clause)

        # Build column list from validated identifiers.
        if columns:
            col_list = ', '.join(_validate_identifier(c) for c in columns)
        else:
            col_list = '*'

        # Build query; bind the limit value as a parameter (never interpolate).
        # The limit clause is dialect-appropriate (Oracle FETCH FIRST,
        # PostgreSQL/SQLite LIMIT) and supplied by the adapter (ADR 0022 §4),
        # so the old hard-wired Oracle ROWNUM paging is gone.
        query = f"SELECT {col_list} FROM {safe_table}"
        params: Optional[Dict[str, Any]] = None

        if limit is not None:
            safe_limit = _validate_limit(limit)
            query += self.adapter.limit_clause("row_limit")
            params = {"row_limit": safe_limit}

        return self.adapter.execute_query(query, params)

    def extract_sample(self, table_name: str, sample_size: int = 1000,
                      columns: Optional[List[str]] = None) -> pd.DataFrame:
        """Extract a sample of data from a table.
        
        Args:
            table_name: Name of the table
            sample_size: Number of rows to sample
            columns: List of columns to extract
            
        Returns:
            DataFrame with sampled data
        """
        return self.extract_table(table_name, columns=columns, limit=sample_size)

    def extract_by_query(self, query: str, params: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        """Extract data using custom query.

        Args:
            query: SQL query
            params: Optional query parameters

        Returns:
            DataFrame with query results
        """
        return self.adapter.execute_query(query, params)

    def extract_to_file(self, table_name: Optional[str] = None, output_file: str = None,
                       columns: Optional[List[str]] = None,
                       where_clause: Optional[str] = None,
                       delimiter: str = '|',
                       chunk_size: int = 10000,
                       query: Optional[str] = None,
                       params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Extract data to a delimited file via the adapter.

        Args:
            table_name: Name of the table (optional if query is provided;
                allow-list validated when used).
            output_file: Output file path
            columns: List of columns to extract (ignored if query is provided;
                each name allow-list validated when used).
            where_clause: Not supported on the table-based path — must be
                ``None`` (ignored entirely when ``query`` is provided). Use the
                ``query`` argument for arbitrary SQL.
            delimiter: File delimiter
            chunk_size: Number of rows per chunk (informational — the adapter
                chooses its own fetch batching; retained for the result dict).
            query: Optional raw SQL query (overrides table_name/columns).
            params: Optional named bind parameters for *query*, bound by the
                adapter (S15-1, #405).  Reconciles the previously broken
                ``extract_to_file(params=…)`` call site so parameterised
                query extraction works end-to-end.

        Returns:
            Dictionary with extraction statistics

        Raises:
            IdentifierValidationError: If the table name or any column name
                fails identifier validation on the table-based path.
            ValueError: If ``where_clause`` is supplied on the table-based
                path, or neither ``table_name`` nor ``query`` is given.
            RuntimeError: If the adapter's extraction fails.
        """
        # Build query
        if query:
            # Use custom SQL query directly (explicit full-statement entry point).
            sql_query = query
        elif table_name:
            # Build table-based query from allow-listed identifiers; raw WHERE
            # is not supported on this path (S13.5-4, #410).
            safe_table = _validate_identifier(table_name)
            _reject_raw_where(where_clause)
            if columns:
                col_list = ', '.join(_validate_identifier(c) for c in columns)
            else:
                col_list = '*'

            sql_query = f"SELECT {col_list} FROM {safe_table}"
        else:
            raise ValueError("Either 'table_name' or 'query' must be provided")

        # Delegate the actual fetch + write (incl. chunking and the
        # driver-level error trapping) to the adapter, which knows its dialect.
        total_rows = self.adapter.extract_to_file(
            sql_query, output_file, delimiter=delimiter, params=params
        )

        return {
            'output_file': output_file,
            'total_rows': total_rows,
            # Single delegated extraction; the adapter manages internal
            # batching. Kept for backward-compatible result-dict shape.
            'chunks_written': 1 if total_rows else 0,
            'chunk_size': chunk_size,
            'query': sql_query,
        }

    def get_table_stats(self, table_name: str) -> Dict[str, Any]:
        """Get statistics about a table.

        Args:
            table_name: Name of the table (allow-list validated identifier).

        Returns:
            Dictionary with table statistics. ``size_mb`` is ``0.0`` on
            backends that do not expose segment-size catalog views (only
            Oracle does); the row/column facts are backend-agnostic.

        Raises:
            IdentifierValidationError: If the table name fails identifier
                validation.
        """
        # Validate the table identifier before any interpolation.
        safe_table = _validate_identifier(table_name)

        # Get row count (table name is allow-list validated above). Column
        # names are normalised so the access path is backend-independent
        # (Oracle upper-cases, SQLite preserves the alias case).
        count_query = f"SELECT COUNT(*) as row_count FROM {safe_table}"
        count_df = self.adapter.execute_query(count_query)
        count_df.columns = [str(c).upper() for c in count_df.columns]
        row_count = count_df['ROW_COUNT'].iloc[0]

        # Get column info via the adapter's catalog read.
        columns = self.adapter.get_table_columns(safe_table)

        return {
            'table_name': table_name,
            'row_count': int(row_count),
            'column_count': len(columns),
            'columns': columns,
            # Segment-size is an Oracle-only catalog concern (user_segments);
            # not portable and not on any in-scope feature path (ADR 0022 §4).
            'size_mb': 0.0,
        }

    def compare_tables(self, table1: str, table2: str, key_columns: List[str]) -> Dict[str, Any]:
        """Compare two tables and return differences.
        
        Args:
            table1: First table name
            table2: Second table name
            key_columns: Columns to use as keys for comparison
            
        Returns:
            Dictionary with comparison results
        """
        # Extract both tables
        df1 = self.extract_table(table1)
        df2 = self.extract_table(table2)

        # Use FileComparator
        from ..comparators.file_comparator import FileComparator
        comparator = FileComparator(df1, df2, key_columns)
        
        return comparator.compare()


class BulkExtractor:
    """Bulk data extraction with parallel processing support."""

    def __init__(self, adapter: DatabaseAdapter):
        """Initialize bulk extractor.

        Args:
            adapter: A connected :class:`DatabaseAdapter` instance.
        """
        self.adapter = adapter
        self.extractor = DataExtractor(adapter)

    def extract_multiple_tables(self, tables: List[str], output_dir: str,
                               delimiter: str = '|') -> Dict[str, Any]:
        """Extract multiple tables to files.
        
        Args:
            tables: List of table names
            output_dir: Output directory
            delimiter: File delimiter
            
        Returns:
            Dictionary with extraction results
        """
        import os
        
        os.makedirs(output_dir, exist_ok=True)
        
        results = {}
        total_rows = 0
        
        for table in tables:
            output_file = os.path.join(output_dir, f"{table}.txt")
            try:
                result = self.extractor.extract_to_file(
                    table, output_file, delimiter=delimiter
                )
                results[table] = {
                    'status': 'success',
                    'output_file': output_file,
                    'rows': result['total_rows'],
                }
                total_rows += result['total_rows']
            except Exception as e:
                results[table] = {
                    'status': 'failed',
                    'error': str(e),
                }

        return {
            'tables_processed': len(tables),
            'tables_succeeded': sum(1 for r in results.values() if r['status'] == 'success'),
            'tables_failed': sum(1 for r in results.values() if r['status'] == 'failed'),
            'total_rows': total_rows,
            'results': results,
        }
