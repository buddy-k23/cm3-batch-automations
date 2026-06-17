"""Set-based chunked file comparator for memory-efficient large-file comparison.

Both files are streamed into a temporary SQLite database (bounded memory) with
a composite index on the key columns. Matches, mismatches and the
``only_in_file1`` / ``only_in_file2`` sets are then computed with a *bounded*
number of set-based SQL queries (one JOIN plus two ``EXCEPT`` queries plus a
handful of counts) rather than one ``SELECT`` per row. This removes the N
round-trips that previously dominated runtime at tens of millions of rows
(S18-5, #423).
"""

import os
import sqlite3
import tempfile
import time
from typing import Any, Dict, List, Optional

import pandas as pd

from ..parsers.chunked_parser import ChunkedFileParser
from ..utils.logger import get_logger
from ..utils.memory_monitor import MemoryMonitor
from ..utils.progress import ProgressTracker

# Default generous cap on the number of materialised difference / only-in
# entries returned in the result payload (S18-5, #423). The *counts* are always
# exact; this only bounds the size of the materialised lists so a pathological
# all-different comparison cannot exhaust memory. Pass ``None`` for unlimited.
DEFAULT_MAX_RESULTS: int = 100_000


class ChunkedFileComparator:
    """Compare large files using SQLite indexing and set-based SQL."""

    def __init__(self, file1_path: str, file2_path: str, key_columns: List[str],
                 delimiter: str = '|', chunk_size: int = 100000,
                 ignore_columns: Optional[List[str]] = None,
                 max_differences: Optional[int] = DEFAULT_MAX_RESULTS,
                 max_only_rows: Optional[int] = DEFAULT_MAX_RESULTS):
        """Initialize chunked comparator.

        Args:
            file1_path: Path to first file.
            file2_path: Path to second file.
            key_columns: Columns to use as unique identifiers.
            delimiter: Field delimiter.
            chunk_size: Rows per chunk when streaming into SQLite.
            ignore_columns: Columns to ignore in comparison.
            max_differences: Maximum number of difference entries to materialise
                in the result. ``total_differences_found`` always reports the
                true total and ``differences_truncated`` flags truncation.
                ``None`` means unlimited. Defaults to
                :data:`DEFAULT_MAX_RESULTS` (a generous, documented cap) — this
                replaces the previous silent hard limit of 1000.
            max_only_rows: Maximum number of ``only_in_file1`` /
                ``only_in_file2`` entries to materialise. The ``*_count`` fields
                are always exact. ``None`` means unlimited.
        """
        self.file1_path = file1_path
        self.file2_path = file2_path
        self.key_columns = key_columns
        self.delimiter = delimiter
        self.chunk_size = chunk_size
        self.ignore_columns = ignore_columns or []
        self.max_differences = max_differences
        self.max_only_rows = max_only_rows
        self.logger = get_logger(__name__)
        self.memory_monitor = MemoryMonitor()

        # Column metadata populated while indexing.
        self._value_columns: List[str] = []

        # Create temporary database.
        self.temp_db_file = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.temp_db_path = self.temp_db_file.name
        self.temp_db_file.close()
        self.db_conn = sqlite3.connect(self.temp_db_path)

        self.logger.info(f"Created temporary database: {self.temp_db_path}")

    # ------------------------------------------------------------------
    # SQL identifier helpers (parameterised values only; identifiers quoted)
    # ------------------------------------------------------------------
    @staticmethod
    def _q(identifier: str) -> str:
        """Quote a SQL identifier safely (doubling embedded quotes)."""
        return '"' + identifier.replace('"', '""') + '"'

    def _key_cols_sql(self, alias: str = "") -> str:
        """Comma-separated quoted key columns, optionally table-aliased."""
        prefix = f"{alias}." if alias else ""
        return ', '.join(f'{prefix}{self._q(c)}' for c in self.key_columns)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def compare(self, detailed: bool = True, show_progress: bool = True) -> Dict[str, Any]:
        """Compare files using set-based SQL.

        Args:
            detailed: Include detailed field-level analysis on each difference.
            show_progress: Show progress bars while indexing.

        Returns:
            Comparison results dictionary with the same schema as the
            in-memory ``FileComparator`` chunked-path contract: counts,
            ``differences``, complete ``only_in_file1`` / ``only_in_file2``
            lists, truncation flags and optional ``field_statistics``.
        """
        start_time = time.time()
        self.logger.info(f"Starting chunked comparison: {self.file1_path} vs {self.file2_path}")
        self.memory_monitor.log_memory_usage("comparison start")

        try:
            # Step 1: stream both files into SQLite with key indexes.
            self.logger.info("Step 1: Indexing file1...")
            file1_count = self._index_file('file1', self.file1_path, show_progress)
            self.memory_monitor.log_memory_usage("after indexing file1")

            self.logger.info("Step 2: Indexing file2...")
            file2_count = self._index_file('file2', self.file2_path, show_progress)
            self.memory_monitor.log_memory_usage("after indexing file2")

            # Step 3: set-based diff via a single JOIN.
            self.logger.info("Step 3: Computing matches/mismatches (JOIN)...")
            differences, total_differences, matched_keys = self._compute_differences(detailed)

            # Step 4: set-based only-in via EXCEPT.
            self.logger.info("Step 4: Computing only-in sets (EXCEPT)...")
            only_in_file1, only_in_file1_count = self._compute_only_in('file1', 'file2')
            only_in_file2, only_in_file2_count = self._compute_only_in('file2', 'file1')

            matching_rows = matched_keys - total_differences

            results = {
                'total_rows_file1': file1_count,
                'total_rows_file2': file2_count,
                'matching_rows': matching_rows,
                'only_in_file1': only_in_file1,
                'only_in_file2': only_in_file2,
                'only_in_file1_count': only_in_file1_count,
                'only_in_file2_count': only_in_file2_count,
                'rows_with_differences': total_differences,
                'differences': differences,
                'differences_truncated': (
                    self.max_differences is not None
                    and total_differences > self.max_differences
                ),
                'total_differences_found': total_differences,
            }

            if detailed and differences:
                results['field_statistics'] = self._calculate_field_statistics(differences)

            elapsed = time.time() - start_time
            self.logger.info(
                f"Comparison complete in {elapsed:.1f}s: "
                f"{file1_count:,} vs {file2_count:,} rows, "
                f"{total_differences:,} differences found"
            )
            self.memory_monitor.log_memory_usage("comparison complete")

            return results

        finally:
            self._cleanup()

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------
    def _index_file(self, table: str, file_path: str, show_progress: bool = True) -> int:
        """Stream a file into a SQLite table with an index on the key columns.

        Args:
            table: Target table name (``file1`` or ``file2``).
            file_path: Path to the source file.
            show_progress: Show a progress bar.

        Returns:
            Total rows indexed.
        """
        parser = ChunkedFileParser(file_path, self.delimiter, self.chunk_size)

        # Determine columns from a small sample.
        first_chunk = parser.parse_sample(n_rows=10)
        columns = list(first_chunk.columns)

        kept_columns = [c for c in columns if c not in self.ignore_columns]
        value_columns = [
            c for c in kept_columns if c not in self.key_columns
        ]
        # The value-column set is shared across both files (same schema).
        if not self._value_columns:
            self._value_columns = value_columns

        col_defs = ', '.join(f'{self._q(c)} TEXT' for c in kept_columns)
        self.db_conn.execute(f"CREATE TABLE {table} ({col_defs})")
        self.db_conn.execute(
            f"CREATE INDEX idx_{table}_keys ON {table} ({self._key_cols_sql()})"
        )

        total_rows = 0
        progress = ProgressTracker(parser.count_rows(), f"Indexing {table}") if show_progress else None

        for chunk in parser.parse_chunks():
            chunk_filtered = chunk[kept_columns]
            chunk_filtered.to_sql(table, self.db_conn, if_exists='append', index=False)
            total_rows += len(chunk)

            if progress:
                progress.update(total_rows)

            if total_rows % (self.chunk_size * 10) == 0:
                self.memory_monitor.force_garbage_collection()

        self.db_conn.commit()

        if progress:
            progress.finish()

        self.logger.info(f"Indexed {total_rows:,} rows into {table}")
        return total_rows

    # ------------------------------------------------------------------
    # Set-based diff (single JOIN, streamed)
    # ------------------------------------------------------------------
    def _compute_differences(self, detailed: bool) -> tuple:
        """Compute field-level differences for matched keys via one JOIN.

        A single ``INNER JOIN`` on the key columns replaces the previous
        per-row ``SELECT``. The joined rows are streamed with ``fetchmany`` so
        memory stays bounded; values are compared in Python to preserve the
        exact diff semantics of the in-memory comparator.

        Args:
            detailed: Include detailed field analysis on each diff.

        Returns:
            Tuple of ``(differences list, total_differences, matched_keys)``.
            ``differences`` is capped at ``max_differences`` entries; the other
            two values are always exact.
        """
        on_clause = ' AND '.join(
            f'f1.{self._q(c)} = f2.{self._q(c)}' for c in self.key_columns
        )
        select_cols = [f'f1.{self._q(c)} AS {self._q(c)}' for c in self.key_columns]
        for col in self._value_columns:
            select_cols.append(f'f1.{self._q(col)} AS {self._q(col + "__1")}')
            select_cols.append(f'f2.{self._q(col)} AS {self._q(col + "__2")}')

        query = (
            f"SELECT {', '.join(select_cols)} "
            f"FROM file1 f1 JOIN file2 f2 ON {on_clause}"
        )

        cursor = self.db_conn.execute(query)
        col_names = [d[0] for d in cursor.description]

        differences: List[Dict[str, Any]] = []
        total_differences = 0
        matched_keys = 0

        while True:
            rows = cursor.fetchmany(self.chunk_size)
            if not rows:
                break
            for row in rows:
                matched_keys += 1
                record = dict(zip(col_names, row))

                row_diffs: Dict[str, Any] = {}
                for col in self._value_columns:
                    val1 = record.get(f"{col}__1")
                    val1 = val1 if val1 is not None else ''
                    val2 = record.get(f"{col}__2")
                    val2 = val2 if val2 is not None else ''
                    if val1 != val2:
                        if detailed:
                            row_diffs[col] = self._analyze_field_difference(col, val1, val2)
                        else:
                            row_diffs[col] = {'file1': val1, 'file2': val2}

                if row_diffs:
                    total_differences += 1
                    if (self.max_differences is None
                            or len(differences) < self.max_differences):
                        diff_entry = {
                            'keys': {k: record[k] for k in self.key_columns},
                            'differences': row_diffs,
                        }
                        if detailed:
                            diff_entry['difference_count'] = len(row_diffs)
                        differences.append(diff_entry)

        return differences, total_differences, matched_keys

    # ------------------------------------------------------------------
    # Set-based only-in (EXCEPT)
    # ------------------------------------------------------------------
    def _compute_only_in(self, source: str, other: str) -> tuple:
        """Compute keys present in *source* but absent from *other* via EXCEPT.

        Args:
            source: Table whose unique keys are returned.
            other: Reference table.

        Returns:
            Tuple of ``(materialised list, exact count)``. The list is capped
            at ``max_only_rows`` entries; the count is always exact.
        """
        keys_sql = self._key_cols_sql()
        except_query = (
            f"SELECT {keys_sql} FROM {source} "
            f"EXCEPT SELECT {keys_sql} FROM {other}"
        )

        # Exact count via a wrapping aggregate (one query, no row materialisation).
        count_cursor = self.db_conn.execute(
            f"SELECT COUNT(*) FROM ({except_query})"
        )
        exact_count = count_cursor.fetchone()[0]

        only_in: List[Dict[str, Any]] = []
        cursor = self.db_conn.execute(except_query)
        col_names = [d[0] for d in cursor.description]
        while True:
            if self.max_only_rows is not None and len(only_in) >= self.max_only_rows:
                break
            rows = cursor.fetchmany(self.chunk_size)
            if not rows:
                break
            for row in rows:
                if self.max_only_rows is not None and len(only_in) >= self.max_only_rows:
                    break
                record = dict(zip(col_names, row))
                only_in.append({'keys': {k: record[k] for k in self.key_columns}})

        return only_in, exact_count

    # ------------------------------------------------------------------
    # Field analysis (unchanged semantics)
    # ------------------------------------------------------------------
    def _analyze_field_difference(self, field_name: str, val1: Any, val2: Any) -> Dict[str, Any]:
        """Analyze difference between two field values.

        Args:
            field_name: Name of field.
            val1: Value from file1.
            val2: Value from file2.

        Returns:
            Difference analysis dictionary.
        """
        result = {
            'file1': val1,
            'file2': val2,
            'type': self._get_difference_type(val1, val2)
        }

        if isinstance(val1, str) and isinstance(val2, str):
            result['string_analysis'] = {
                'length_diff': len(val2) - len(val1),
                'case_only': val1.lower() == val2.lower(),
                'whitespace_diff': val1.strip() == val2.strip()
            }

        try:
            num1 = float(val1)
            num2 = float(val2)
            diff = num2 - num1
            result['numeric_analysis'] = {
                'absolute_difference': diff,
                'percent_change': (diff / num1 * 100) if num1 != 0 else float('inf')
            }
        except (ValueError, TypeError):
            pass

        return result

    def _get_difference_type(self, val1: Any, val2: Any) -> str:
        """Determine type of difference.

        Args:
            val1: First value.
            val2: Second value.

        Returns:
            Difference type string.
        """
        if not val1 and not val2:
            return 'both_empty'
        elif not val1:
            return 'empty_to_value'
        elif not val2:
            return 'value_to_empty'
        else:
            return 'value_difference'

    def _calculate_field_statistics(self, differences: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calculate statistics about field differences.

        Args:
            differences: List of difference dictionaries.

        Returns:
            Field statistics dictionary.
        """
        field_counts: Dict[str, int] = {}

        for diff in differences:
            for field in diff['differences'].keys():
                field_counts[field] = field_counts.get(field, 0) + 1

        sorted_fields = sorted(field_counts.items(), key=lambda x: x[1], reverse=True)

        return {
            'fields_with_differences': len(field_counts),
            'field_difference_counts': dict(sorted_fields),
            'most_different_field': sorted_fields[0][0] if sorted_fields else None
        }

    def _cleanup(self):
        """Clean up temporary database."""
        try:
            self.db_conn.close()
            if os.path.exists(self.temp_db_path):
                os.unlink(self.temp_db_path)
                self.logger.info(f"Cleaned up temporary database: {self.temp_db_path}")
        except Exception as e:
            self.logger.warning(f"Error cleaning up temporary database: {e}")

    def __del__(self):
        """Destructor to ensure cleanup."""
        self._cleanup()
