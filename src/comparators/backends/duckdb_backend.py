"""DuckDB-backed comparison backend (S25-2).

This backend computes the file-to-file diff with DuckDB SQL (a vectorized,
columnar OLAP engine) while emitting the **exact same materialized result
dict** as the default
:class:`~src.comparators.backends.native_backend.NativeComparisonBackend`.  It
is a drop-in for the delimited keyed comparison that powers the DB↔file / Excel
use case, and is selected via ``COMPARISON_BACKEND=duckdb`` or
``get_comparison_backend("duckdb")``.

Design constraints
------------------
- **Lazy optional dependency.** ``import duckdb`` happens *inside* the compare
  method, never at module import time.  The module therefore imports cleanly on
  an interpreter without duckdb installed (so the default test suite runs
  duckdb-uninstalled), and selecting the backend without duckdb raises a clear,
  actionable error (``pip install duckdb``).
- **Full contract, not counts.** Unlike the S25-1 benchmark (which only
  computed counts), this backend materializes ``only_in_file1`` /
  ``only_in_file2`` (pandas DataFrames of *full rows*), ``differences`` (the
  per-row field-difference list with ``keys`` / ``differences`` / ``type`` /
  ``string_analysis`` / ``difference_count`` / ``source_row_file1`` /
  ``source_row_file2``) and ``field_statistics`` — byte-for-byte matching the
  native ``FileComparator`` output.
- **Delimited target, native-parity parsing.** To guarantee a true drop-in,
  the backend parses both files through the **exact same** native stages
  (:class:`FormatDetector` → parser → header/mapping key-resolution fallback),
  then *registers those resolved pandas DataFrames into DuckDB* and runs the
  diff in SQL over them.  Consuming the identical frames native's
  ``FileComparator`` would consume eliminates any delimiter / header / NULL
  divergence by construction.  Cases the native engine handles through
  pandas-specific parsing — fixed-width inputs, row-by-row (no keys)
  comparison, the chunked set-based path, and any input where the keys cannot
  be resolved to columns — are **cleanly deferred to the native backend**.
  Structure-incompatible inputs reproduce the native early-return dict exactly.

Semantics reproduced
---------------------
- All values are compared as **text**; the native delimited path reads with
  ``dtype=str`` / ``keep_default_na=False`` (NULL → ``''``).  Because the
  resolved frames are registered as-is, DuckDB sees the same ``''`` strings and
  the ``string_analysis`` lengths match exactly.
- A **difference** = a key present on both sides with at least one non-key
  value column unequal.  **only_in** = a key present on exactly one side.
  **matching** = rows in file1 minus only-in-file1 minus rows-with-differences
  (the native arithmetic).
- ``source_row_file1`` / ``source_row_file2`` reproduce the native value: the
  1-based position of the matched key within the inner-join result, ordered by
  file1's physical row order.

Known parity edge case (flagged for S25-3)
------------------------------------------
For **duplicate key values** the engines agree on the difference *count*, the
``keys``, the per-field ``file1`` / ``file2`` values and ``matching_rows`` — but
the ``source_row_file*`` index on the cartesian-product rows can differ, because
pandas' inner-merge enumeration order and DuckDB's join order differ for
repeated keys.  Native's per-row source index for duplicate keys is itself
ambiguous; the S25-3 parity matrix should decide the canonical behaviour.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.comparators.backends.base import ComparisonBackend
from src.comparators.backends.native_backend import (
    NativeComparisonBackend,
    _check_structure_compatibility,
)
from src.comparators.file_comparator import FileComparator
from src.parsers.fixed_width_parser import FixedWidthParser
from src.parsers.format_detector import FormatDetector

_INSTALL_HINT = (
    "DuckDB backend selected but the 'duckdb' package is not installed; "
    "pip install duckdb"
)


class DuckDBComparisonBackend(ComparisonBackend):
    """Comparison backend that computes the diff with DuckDB SQL.

    Reproduces the native in-memory result contract exactly for delimited,
    keyed comparisons and defers the non-delimited / non-keyed / chunked cases
    to :class:`NativeComparisonBackend` so parity holds across the board.
    """

    def compare(
        self,
        file1: str,
        file2: str,
        key_columns: list[str] | None,
        *,
        mapping_config: dict[str, Any] | None = None,
        detailed: bool = True,
        chunk_size: int = 100000,
        progress: bool = False,
        use_chunked: bool = False,
    ) -> dict[str, Any]:
        """Compare two delimited files with DuckDB and return the native contract.

        See :meth:`src.comparators.backends.base.ComparisonBackend.compare` for
        the full contract.  This implementation produces the in-memory shape
        identical to :class:`NativeComparisonBackend` for the keyed delimited
        case, and defers other cases (see module docstring) to native.

        Args:
            file1: Path to the first delimited file.
            file2: Path to the second delimited file.
            key_columns: Key column names for row matching.  Required — passing
                ``None`` raises ``ValueError`` (the DuckDB engine targets keyed
                comparison; row-by-row is a native concern).
            mapping_config: Optional mapping dict.  When the input is
                fixed-width the whole comparison is deferred to native.
            detailed: When True, include the field-level diff analysis
                (``type`` + ``string_analysis``) and ``field_statistics``;
                when False the diff list carries only ``file1`` / ``file2`` and
                ``field_statistics`` is ``{}`` — matching native.
            chunk_size: Forwarded to native when deferring the chunked path.
            progress: Forwarded to native when deferring the chunked path.
            use_chunked: When True the set-based path is deferred to native.

        Returns:
            The comparison result dict (native in-memory shape).

        Raises:
            ImportError: If the ``duckdb`` package is not installed.
            ValueError: If ``key_columns`` is empty/``None``.
        """
        # Lazy, actionable optional-dependency import.  Kept inside the method
        # so the module imports fine on a duckdb-less interpreter.
        try:
            import duckdb
        except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
            raise ImportError(_INSTALL_HINT) from exc

        # --- Deferral cases: hand off to native for guaranteed parity --------
        if use_chunked:
            return NativeComparisonBackend().compare(
                file1, file2, key_columns,
                mapping_config=mapping_config, detailed=detailed,
                chunk_size=chunk_size, progress=progress, use_chunked=True,
            )

        if not key_columns:
            # The native engine would fall back to positional row-by-row here;
            # the DuckDB keyed engine requires keys.  Raise the same error type.
            raise ValueError(
                "DuckDB backend requires key_columns for comparison; "
                "row-by-row (no keys) comparison is handled by the native backend."
            )

        detector = FormatDetector()
        if (
            detector.get_parser_class(file1) is FixedWidthParser
            or detector.get_parser_class(file2) is FixedWidthParser
        ):
            # Fixed-width parsing is pandas/byte-offset specific; defer to native
            # so the fixed-width contract is reproduced exactly rather than
            # re-implemented in SQL.
            return NativeComparisonBackend().compare(
                file1, file2, key_columns,
                mapping_config=mapping_config, detailed=detailed,
                chunk_size=chunk_size, progress=progress, use_chunked=False,
            )

        # --- Reproduce the native parse + structure check + key resolution ---
        # The native delimited path parses via FormatDetector/PipeDelimitedParser
        # (header=None, integer columns + ``__source_row__``), runs the structure
        # check on *those* frames, then re-reads with a header (or mapping) when
        # the requested keys are not present.  We replicate that exactly so the
        # structure-incompatible early return and the resolved DataFrames are
        # byte-identical to native; DuckDB then consumes those resolved frames.
        df1, df2 = self._native_parse(detector, file1, file2, mapping_config)

        structure_errors = _check_structure_compatibility(df1, df2, mapping_config)
        if structure_errors:
            return {
                "structure_compatible": False,
                "structure_errors": structure_errors,
                "total_rows_file1": len(df1),
                "total_rows_file2": len(df2),
                "matching_rows": 0,
                "only_in_file1": 0,
                "only_in_file2": 0,
                "differences": 0,
            }

        df1, df2, keys_resolved = self._resolve_keys(
            df1, df2, file1, file2, key_columns, mapping_config
        )
        if not keys_resolved:
            # Native has yet more pandas fallbacks (or fails the merge) when the
            # keys cannot be resolved to columns.  Defer to native to reproduce
            # whatever it does (including any error) exactly.
            return NativeComparisonBackend().compare(
                file1, file2, key_columns,
                mapping_config=mapping_config, detailed=detailed,
                chunk_size=chunk_size, progress=progress, use_chunked=False,
            )

        # Drop the internal tracking column from the value/projection set so it
        # never appears in only_in rows or diffs (native excludes it too).
        columns = [c for c in df1.columns if c != "__source_row__"]
        df1_q = df1[columns]
        df2_q = df2[columns]

        con = duckdb.connect(database=":memory:")
        try:
            # Register the *already resolved* pandas frames so DuckDB compares
            # byte-identical data to what native's FileComparator would consume.
            con.register("f1", df1_q)
            con.register("f2", df2_q)
            return self._compare_with_sql(con, columns, key_columns, detailed)
        finally:
            con.close()

    # ------------------------------------------------------------------
    # Native-parity parsing helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _native_parse(
        detector: FormatDetector,
        file1: str,
        file2: str,
        mapping_config: dict[str, Any] | None,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Parse both files exactly as the native delimited path does.

        Mirrors :meth:`NativeComparisonBackend.compare`'s first parse stage:
        the format detector picks the parser and the files are read with
        ``header=None`` (integer columns + a leading ``__source_row__``).

        Args:
            detector: Shared :class:`FormatDetector`.
            file1: First file path.
            file2: Second file path.
            mapping_config: Optional mapping (unused for delimited; kept for
                signature symmetry with the native path).

        Returns:
            Tuple of the two parsed DataFrames.
        """
        parser1 = detector.get_parser_class(file1)(file1)
        parser2 = detector.get_parser_class(file2)(file2)
        return parser1.parse(), parser2.parse()

    @staticmethod
    def _resolve_keys(
        df1: pd.DataFrame,
        df2: pd.DataFrame,
        file1: str,
        file2: str,
        key_columns: list[str],
        mapping_config: dict[str, Any] | None,
    ) -> tuple[pd.DataFrame, pd.DataFrame, bool]:
        """Resolve key columns to named columns, mirroring the native fallback.

        Reproduces the header-derived (and mapping-derived) re-read the native
        backend performs when the requested ``key_columns`` are absent from the
        integer-indexed parse.

        Args:
            df1: First parsed DataFrame (integer columns + ``__source_row__``).
            df2: Second parsed DataFrame.
            file1: First file path (for the header/mapping re-read).
            file2: Second file path.
            key_columns: Requested key column names.
            mapping_config: Optional mapping providing field names for headerless
                files.

        Returns:
            Tuple ``(df1, df2, resolved)`` where ``resolved`` is True when every
            key column is present in both returned frames.
        """
        if all(k in df1.columns for k in key_columns) and all(
            k in df2.columns for k in key_columns
        ):
            return df1, df2, True

        try:
            df1h = pd.read_csv(file1, sep="|", dtype=str, keep_default_na=False, header=0)
            df2h = pd.read_csv(file2, sep="|", dtype=str, keep_default_na=False, header=0)
            if all(k in df1h.columns for k in key_columns) and all(
                k in df2h.columns for k in key_columns
            ):
                return df1h, df2h, True
            if mapping_config and mapping_config.get("fields"):
                names = [
                    f.get("name")
                    for f in mapping_config.get("fields", [])
                    if f.get("name")
                ]
                if names:
                    df1m = pd.read_csv(
                        file1, sep="|", dtype=str, keep_default_na=False,
                        header=None, names=names,
                    )
                    df2m = pd.read_csv(
                        file2, sep="|", dtype=str, keep_default_na=False,
                        header=None, names=names,
                    )
                    if all(k in df1m.columns for k in key_columns) and all(
                        k in df2m.columns for k in key_columns
                    ):
                        return df1m, df2m, True
        except Exception:
            pass

        return df1, df2, False

    # ------------------------------------------------------------------
    # SQL-driven diff + materialization
    # ------------------------------------------------------------------
    def _compare_with_sql(
        self,
        con: Any,
        columns: list[str],
        key_columns: list[str],
        detailed: bool,
    ) -> dict[str, Any]:
        """Run the SQL diff and materialize the native-shaped result dict.

        Uses an ``INNER JOIN`` on the key columns for matched rows (carrying a
        file1 row-order sequence so ``source_row`` reproduces native), and
        anti-joins (``WHERE NOT EXISTS``) for ``only_in_file1`` /
        ``only_in_file2``.  Materialization then assembles the exact Python
        structures the native :class:`FileComparator` returns.

        Args:
            con: Open DuckDB connection with ``f1`` / ``f2`` registered (the
                native-resolved pandas frames, ``__source_row__`` already
                dropped).
            columns: Ordered column names (from file1's resolved frame).
            key_columns: Key column names.
            detailed: Include field-level analysis and ``field_statistics``.

        Returns:
            The native in-memory comparison result dict.
        """
        value_columns = [c for c in columns if c not in key_columns]

        def q(identifier: str) -> str:
            """Quote a SQL identifier (doubling embedded quotes)."""
            return '"' + identifier.replace('"', '""') + '"'

        total1 = con.execute("SELECT COUNT(*) FROM f1").fetchone()[0]
        total2 = con.execute("SELECT COUNT(*) FROM f2").fetchone()[0]

        # --- Matched rows (inner join), file1 row-order preserved ------------
        # A monotonic row sequence over file1 lets us reproduce the native
        # source_row (1-based position within the inner-join result ordered by
        # file1's physical order).  ``on_clause`` was built against the ``f1``
        # alias; the join below aliases file1 as ``f1n`` so re-derive it there.
        select_parts = [f"f1n.{q(c)} AS {q(c)}" for c in key_columns]
        for col in value_columns:
            select_parts.append(f"f1n.{q(col)} AS {q(col + '__1')}")
            select_parts.append(f"f2.{q(col)} AS {q(col + '__2')}")

        join_on = " AND ".join(f"f1n.{q(c)} = f2.{q(c)}" for c in key_columns)
        join_sql = (
            "WITH f1n AS (SELECT *, row_number() OVER () AS __rn FROM f1) "
            f"SELECT {', '.join(select_parts)} "
            f"FROM f1n JOIN f2 ON {join_on} "
            "ORDER BY f1n.__rn"
        )
        cursor = con.execute(join_sql)
        matched = cursor.fetchall()
        matched_cols = [d[0] for d in cursor.description]
        col_idx = {name: i for i, name in enumerate(matched_cols)}

        differences: list[dict[str, Any]] = []
        position = 0
        for row in matched:
            position += 1
            row_diffs: dict[str, Any] = {}
            for col in value_columns:
                v1 = row[col_idx[col + "__1"]]
                v2 = row[col_idx[col + "__2"]]
                v1 = "" if v1 is None else v1
                v2 = "" if v2 is None else v2
                if v1 != v2:
                    if detailed:
                        row_diffs[col] = self._analyze_field_difference(v1, v2)
                    else:
                        row_diffs[col] = {"file1": v1, "file2": v2}
            if row_diffs:
                key_values = {k: row[col_idx[k]] for k in key_columns}
                entry: dict[str, Any] = {
                    "keys": key_values,
                    "differences": row_diffs,
                }
                if detailed:
                    entry["difference_count"] = len(row_diffs)
                # Matching keys share row identity across both files, so the
                # native fallback yields the same positional source row for
                # both sides (idx + 1 in the merged frame).
                entry["source_row_file1"] = position
                entry["source_row_file2"] = position
                differences.append(entry)

        # --- only_in via anti-join, full rows as DataFrames ------------------
        only_in_file1 = self._fetch_only_in(con, "f1", "f2", columns, key_columns, q)
        only_in_file2 = self._fetch_only_in(con, "f2", "f1", columns, key_columns, q)

        total_differences = len(differences)
        field_stats = (
            self._calculate_field_statistics(differences) if detailed else {}
        )

        return {
            "only_in_file1": only_in_file1,
            "only_in_file2": only_in_file2,
            "differences": differences,
            "total_rows_file1": total1,
            "total_rows_file2": total2,
            "matching_rows": total1 - len(only_in_file1) - total_differences,
            "rows_with_differences": total_differences,
            "field_statistics": field_stats,
            "structure_compatible": True,
        }

    @staticmethod
    def _fetch_only_in(
        con: Any,
        source: str,
        other: str,
        columns: list[str],
        key_columns: list[str],
        q: Any,
    ) -> pd.DataFrame:
        """Return rows whose key is in *source* but not *other* as a DataFrame.

        Reproduces the native ``only_in_file*`` payload: a pandas DataFrame of
        the **full source rows** (all columns, original header order, values as
        text).

        Args:
            con: Open DuckDB connection.
            source: Table whose unique rows are returned.
            other: Reference table.
            columns: Ordered column names to project (source header order).
            key_columns: Key columns for the anti-join predicate.
            q: Identifier-quoting callable.

        Returns:
            DataFrame of the unique source rows (may be empty).
        """
        select_cols = ", ".join(f"s.{q(c)} AS {q(c)}" for c in columns)
        not_exists = " AND ".join(f"s.{q(c)} = o.{q(c)}" for c in key_columns)
        sql = (
            f"SELECT {select_cols} FROM {source} s "
            f"WHERE NOT EXISTS (SELECT 1 FROM {other} o WHERE {not_exists})"
        )
        df = con.execute(sql).fetchdf()
        # DuckDB may surface empty text cells as NaN/None; normalize to '' so
        # the materialized rows match the native dtype=str / NaN-free read.
        if not df.empty:
            df = df.fillna("")
            for c in df.columns:
                df[c] = df[c].astype(str)
        else:
            df = pd.DataFrame(columns=columns)
        return df[columns]

    # ------------------------------------------------------------------
    # Field analysis (mirrors FileComparator semantics for text values)
    # ------------------------------------------------------------------
    @staticmethod
    def _analyze_field_difference(val1: str, val2: str) -> dict[str, Any]:
        """Build the native detailed-diff dict for two text field values.

        For the delimited path every value is a string (NULL → ``''``), so the
        native ``_get_difference_type`` always yields ``"value_difference"`` and
        ``string_analysis`` is always present — reproduced here.

        Args:
            val1: File1 value (text).
            val2: File2 value (text).

        Returns:
            Dict with ``file1``, ``file2``, ``type`` and ``string_analysis``
            matching :meth:`FileComparator._analyze_field_difference`.
        """
        return {
            "file1": val1,
            "file2": val2,
            "type": "value_difference",
            "string_analysis": {
                "length_diff": len(val2) - len(val1),
                "case_only": val1.lower() == val2.lower(),
                "whitespace_diff": val1.strip() == val2.strip(),
                "length_file1": len(val1),
                "length_file2": len(val2),
            },
        }

    @staticmethod
    def _calculate_field_statistics(
        differences: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Compute the native ``field_statistics`` dict from the diff list.

        Reuses the identical logic of
        :meth:`FileComparator._calculate_field_statistics` so the
        ``fields_with_differences`` / ``field_difference_counts`` /
        ``field_difference_types`` / ``most_different_field`` keys match exactly.

        Args:
            differences: The materialized differences list.

        Returns:
            The native field-statistics dict.
        """
        # Delegate to a throwaway FileComparator instance method to guarantee
        # identical behaviour (no empty-vs-None drift between the engines).
        fc = FileComparator.__new__(FileComparator)
        return fc._calculate_field_statistics(differences)
